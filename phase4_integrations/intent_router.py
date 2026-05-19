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

    for service in connected_services:
        for action in service["actions"]:
            # Build parameter schema from the action's parameter list
            properties = {}
            for param in action.get("parameters", []):
                properties[param] = {"type": "string", "description": f"The {param} value"}

            tool = {
                "type": "function",
                "function": {
                    "name": f"{service['serviceId']}__{action['actionId']}",
                    "description": f"{action['actionName']} via {service['serviceName']}",
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": list(properties.keys())
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
    system_prompt = f"""{project_config.get('projectInstruction', 'You are a helpful AI assistant.')}

You have access to the following external integrations. If the user's message
is clearly requesting a real-world action (like creating an event, placing an order,
sending a message), call the appropriate function.

If the user is just asking a question or having a conversation, respond normally
with a short message saying you'll answer from the knowledge base. Do NOT call
any function for simple questions."""

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
