"""
Phase 4 - Step A3: API Key Handler
Validates, encrypts, and stores API keys for Scenario B integrations.
"""
import requests
from datetime import datetime, timezone

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from database import db
from phase4_integrations.crypto import encrypt_credential
from phase4_integrations.registry import get_service


def connect_api_key(project_id, admin_id, service_id, api_key):
    """
    Validates an API key via handshake, encrypts it, and stores in the vault.
    Returns (success: bool, message: str)
    """
    # 1. Get the service config from the registry
    service = get_service(service_id)
    if not service:
        return False, f"Service '{service_id}' not found in registry."

    if service["authType"] != "api_key":
        return False, f"Service '{service_id}' does not use API Key auth."

    config = service.get("api_key_config")
    if not config:
        return False, "Missing api_key_config in registry."

    # 2. Handshake: Test if the key is valid
    header_name = config["headerName"]
    header_prefix = config.get("headerPrefix", "")
    auth_value = f"{header_prefix} {api_key}".strip()

    try:
        resp = requests.request(
            method=config.get("validationMethod", "GET"),
            url=config["validationUrl"],
            headers={header_name: auth_value},
            timeout=10
        )
    except requests.RequestException as e:
        return False, f"Handshake failed: Could not reach service. {str(e)}"

    if resp.status_code != 200:
        return False, f"Handshake failed: Service returned {resp.status_code}. Key is invalid."

    # 3. Encrypt and store
    credentials = db["project_credentials"]

    credential_doc = {
        "projectId": project_id,
        "adminId": admin_id,
        "serviceId": service_id,
        "authType": "api_key",
        "credentials": {
            "apiKeyEncrypted": encrypt_credential(api_key),
            "headerName": header_name,
            "headerPrefix": header_prefix
        },
        "status": "connected",
        "lastUsedAt": None,
        "connectedAt": datetime.now(timezone.utc),
        "updatedAt": datetime.now(timezone.utc)
    }

    # Upsert: if this project already has this service, update it
    credentials.update_one(
        {"projectId": project_id, "serviceId": service_id},
        {"$set": credential_doc},
        upsert=True
    )

    return True, f"{service['serviceName']} connected successfully."
