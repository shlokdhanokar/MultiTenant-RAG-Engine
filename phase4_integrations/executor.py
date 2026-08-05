"""
Phase 4 - Step C2: Action Executor
The AI's "Hands" — takes the intent router's decision, grabs the decrypted
token from the vault, and fires the actual HTTP request to the external API.
"""
import os
import sys
import json
import requests
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from database import db
from phase4_integrations.registry import get_service
from phase4_integrations.oauth_handler import get_active_credential
from phase4_integrations.crypto import decrypt_credential


def _get_token_and_headers(project_id, service_id):
    """
    Retrieves the decrypted token for a project+service and builds
    the correct Authorization header based on the service's auth type.

    Returns:
        (success: bool, headers_or_error: dict|str)
    """
    service = get_service(service_id)
    if not service:
        return False, f"Service '{service_id}' not found."

    auth_type = service.get("authType")

    if auth_type in ("oauth", "shopify_oauth"):
        # OAuth tokens — use get_active_credential (handles refresh)
        success, token_or_error = get_active_credential(project_id, service_id)
        if not success:
            return False, token_or_error

        return True, {
            "Authorization": f"Bearer {token_or_error}",
            "Content-Type": "application/json"
        }

    elif auth_type == "api_key":
        # API Key tokens — pull from vault manually
        cred_doc = db["project_credentials"].find_one({
            "projectId": project_id,
            "serviceId": service_id,
            "status": "connected"
        })
        if not cred_doc:
            return False, f"No connected credential for '{service_id}'."

        creds = cred_doc.get("credentials", {})
        try:
            api_key = decrypt_credential(creds["apiKeyEncrypted"])
        except Exception as e:
            return False, f"Failed to decrypt API key: {str(e)}"

        header_name = creds.get("headerName", "Authorization")
        header_prefix = creds.get("headerPrefix", "Bearer")
        auth_value = f"{header_prefix} {api_key}".strip()

        # Update lastUsedAt
        db["project_credentials"].update_one(
            {"projectId": project_id, "serviceId": service_id},
            {"$set": {"lastUsedAt": datetime.now(timezone.utc)}}
        )

        return True, {
            header_name: auth_value,
            "Content-Type": "application/json"
        }

    else:
        return False, f"Unsupported auth type: {auth_type}"


def _find_action_config(service_id, action_id):
    """
    Looks up the specific action definition (method, endpoint, etc.)
    from the service registry.
    """
    service = get_service(service_id)
    if not service:
        return None

    for action in service.get("availableActions", []):
        if action["actionId"] == action_id:
            return action

    return None


def execute_action(project_id, service_id, action_id, parameters):
    """
    The main execution function. It:
      1. Finds the action config (HTTP method, endpoint URL)
      2. Grabs the decrypted token from the vault
      3. Fires the HTTP request to the external API
      4. Returns a structured result

    Args:
        project_id: The tenant's project ID
        service_id: e.g. "google_calendar", "shopify"
        action_id:  e.g. "create_event", "get_products"
        parameters: Dict of params the LLM extracted from the user's message

    Returns:
        {
            "success": True/False,
            "service": "Google Calendar",
            "action": "Create Calendar Event",
            "data": <API response dict> or None,
            "error": <error string> or None
        }
    """
    # 1. Find the action definition
    action_config = _find_action_config(service_id, action_id)
    if not action_config:
        return {
            "success": False,
            "service": service_id,
            "action": action_id,
            "data": None,
            "error": f"Action '{action_id}' not found in '{service_id}' registry."
        }

    service = get_service(service_id)
    service_name = service["serviceName"] if service else service_id
    action_name = action_config.get("actionName", action_id)

    logger.info(f"  [EXEC] Executing: {service_name} -> {action_name}")
    logger.info(f"  [EXEC] Parameters: {json.dumps(parameters)}")

    # 2. Get the authorization headers
    success, headers_or_error = _get_token_and_headers(project_id, service_id)
    if not success:
        logger.error(f"  [EXEC] Auth failed: {headers_or_error}")
        return {
            "success": False,
            "service": service_name,
            "action": action_name,
            "data": None,
            "error": headers_or_error
        }

    headers = headers_or_error

    # 3. Prepare the request
    method = action_config.get("method", "GET").upper()
    endpoint = action_config.get("endpoint", "")

    # 4. Fire the request
    try:
        if method == "GET":
            resp = requests.get(endpoint, headers=headers, params=parameters, timeout=15)
        elif method == "POST":
            resp = requests.post(endpoint, headers=headers, json=parameters, timeout=15)
        elif method == "PUT":
            resp = requests.put(endpoint, headers=headers, json=parameters, timeout=15)
        elif method == "DELETE":
            resp = requests.delete(endpoint, headers=headers, params=parameters, timeout=15)
        else:
            return {
                "success": False,
                "service": service_name,
                "action": action_name,
                "data": None,
                "error": f"Unsupported HTTP method: {method}"
            }

        # 5. Parse the response
        try:
            response_data = resp.json()
        except Exception:
            response_data = {"raw": resp.text[:500]}

        if resp.status_code < 300:
            logger.info(f"  [EXEC] Success! Status: {resp.status_code}")
            return {
                "success": True,
                "service": service_name,
                "action": action_name,
                "data": response_data,
                "error": None
            }
        else:
            logger.error(f"  [EXEC] API returned error: {resp.status_code}")
            return {
                "success": False,
                "service": service_name,
                "action": action_name,
                "data": response_data,
                "error": f"API returned status {resp.status_code}"
            }

    except requests.RequestException as e:
        logger.error(f"  [EXEC] Request failed: {str(e)}")
        return {
            "success": False,
            "service": service_name,
            "action": action_name,
            "data": None,
            "error": f"Request failed: {str(e)}"
        }


def format_action_result(result):
    """
    Takes the raw execution result and converts it into a human-friendly
    string that can be passed back into the chat as the AI's response.
    """
    if result["success"]:
        return (
            f"Done! I successfully completed '{result['action']}' "
            f"via {result['service']}."
        )
    else:
        return (
            f"Sorry, I tried to run '{result['action']}' via {result['service']} "
            f"but it failed: {result['error']}"
        )
