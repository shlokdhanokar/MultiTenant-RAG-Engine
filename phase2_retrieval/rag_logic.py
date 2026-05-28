import os
from openai import OpenAI
from database import perform_semantic_retrieval
from langdetect import detect

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

# Initialize OpenAI client
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def generate_rag_response(query, chunks, project_config, chat_history=None):

    """
    Orchestrates the final call to OpenAI GPT-4o-mini using retrieved context and tenant rules.
    Returns the AI text, tenant config, and token usage info.
    """
   
    
    # Detect language in Python — don't rely on LLM guessing
    detected_lang = detect_query_language(query)
    print(f"  [LANG] Detected query language: {detected_lang}")
    
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
    
    messages = [{"role": "system", "content": system_prompt.strip()}]
    
    # Append previous chat history
    if chat_history:
        for msg in chat_history:
            # Map database 'sender' to AI 'role'
            role = "user" if msg["sender"] == "user" else "assistant"
            messages.append({"role": role, "content": msg["content"]})
            
    # Append the current query with context
    messages.append({"role": "user", "content": user_message.strip()})
    
    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        max_tokens=500,  # Limit output to control cost
        temperature=0.3,  # Lower temperature for factual, consistent answers
    )
    
    ai_text = response.choices[0].message.content.strip()
    usage = response.usage
    
    # Log token usage and cost
    input_cost = (usage.prompt_tokens / 1_000_000) * 0.15
    output_cost = (usage.completion_tokens / 1_000_000) * 0.60
    total_cost = input_cost + output_cost
    
    token_info = {
        "model": "gpt-4o-mini",
        "input_tokens": usage.prompt_tokens,
        "output_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "cost_usd": total_cost,
    }
    
    print(f"  [TOKENS] RAG Response | Model: gpt-4o-mini")
    print(f"           Input: {usage.prompt_tokens} | Output: {usage.completion_tokens} | Total: {usage.total_tokens}")
    print(f"           Cost: ${total_cost:.6f}")

    # Log to CSV automatically
    try:
        from token_logger import log_openai_expenditure
        log_openai_expenditure("RAG Response", "gpt-4o-mini", usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)
    except Exception as e:
        print(f"Failed to log RAG tokens to CSV: {e}")
    
    return ai_text, project_config, token_info


def generate_text_embedding(text):
    """
    Convert text into a 1536-dimensional vector using OpenAI's text-embedding-3-small.
    """
    try:
        response = openai_client.embeddings.create(
            model="text-embedding-3-small",
            input=text,
        )
        try:
            usage = response.usage
            from token_logger import log_openai_expenditure
            log_openai_expenditure("Text Embedding", "text-embedding-3-small", usage.prompt_tokens, 0, usage.total_tokens)
        except Exception as log_err:
            print(f"Failed to log embedding tokens: {log_err}")
            
        return response.data[0].embedding
    except Exception as e:
        print(f"Embedding generation failed: {e}")
        return [0.0] * 1536  # Fallback zero vector
