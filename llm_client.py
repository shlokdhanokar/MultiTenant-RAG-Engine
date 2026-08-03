"""
Provider-agnostic LLM client.

Generation is pluggable via LLM_PROVIDER (groq | gemini). Embeddings are always
Gemini: Groq serves no embedding model, and mixing embedding providers would be
worse than pinning one — vectors from different models occupy different spaces,
so a knowledge base embedded by one provider is unreadable by another. Splitting
generation from embeddings this way also means a generation-side outage or quota
cap never invalidates the indexed corpus.

Callers never see provider types. Every path returns an LLMResponse exposing
.text and .function_calls, so the phase packages stay provider-neutral and a
provider swap is a config change rather than a code change.
"""
import json
import logging
import os
import time

logger = logging.getLogger(__name__)

PROVIDER = os.getenv("LLM_PROVIDER", "groq").strip().lower()

GROQ_CHAT_MODEL = os.getenv("GROQ_CHAT_MODEL", "llama-3.3-70b-versatile")
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

GEMINI_CHAT_MODEL = os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash")

# Embeddings (Gemini only, regardless of PROVIDER)
EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
# gemini-embedding-001 supports Matryoshka truncation (768 / 1536 / 3072).
# Must match the Atlas vector index's numDimensions.
EMBEDDING_DIMENSIONS = int(os.getenv("GEMINI_EMBEDDING_DIMENSIONS", "1536"))

CHAT_MODEL = GROQ_CHAT_MODEL if PROVIDER == "groq" else GEMINI_CHAT_MODEL

_gemini_client = None


class LLMError(Exception):
    """Raised when a generation or embedding call fails after retries."""


class FunctionCall:
    """A tool call, normalized across providers."""

    def __init__(self, name, args):
        self.name = name
        self.args = args or {}

    def __repr__(self):
        return f"FunctionCall(name={self.name!r}, args={self.args!r})"


class LLMResponse:
    """
    Normalized generation result.

    .text            assistant text ("" when the model only called tools)
    .function_calls  list[FunctionCall], empty when no tool was called
    """

    def __init__(self, text, function_calls=None):
        self.text = text or ""
        self.function_calls = function_calls or []


def _gemini():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise LLMError("GEMINI_API_KEY not set (required for embeddings)")
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


def _usage(model, input_tokens, output_tokens, total_tokens=None):
    from token_logger import estimate_cost
    return {
        "model": model,
        "input_tokens": input_tokens or 0,
        "output_tokens": output_tokens or 0,
        "total_tokens": total_tokens if total_tokens is not None else (input_tokens or 0) + (output_tokens or 0),
        "cost_usd": estimate_cost(model, input_tokens or 0, output_tokens or 0),
    }


# ---------------------------------------------------------------- Groq

def _groq_call(system_instruction, contents, temperature, max_output_tokens, json_mode, tools, model):
    """
    Groq speaks the OpenAI chat-completions dialect. Called over plain HTTP
    rather than through an SDK — the surface used here (messages, JSON mode,
    tools) is small and stable, and `requests` is already a dependency.
    """
    import requests

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise LLMError("GROQ_API_KEY not set")

    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.extend(_to_openai_messages(contents))

    payload = {
        "model": model or GROQ_CHAT_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if max_output_tokens:
        payload["max_tokens"] = max_output_tokens
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if tools:
        payload["tools"] = [
            {"type": "function", "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("parameters") or {"type": "object", "properties": {}},
            }}
            for t in tools
        ]
        payload["tool_choice"] = "auto"

    resp = requests.post(
        f"{GROQ_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    if not resp.ok:
        raise LLMError(f"Groq {resp.status_code}: {resp.text[:300]}")

    body = resp.json()
    choice = body["choices"][0]["message"]

    calls = []
    for tc in choice.get("tool_calls") or []:
        raw = tc.get("function", {}).get("arguments") or "{}"
        try:
            args = json.loads(raw)
        except json.JSONDecodeError:
            # A malformed argument blob is a failed tool call, not a failed
            # request — drop it so the caller falls through to its non-tool path.
            logger.error(f"Groq returned unparseable tool arguments: {raw[:200]}")
            continue
        calls.append(FunctionCall(tc["function"]["name"], args))

    u = body.get("usage") or {}
    return (
        LLMResponse(choice.get("content"), calls),
        _usage(payload["model"], u.get("prompt_tokens"), u.get("completion_tokens"), u.get("total_tokens")),
    )


def _to_openai_messages(contents):
    """
    Normalize the caller's `contents` into OpenAI-style messages.

    Accepts a bare string, or the Gemini-shaped list the phase packages already
    build ({"role": "user"|"model", "parts": [{"text": ...}]}), so callers don't
    need provider-specific branches.
    """
    if contents is None:
        return []
    if isinstance(contents, str):
        return [{"role": "user", "content": contents}]

    out = []
    for item in contents:
        if isinstance(item, str):
            out.append({"role": "user", "content": item})
            continue
        role = item.get("role", "user")
        parts = item.get("parts") or []
        text = " ".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
        if not text:
            text = item.get("content", "") or ""
        out.append({"role": "assistant" if role == "model" else role, "content": text})
    return out


# ---------------------------------------------------------------- Gemini

def _gemini_call(system_instruction, contents, temperature, max_output_tokens,
                 json_mode, tools, model, thinking_budget):
    from google.genai import types

    cfg = {"temperature": temperature}
    if system_instruction:
        cfg["system_instruction"] = system_instruction
    if max_output_tokens:
        cfg["max_output_tokens"] = max_output_tokens
    if json_mode:
        cfg["response_mime_type"] = "application/json"
    if tools:
        cfg["tools"] = [types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name=t["name"],
                description=t.get("description", ""),
                parameters=t.get("parameters") or None,
            )
            for t in tools
        ])]
    if thinking_budget is not None:
        # Gemini 2.5 spends thinking tokens against max_output_tokens, so an
        # unbounded budget on a tightly-capped call can consume the entire
        # allowance and return empty text. Every task here is shallow enough
        # not to need it.
        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=thinking_budget)

    raw = _gemini().models.generate_content(
        model=model or GEMINI_CHAT_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(**cfg),
    )

    calls = [FunctionCall(c.name, dict(c.args) if c.args else {}) for c in (raw.function_calls or [])]
    meta = getattr(raw, "usage_metadata", None)
    return (
        LLMResponse(raw.text if not calls else (raw.text or ""), calls),
        _usage(
            model or GEMINI_CHAT_MODEL,
            getattr(meta, "prompt_token_count", 0),
            getattr(meta, "candidates_token_count", 0),
            getattr(meta, "total_token_count", None),
        ),
    )


# ---------------------------------------------------------------- public API

def generate_text(
    system_instruction=None,
    contents=None,
    temperature=0.3,
    max_output_tokens=None,
    json_mode=False,
    tools=None,
    model=None,
    operation="LLM Call",
    thinking_budget=0,
    provider=None,
):
    """
    Single entry point for generation. Returns (LLMResponse, token_info).

    `tools` is a list of provider-neutral dicts:
        {"name": str, "description": str, "parameters": <JSON Schema object>}

    Retries once on failure, then raises LLMError.
    """
    active = (provider or PROVIDER).lower()
    last_error = None

    for attempt in range(2):
        try:
            if active == "groq":
                response, token_info = _groq_call(
                    system_instruction, contents, temperature,
                    max_output_tokens, json_mode, tools, model,
                )
            else:
                response, token_info = _gemini_call(
                    system_instruction, contents, temperature,
                    max_output_tokens, json_mode, tools, model, thinking_budget,
                )
            _log_usage(operation, token_info["model"], token_info)
            return response, token_info
        except Exception as e:
            last_error = e
            logger.error(f"{operation} failed on {active} (attempt {attempt + 1}/2): {e}")
            if attempt == 0:
                time.sleep(1)

    raise LLMError(f"{operation} failed after retries: {last_error}")


def embed(text, task_type="RETRIEVAL_DOCUMENT", operation="Text Embedding"):
    """
    Embed text via Gemini, regardless of the configured generation provider.

    task_type must be RETRIEVAL_DOCUMENT when indexing content and
    RETRIEVAL_QUERY when embedding a user question — Gemini trains these as a
    matched pair, and mixing them up degrades retrieval silently rather than
    raising.

    Raises rather than returning a zero vector, which would rank as "nothing
    relevant found" instead of surfacing the outage.
    """
    from google.genai import types

    last_error = None
    for attempt in range(2):
        try:
            response = _gemini().models.embed_content(
                model=EMBEDDING_MODEL,
                contents=text,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=EMBEDDING_DIMENSIONS,
                ),
            )
            values = response.embeddings[0].values

            # Truncated MRL vectors are no longer unit-length; Atlas cosine
            # similarity assumes normalized input, so renormalize here.
            if EMBEDDING_DIMENSIONS != 3072:
                norm = sum(v * v for v in values) ** 0.5
                if norm > 0:
                    values = [v / norm for v in values]
            return values
        except Exception as e:
            last_error = e
            logger.error(f"Embedding failed (attempt {attempt + 1}/2): {e}")
            if attempt == 0:
                time.sleep(1)

    raise LLMError(f"Embedding failed after retries: {last_error}")


def _log_usage(operation, model, token_info):
    try:
        from token_logger import log_openai_expenditure
        log_openai_expenditure(
            operation, model,
            token_info["input_tokens"], token_info["output_tokens"], token_info["total_tokens"],
        )
    except Exception as e:
        logger.error(f"Failed to log token usage: {e}")
