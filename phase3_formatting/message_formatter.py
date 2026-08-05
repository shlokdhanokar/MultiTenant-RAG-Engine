import json
import logging

from llm_client import generate_text

logger = logging.getLogger(__name__)

FORMAT_AGENT_SYSTEM_PROMPT = """You are a WhatsApp Message Formatting Agent for a RAG chatbot.
Choose the BEST session message type and format the response.

## Available Types (use ONLY these for RAG responses):

### 1. session_text
For simple text answers.
Return: {"chosen_type": "session_text", "text": "Formatted response"}

### 2. session_text_with_preview_url
When a URL is in the answer.
Return: {"chosen_type": "session_text_with_preview_url", "text": "Check: https://...", "preview_url": true}

### 3. session_image
When image is available AND relevant to query.
Return: {"chosen_type": "session_image", "media": {"type": "image", "url": "IMAGE_URL", "file": "", "caption": "Caption"}}

### 4. session_location
When a physical address with coordinates is mentioned.
Return: {"chosen_type": "session_location", "media": {"type": "location", "longitude": 0.0, "latitude": 0.0, "name": "Name", "address": "Address"}}

### 5. session_interactive_list
When presenting 3+ distinct categorized items.
Return: {"chosen_type": "session_interactive_list", "media": {"header": {"text": "Header"}, "body": "Body", "footer_text": "", "button_text": "Select", "button": [{"section_title": "Section", "row": [{"id": "1", "title": "Title", "description": "Desc"}]}]}}

### 6. session_quick_reply_with_text
When follow-up action buttons are useful (no image).
Return: {"chosen_type": "session_quick_reply_with_text", "media": {"header": {"text": ""}, "body": "Body", "footer_text": "", "button": [{"id": "1", "title": "Button"}]}}

### 7. session_quick_reply_with_media
When image + follow-up buttons are both useful.
Return: {"chosen_type": "session_quick_reply_with_media", "media": {"header": {"type": "image", "url": "IMAGE_URL", "file": "", "caption": ""}, "body": "Body", "footer_text": "", "button": [{"id": "1", "title": "Button"}]}}

## Decision Rules:
1. Query asks for visuals + images available -> session_image or session_quick_reply_with_media
2. 3+ distinct categorizable items -> session_interactive_list
3. Answer has clear follow-up actions -> add buttons (session_quick_reply_with_text or _with_media)
4. URL in answer -> session_text_with_preview_url
5. Physical address -> session_location
6. Simple factual answer -> session_text
7. Do NOT add buttons to every response. Only when genuine follow-up exists.

## Button Rules:
- Max 3 buttons, each title max 20 chars
- Must be actionable and related to the answer
- Do NOT use generic text like "Learn More" or "OK"

## List Rules:
- Item titles: max 24 chars, descriptions: max 72 chars

## Formatting:
- Use *bold*, _italic_ for WhatsApp
- Replace IMAGE_URL with actual URL from context
- Return ONLY valid JSON"""

TEMPLATE_AGENT_SYSTEM_PROMPT = """You are a WhatsApp Template Selection Agent.
When a session is EXPIRED (>24h), choose the best pre-approved template and fill it with RAG content.

## Your Job:
1. Analyze the RAG response text.
2. Select the BEST template from the available list.
3. Return the FULL template JSON with body.text updated to reflect the RAG response.

Return ONLY the full template JSON object."""


def format_rag_to_session_message(rag_text, image_urls, buttons, user_info, query=None, tag_ids=None, is_expired=False, templates=None):
    """
    Agentic AI: LLM selects optimal WhatsApp session type, generates contextual
    buttons, and formats content. Python validates WhatsApp constraints.
    """
    chosen_type = "session_text"
    type_data = {}

    if is_expired and templates:
        # --- EXPIRED SESSION: TEMPLATE LOGIC ---
        template_message = f"""RAG RESPONSE TEXT: {rag_text}
AVAILABLE TEMPLATES: {json.dumps(templates)}
USER NAME: {user_info.get('name', 'User')}
Fill the best template with the RAG information."""
        try:
            resp, _usage = generate_text(
                system_instruction=TEMPLATE_AGENT_SYSTEM_PROMPT,
                contents=template_message,
                temperature=0.2,
                json_mode=True,
                max_output_tokens=2000,
                operation="Template Selection Agent",
            )
            result = json.loads(resp.text)
            chosen_type = result.get("template_type", "template")
            type_data = result
        except Exception as e:
            logger.error(f"  [FORMATTER] Template Error: {e}")
            chosen_type = "session_text"
            type_data = {}
    else:
        # --- ACTIVE SESSION: LLM DECIDES TYPE ---
        query_ctx = f"USER QUERY: {query}\n" if query else ""
        image_ctx = f"IMAGE_URLS: {json.dumps(image_urls)}" if image_urls else "IMAGE_URLS: None"

        user_message = f"""{query_ctx}RAG RESPONSE TEXT:
{rag_text}

{image_ctx}

Choose the best WhatsApp session type and format the response."""
        try:
            resp, _usage = generate_text(
                system_instruction=FORMAT_AGENT_SYSTEM_PROMPT,
                contents=user_message.strip(),
                temperature=0.2,
                json_mode=True,
                max_output_tokens=2000,
                operation="WhatsApp Formatting Agent",
            )
            llm_output = json.loads(resp.text)
        except Exception as e:
            logger.error(f"  [FORMATTER] LLM Error: {e}")
            llm_output = {"chosen_type": "session_text", "text": rag_text}

        chosen_type = llm_output.get("chosen_type", "session_text")
        type_data = llm_output

    # --- VALIDATE & BUILD PAYLOAD ---
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": user_info.get("phone", ""),
        "type": chosen_type,
        "name": user_info.get("name", ""),
        "phone": user_info.get("phone", ""),
        "enable_acculync": True,
        "tagIds": tag_ids or []
    }

    # Types that use direct "text" field
    if chosen_type == "session_text":
        payload["text"] = type_data.get("text", rag_text)

    elif chosen_type == "session_text_with_preview_url":
        payload["text"] = type_data.get("text", rag_text)
        payload["preview_url"] = True

    # Types that use "media" field
    elif chosen_type == "session_image":
        media = type_data.get("media", {})
        url = media.get("url", "")
        if (not url or url == "IMAGE_URL") and image_urls:
            url = image_urls[0]
        payload["media"] = {
            "type": "image",
            "url": url,
            "file": media.get("file", ""),
            "caption": media.get("caption", rag_text)
        }

    elif chosen_type == "session_location":
        media = type_data.get("media", {})
        payload["media"] = {
            "type": "location",
            "longitude": media.get("longitude", 0.0),
            "latitude": media.get("latitude", 0.0),
            "name": media.get("name", ""),
            "address": media.get("address", "")
        }

    elif chosen_type == "session_interactive_list":
        media = type_data.get("media", {})
        sections = media.get("button", [])
        validated = []
        for sec in sections:
            rows = []
            for row in sec.get("row", []):
                rows.append({
                    "id": str(row.get("id", len(rows)+1)),
                    "title": str(row.get("title", ""))[:24],
                    "description": str(row.get("description", ""))[:72]
                })
            if rows:
                validated.append({"section_title": str(sec.get("section_title", ""))[:24], "row": rows})
        if validated:
            header = media.get("header", {})
            if isinstance(header, str):
                header = {"text": header}
            payload["media"] = {
                "header": header,
                "body": media.get("body", rag_text),
                "footer_text": media.get("footer_text", ""),
                "button_text": str(media.get("button_text", "View Options"))[:20],
                "button": validated
            }
        else:
            payload["type"] = "session_text"
            payload["text"] = rag_text

    elif chosen_type == "session_quick_reply_with_text":
        media = type_data.get("media", {})
        valid_btns = _validate_buttons(media.get("button", []))
        if valid_btns:
            header = media.get("header", {})
            if isinstance(header, str):
                header = {"text": header}
            payload["media"] = {
                "header": header,
                "body": media.get("body", rag_text),
                "footer_text": media.get("footer_text", ""),
                "button": valid_btns
            }
        else:
            payload["type"] = "session_text"
            payload["text"] = rag_text

    elif chosen_type == "session_quick_reply_with_media":
        media = type_data.get("media", {})
        valid_btns = _validate_buttons(media.get("button", []))
        if valid_btns:
            header = media.get("header", {})
            if isinstance(header, dict) and header.get("type") == "image":
                url = header.get("url", "")
                if (not url or url == "IMAGE_URL") and image_urls:
                    header["url"] = image_urls[0]
            elif image_urls:
                header = {"type": "image", "url": image_urls[0], "file": "", "caption": ""}
            payload["media"] = {
                "header": header,
                "body": media.get("body", rag_text),
                "footer_text": media.get("footer_text", ""),
                "button": valid_btns
            }
        else:
            payload["type"] = "session_text"
            payload["text"] = rag_text

    else:
        # Fallback for unknown/template types
        payload["type"] = chosen_type
        if "text" in type_data:
            payload["text"] = type_data["text"]
        elif "media" in type_data:
            payload["media"] = type_data["media"]
        else:
            payload["text"] = rag_text

    logger.info(f"  [FORMATTER] Type: {payload['type']}")
    return payload, {}


def _validate_buttons(raw_buttons):
    """Validates button array: max 3 buttons, max 20 chars each."""
    valid = []
    for btn in raw_buttons[:3]:
        title = str(btn.get("title", ""))[:20].strip()
        if title:
            valid.append({"id": btn.get("id", f"btn_{len(valid)+1}"), "title": title})
    return valid
