import os
from database import perform_semantic_retrieval
from langdetect import detect
import logging

logger = logging.getLogger(__name__)

# Language code to full name mapping
LANG_MAP = {
    "en": "English", "hi": "Hindi", "es": "Spanish", "fr": "French",
    "de": "German", "pt": "Portuguese", "ja": "Japanese", "ko": "Korean",
    "zh-cn": "Chinese", "ar": "Arabic", "ru": "Russian", "it": "Italian",
    "bn": "Bengali", "ta": "Tamil", "te": "Telugu", "mr": "Marathi",
    "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam", "pa": "Punjabi",
}

def detect_query_language(query):
    """
    Detect the language of the user query using langdetect.
    Falls back to English if detection fails or is uncertain.
    """
    try:
        # Only use non-ASCII character ratio to decide script
        non_ascii = sum(1 for c in query if ord(c) > 127)
        ratio = non_ascii / max(len(query), 1)
        
        # If the query is mostly ASCII (Latin script), it's English
        if ratio < 0.3:
            return "English"
        
        code = detect(query)
        lang_name = LANG_MAP.get(code, "English")
        return lang_name
    except Exception:
        return "English"

# Words too common to signal relevance — ignored when scoring keyword overlap.
_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "have", "has", "had", "can", "could", "will",
    "would", "should", "may", "might", "must", "of", "in", "on", "at",
    "to", "for", "with", "from", "by", "about", "as", "into", "and",
    "or", "but", "if", "then", "than", "so", "it", "its", "this", "that",
    "these", "those", "there", "here", "what", "which", "who", "whom",
    "how", "when", "where", "why", "you", "your", "i", "me", "my", "we",
    "our", "they", "them", "their", "he", "she", "his", "her", "not",
    "no", "yes", "any", "all", "some", "more", "most", "other", "please",
    "tell", "give", "want", "need", "know",
}


def _tokenize(text):
    """Lowercase alphanumeric tokens, stopwords removed."""
    import re
    tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {t for t in tokens if t not in _STOPWORDS and len(t) > 2}


def rerank_chunks(query, chunks, n=4, keyword_weight=0.3):
    """
    Re-ranks vector-search candidates by blending semantic similarity with
    lexical overlap, then returns the top n.

    Pure dense retrieval reliably captures paraphrase but can miss exact
    identifiers — product codes, proper nouns, numbers — where the literal
    token matters more than the surrounding semantics. Scoring keyword overlap
    alongside the vector score recovers those cases without the latency or
    cost of a cross-encoder / LLM reranker.

    Vector scores are min-max normalized within the candidate set so the two
    signals are comparable regardless of the absolute cosine range.
    """
    if not chunks:
        return []

    query_tokens = _tokenize(query)

    vector_scores = [c.get("score", 0.0) or 0.0 for c in chunks]
    lo, hi = min(vector_scores), max(vector_scores)
    span = hi - lo

    ranked = []
    for chunk in chunks:
        raw_vector = chunk.get("score", 0.0) or 0.0
        # If every candidate scored identically, normalization is meaningless —
        # treat them as equally strong rather than dividing by zero.
        norm_vector = (raw_vector - lo) / span if span > 0 else 1.0

        if query_tokens:
            chunk_tokens = _tokenize(f"{chunk.get('topic_name', '')} {chunk.get('text', '')}")
            keyword_score = len(query_tokens & chunk_tokens) / len(query_tokens)
        else:
            keyword_score = 0.0

        combined = (1 - keyword_weight) * norm_vector + keyword_weight * keyword_score
        chunk["vector_score"] = raw_vector
        chunk["keyword_score"] = round(keyword_score, 4)
        chunk["rerank_score"] = round(combined, 4)
        ranked.append(chunk)

    ranked.sort(key=lambda c: c["rerank_score"], reverse=True)
    top = ranked[:n]
    logger.info(
        "  [RERANK] %d candidates -> top %d | best=%.4f (vec=%.4f kw=%.4f)",
        len(chunks), len(top), top[0]["rerank_score"], top[0]["vector_score"], top[0]["keyword_score"]
    )
    return top

def generate_rag_response(query, chunks, project_config, chat_history=None):

    """
    Orchestrates the final Gemini call using retrieved context and tenant rules.
    Returns the AI text, tenant config, and token usage info.
    """
   
    
    # Detect language in Python — don't rely on LLM guessing
    detected_lang = detect_query_language(query)
    logger.info(f"  [LANG] Detected query language: {detected_lang}")
    
    # Construct context string with Image IDs included
    context_text = "\n\n".join([
        f"Source: {c['topic_name']}\nImage IDs: {','.join(c.get('associated_image_ids', []))}\nContent: {c['text']}" 
        for c in chunks
    ])
    
    system_prompt = f"""
{project_config.get('projectInstruction', 'You are a helpful AI assistant.')}
GUARDRAILS:
{project_config.get('projectGuardrails', 'Be polite and accurate.')}
Generate a helpful response using ONLY the provided context. 

STRICT RULES:
1. DO NOT use your own external knowledge or provide information not found in the context.
2. If the user asks for a definition or information that is NOT in the context, do NOT provide it, even if you know it.
3. If the information is missing, simply state that you don't know based on the available information. This refusal must be in the SAME language as the user's query.
4. Your priority is to be a factual assistant based ONLY on the provided knowledge base.

CRITICAL IMAGE INSTRUCTION:
Each Source in the context may have an 'Image IDs' field containing actual database IDs (long hex strings like "69fb3f5129f312cd85c993ba").
If your answer uses information from a Source that has non-empty Image IDs, you MUST copy those EXACT ID strings and append them at the very end of your response in this format: [IMAGE: 69fb3f5129f312cd85c993ba]
Do NOT use placeholder text like "id1". Copy the ACTUAL hex string from the Image IDs field.
If the Source you used has no Image IDs, do not include this tag.

CRITICAL LANGUAGE INSTRUCTION:
The user's query language has been detected as: {detected_lang}.
You MUST write your ENTIRE response in {detected_lang}. No exceptions.
This includes any "I don't know" or refusal messages — they must also be in {detected_lang}.

"""

    user_message = f"""
CONTEXT FROM KNOWLEDGE BASE:
{context_text}

USER QUERY:
{query}
"""
    
    # Gemini takes the system prompt as a separate argument rather than a
    # message with role="system", and uses "model" where OpenAI uses "assistant".
    contents = []
    if chat_history:
        for msg in chat_history:
            role = "user" if msg["sender"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg["content"]}]})

    contents.append({"role": "user", "parts": [{"text": user_message.strip()}]})

    from llm_client import generate_text, LLMError

    try:
        response, token_info = generate_text(
            system_instruction=system_prompt.strip(),
            contents=contents,
            temperature=0.3,   # Lower temperature for factual, consistent answers
            max_output_tokens=8000,
            operation="RAG Response",
        )
    except LLMError as e:
        raise GenerationError(str(e)) from e

    ai_text = (response.text or "").strip()

    logger.info(
        f"  [TOKENS] RAG Response | {token_info['model']} | in={token_info['input_tokens']} "
        f"out={token_info['output_tokens']} total={token_info['total_tokens']} "
        f"cost=${token_info['cost_usd']:.6f}"
    )

    return ai_text, project_config, token_info


class EmbeddingGenerationError(Exception):
    """Raised when embedding generation fails after retries."""


class GenerationError(Exception):
    """Raised when the chat model fails after retries."""


def generate_text_embedding(text, task_type="RETRIEVAL_DOCUMENT"):
    """
    Convert text into an embedding vector via Gemini.

    Pass task_type="RETRIEVAL_QUERY" when embedding a user question and leave
    the default when embedding knowledge-base content — Gemini trains these as
    a matched pair, so mixing them up degrades retrieval quality silently.

    Raises EmbeddingGenerationError on failure rather than returning a zero
    vector, which would look like "nothing relevant found" instead of an outage.
    """
    from llm_client import embed, LLMError
    try:
        return embed(text, task_type=task_type)
    except LLMError as e:
        raise EmbeddingGenerationError(str(e)) from e


def generate_query_embedding(text):
    """Embed a user query using the retrieval-query task type."""
    return generate_text_embedding(text, task_type="RETRIEVAL_QUERY")
