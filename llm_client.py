"""
Central LLM client (Google Gemini).

Every model call in the app goes through this module so provider details —
model names, embedding dimensions, retry policy, token accounting — live in
one place instead of being duplicated across the phase packages.

Embeddings use asymmetric task types: documents are embedded with
RETRIEVAL_DOCUMENT and queries with RETRIEVAL_QUERY. Gemini trains these as a
matched pair, so a query vector lands closer to passages that *answer* it
rather than passages that merely *resemble* it — a retrieval-quality gain that
symmetric embedding models can't express.
"""
import logging
import os
import time

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

CHAT_MODEL = os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash")
EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")

# gemini-embedding-001 supports Matryoshka truncation (768 / 1536 / 3072).
# 1536 keeps parity with the existing Atlas vector index while retaining
# substantially more signal than 768.
EMBEDDING_DIMENSIONS = int(os.getenv("GEMINI_EMBEDDING_DIMENSIONS", "1536"))

_client = None


def get_client():
    """Lazily construct the shared Gemini client."""
    global _client
    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set in environment")
        _client = genai.Client(api_key=api_key)
    return _client


class LLMError(Exception):
    """Raised when a Gemini call fails after retries."""


def _usage(response, model):
    """Normalize Gemini usage metadata into the app's token_info shape."""
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return {"model": model, "input_tokens": 0, "output_tokens": 0,
                "total_tokens": 0, "cost_usd": 0.0}

    input_tokens = usage.prompt_token_count or 0
    output_tokens = usage.candidates_token_count or 0

    from token_logger import estimate_cost
    return {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": usage.total_token_count or 0,
        "cost_usd": estimate_cost(model, input_tokens, output_tokens),
    }


def generate_text(
    system_instruction=None,
    contents=None,
    temperature=0.3,
    max_output_tokens=None,
    json_mode=False,
    tools=None,
    model=None,
    operation="Gemini Call",
    thinking_budget=0,
):
    """
    Single entry point for text generation.

    Returns (response, token_info). The raw response is returned alongside the
    token counts because callers need different things from it — plain text,
    parsed JSON, or function calls.

    thinking_budget defaults to 0. Gemini 2.5 models spend "thinking" tokens
    before emitting output, and those count against max_output_tokens — so an
    unbounded budget on a tightly-capped call can burn the entire allowance and
    return empty text. Every task here (grounded extraction, JSON formatting,
    intent routing) is shallow enough not to need it; raise it deliberately per
    call if a task ever does.
    """
    client = get_client()
    config_kwargs = {"temperature": temperature}
    if system_instruction:
        config_kwargs["system_instruction"] = system_instruction
    if max_output_tokens:
        config_kwargs["max_output_tokens"] = max_output_tokens
    if json_mode:
        config_kwargs["response_mime_type"] = "application/json"
    if tools:
        config_kwargs["tools"] = tools
    if thinking_budget is not None:
        config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=thinking_budget)

    last_error = None
    for attempt in range(2):
        try:
            response = client.models.generate_content(
                model=model or CHAT_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(**config_kwargs),
            )
            token_info = _usage(response, model or CHAT_MODEL)
            _log_usage(operation, model or CHAT_MODEL, token_info)
            return response, token_info
        except Exception as e:
            last_error = e
            logger.error(f"{operation} failed (attempt {attempt + 1}/2): {e}")
            if attempt == 0:
                time.sleep(1)

    raise LLMError(f"{operation} failed after retries: {last_error}")


def embed(text, task_type="RETRIEVAL_DOCUMENT", operation="Text Embedding"):
    """
    Embed a single piece of text.

    task_type must be RETRIEVAL_DOCUMENT when indexing content and
    RETRIEVAL_QUERY when embedding a user question — mixing them up silently
    degrades retrieval rather than raising, so callers should be explicit.

    Retries once, then raises. A zero-vector fallback would look like "nothing
    relevant found" instead of surfacing the outage.
    """
    client = get_client()

    last_error = None
    for attempt in range(2):
        try:
            response = client.models.embed_content(
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

            _log_usage(operation, EMBEDDING_MODEL, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
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
