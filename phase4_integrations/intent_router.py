"""
Phase 4 - Step C1: Intent Router
The AI's "Brain" — uses OpenAI Function Calling to decide whether the user
wants a standard RAG answer OR wants to trigger a real-world action.
"""
import os
import sys
import json

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
    Converts the connected services and their actions into OpenAI-compatible
    Function Calling tool definitions.
    
    Each action becomes one "function" that the LLM can call.
    """
    tools = []

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
                # Infer type for known numeric fields
                if param in ("limit", "offset", "quantity", "totalAmount", "paidAmount", "deliveryCharge"):
                    param_type = "number"
                else:
                    param_type = "string"
                properties[param] = {"type": param_type, "description": f"The {param} value"}

            required_params = [
                p for p in action.get("parameters", [])
                if p not in NOT_REQUIRED_PARAMS
            ]

            tool = {
                "type": "function",
                "function": {
                    "name": f"{service['serviceId']}__{action['actionId']}",
                    "description": f"{action['actionName']} via {service['serviceName']}",
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required_params
                    }
                }
            }
            tools.append(tool)

    return tools


def route_intent(query, project_id, project_config, chat_history=None):
    """
    The main routing function. Sends the user query to OpenAI with Function Calling
    enabled. The LLM decides:
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
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # 1. Check what services this project has connected
    connected_services = get_connected_services(project_id)

    # If no integrations are connected, skip routing — go straight to RAG
    if not connected_services:
        return {"type": "rag"}

    # 2. Build tool definitions for OpenAI
    tools = build_tool_definitions(connected_services)

    # 3. Build the system prompt
    system_prompt = """You are an intent routing assistant. Your ONLY job is to decide whether to call a function/tool or route to RAG.

CRITICAL INSTRUCTION: If the user's message is requesting any e-commerce or shopping action that maps to one of the available integration tools (e.g. searching products, viewing the cart, "show my cart", adding items to the cart, placing an order, etc.), you MUST call the appropriate function. Do NOT answer these with normal text.

Only route to RAG (by returning normal text) if the user is asking a general knowledge question that does NOT relate to any available integration tool."""

    messages = [{"role": "system", "content": system_prompt}]

    # Add recent chat history for context
    if chat_history:
        for msg in chat_history[-6:]:
            role = "user" if msg.get("role") == "user" else "assistant"
            messages.append({"role": role, "content": msg.get("content", "")})

    messages.append({"role": "user", "content": query})

    # 4. Call OpenAI with function calling
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.1,
            max_tokens=300
        )
        try:
            usage = response.usage
            from token_logger import log_openai_expenditure
            log_openai_expenditure("Intent Routing", "gpt-4o-mini", usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)
        except Exception as log_err:
            print(f"Failed to log intent routing tokens: {log_err}")
    except Exception as e:
        print(f"  [INTENT] OpenAI call failed: {e}")
        return {"type": "rag"}

    choice = response.choices[0]

    # 5. Check if the LLM decided to call a function
    if choice.message.tool_calls:
        tool_call = choice.message.tool_calls[0]
        full_name = tool_call.function.name
        raw_args = tool_call.function.arguments

        # Parse "google_calendar__create_event" -> service_id, action_id
        parts = full_name.split("__", 1)
        if len(parts) != 2:
            print(f"  [INTENT] Malformed function name: {full_name}")
            return {"type": "rag"}

        service_id, action_id = parts

        try:
            parameters = json.loads(raw_args)
        except json.JSONDecodeError:
            parameters = {}

        print(f"  [INTENT] Action detected: {service_id} -> {action_id}")
        print(f"  [INTENT] Parameters: {parameters}")

        return {
            "type": "action",
            "service_id": service_id,
            "action_id": action_id,
            "parameters": parameters
        }

    # 6. No function call — it's a normal question, use RAG
    print("  [INTENT] No action detected, routing to RAG.")
    return {"type": "rag"}
