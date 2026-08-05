"""
Phase 4 - Step C1: Intent Router
The AI's "Brain" — uses OpenAI Function Calling to decide whether the user
wants a standard RAG answer OR wants to trigger a real-world action.
"""
import os
import sys
import json
import logging

logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from database import db
from phase4_integrations.registry import get_service


def get_connected_services(project_id):
    """
    Fetch all integrations that are actively connected for this project.
    Returns a list of service definitions with their available actions.
    """
    connected = list(db["project_credentials"].find(
        {"projectId": project_id, "status": "connected"},
        {"_id": 0, "serviceId": 1}
    ))

    services = []
    for cred in connected:
        service = get_service(cred["serviceId"])
        if service and service.get("availableActions"):
            services.append({
                "serviceId": service["serviceId"],
                "serviceName": service["serviceName"],
                "actions": service["availableActions"]
            })

    return services


def build_tool_definitions(connected_services):
    """
    Converts the connected services and their actions into provider-neutral
    tool definitions, which llm_client translates for whichever LLM is active.

    Each action becomes one function the model can call. Names are namespaced
    as "<serviceId>__<actionId>" so the flat function namespace these APIs
    expose can be mapped back to a specific service on the way out.
    """
    declarations = []

    # Internal/automatic parameters that the user shouldn't be required to provide in chat.
    NOT_REQUIRED_PARAMS = {
        "customerId", "storeId", "customerPhone", "currencyId", 
        "limit", "offset", "categoryId", "totalAmount", 
        "paidAmount", "deliverySlotId", "deliverySlotTiming", 
        "deliverySlotName", "deliveryCharge", "customerDeliveryAddressId",
        "productVarientUomId"
    }

    for service in connected_services:
        for action in service["actions"]:
            # Build parameter schema from the action's parameter list
            properties = {}
            for param in action.get("parameters", []):
                if param in ("limit", "offset", "quantity", "totalAmount", "paidAmount", "deliveryCharge"):
                    param_type = "number"
                else:
                    param_type = "string"

                desc = f"The {param} value."
                if param == "customerDeliveryAddressId":
                    desc = "The ID of the delivery address the user selected (found in previous messages)."
                elif param == "deliverySlotId":
                    desc = "The ID of the delivery slot the user selected (found in previous messages)."
                elif param == "searchKey":
                    desc = "The item the user is searching for, translated to English singular."
                    
                properties[param] = {"type": param_type, "description": desc}

            required_params = [
                p for p in action.get("parameters", [])
                if p not in NOT_REQUIRED_PARAMS
            ]

            declarations.append({
                "name": f"{service['serviceId']}__{action['actionId']}",
                "description": f"{action['actionName']} via {service['serviceName']}. {action.get('description', '')}",
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required_params,
                } if properties else None,
            })

    return declarations


def route_intent(query, project_id, project_config, chat_history=None):
    """
    The main routing function. Sends the user query to Gemini with function
    calling enabled. The model decides:
      - If it's a simple question -> returns a normal text answer (use RAG).
      - If it's an action request   -> returns a function_call with parameters.

    Returns:
        {
            "type": "rag" | "action",
            "content": <str>           (if type == "rag", this is the text answer),
            "service_id": <str>        (if type == "action"),
            "action_id": <str>         (if type == "action"),
            "parameters": <dict>       (if type == "action"),
        }
    """
    from llm_client import generate_text

    # 1. Check what services this project has connected
    connected_services = get_connected_services(project_id)

    # If no integrations are connected, skip routing — go straight to RAG
    if not connected_services:
        return {"type": "rag"}

    # 2. Build tool definitions
    declarations = build_tool_definitions(connected_services)
    if not declarations:
        return {"type": "rag"}

    # 3. Build the system prompt
    service_instructions = "\n".join([f"- {s['serviceName']}: {s.get('description', '')}" for s in connected_services])

    system_prompt = f"""You are an intent routing assistant. Your ONLY job is to decide whether to call a function/tool or route to RAG.

SERVICE CONTEXT AND SOPs:
{service_instructions}

CRITICAL INSTRUCTION: If the user's message is requesting any e-commerce or shopping action that maps to one of the available integration tools (e.g. searching products, viewing the cart, "show my cart", adding items to the cart, placing an order, etc.), you MUST call the appropriate function. Do NOT answer these with normal text.

CRITICAL PARAMETER TRANSLATION: If the user's query is in a non-English language or written in Romanized scripts (e.g. Hinglish "mujhe narangi chahiye"), you MUST translate the actual search terms into English before passing them as function arguments. For example, if they search for "narangi" or "seb", you must pass {{"searchKey": "orange"}} or {{"searchKey": "apple"}}. 

Only route to RAG (by returning normal text) if the user is asking a general knowledge question that does NOT relate to any available integration tool."""

    contents = []

    # Add recent chat history for context
    if chat_history:
        for msg in chat_history[-6:]:
            role = "user" if msg.get("role") == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg.get("content", "")}]})

    contents.append({"role": "user", "parts": [{"text": query}]})

    # 4. Call the configured LLM with function calling
    try:
        response, _usage = generate_text(
            system_instruction=system_prompt,
            contents=contents,
            tools=declarations,
            temperature=0.1,
            max_output_tokens=1000,
            operation="Intent Routing",
        )
    except Exception as e:
        logger.error(f"  [INTENT] Routing call failed: {e}")
        return {"type": "rag"}

    # 5. Check if the model decided to call a function
    function_calls = response.function_calls or []
    if function_calls:
        call = function_calls[0]
        full_name = call.name

        # Parse "google_calendar__create_event" -> service_id, action_id
        parts = full_name.split("__", 1)
        if len(parts) != 2:
            logger.info(f"  [INTENT] Malformed function name: {full_name}")
            return {"type": "rag"}

        service_id, action_id = parts
        parameters = dict(call.args) if call.args else {}

        logger.info(f"  [INTENT] Action detected: {service_id} -> {action_id}")
        logger.info(f"  [INTENT] Parameters: {parameters}")

        return {
            "type": "action",
            "service_id": service_id,
            "action_id": action_id,
            "parameters": parameters
        }

    # 6. No function call — it's a normal question, use RAG
    logger.info("  [INTENT] No action detected, routing to RAG.")
    return {"type": "rag"}
