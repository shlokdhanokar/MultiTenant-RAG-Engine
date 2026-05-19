"""
Phase 4 - Step B: OAuth Handler
Person B — OAuth Initiate, Callback, Refresh, and Active Credential retrieval.
"""
import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from database import db  
from phase4_integrations.crypto import encrypt_credential, decrypt_credential
from phase4_integrations.registry import get_service


# ── Helper: State encode/decode 
def _encode_state(data: dict) -> str:
    """Encode state as encrypted JSON string."""
    raw = json.dumps(data)
    return encrypt_credential(raw)


def _decode_state(state: str) -> dict:
    """Decode encrypted state string back to dict."""
    raw = decrypt_credential(state)
    return json.loads(raw)


# ── Step B1: OAuth Initiator 
def get_oauth_redirect_url(service_id: str, project_id: str, shop: str = None) -> tuple:
    """
    Generates the OAuth authorization URL for the given service.
    Accepts an optional 'shop' parameter for Shopify integrations.

    Returns:
        (success: bool, url_or_error: str)
    """
    service = get_service(service_id)
    if not service:
        return False, f"Service '{service_id}' not found in registry."

    if service["authType"] not in ("oauth", "shopify_oauth"):
        return False, f"Service '{service_id}' does not use OAuth."

    config = service.get("oauth_config")
    if not config:
        return False, "Missing oauth_config in registry."

    client_id = config.get("clientId", "")
    if not client_id:
        return False, f"clientId not configured for '{service_id}' in registry."

    # Build encrypted state param (include shop if it's Shopify)
    state_data = {
        "projectId": project_id,
        "serviceId": service_id,
    }
    if shop:
        state_data["shop"] = shop
        
    state = _encode_state(state_data)

    scopes = " ".join(config.get("scopes", []))
    redirect_uri = config.get("redirectUri", "")
    auth_url = config.get("authorizationUrl", "")

    # ADDED: Handle Shopify's dynamic URL
    if service["authType"] == "shopify_oauth":
        if not shop:
            return False, "Shop domain is required for Shopify integration."
        auth_url = auth_url.replace("{shop}", shop)

    url = (
        f"{auth_url}"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope={scopes}"
        f"&access_type=offline"
        f"&prompt=consent"
        f"&state={state}"
    )

    return True, url


# ── Step B2: OAuth Callback 
def handle_oauth_callback(code: str, state: str, shop: str = None) -> tuple:
    """
    Exchanges OAuth code for tokens, encrypts, and stores in vault.

    Returns:
        (success: bool, message: str)
    """
    # 1. Decrypt state → get projectId + serviceId
    try:
        state_data = _decode_state(state)
        project_id = state_data["projectId"]
        service_id = state_data["serviceId"]
        # If shopify, grab the shop from state if not provided in URL
        if not shop and "shop" in state_data:
            shop = state_data["shop"]
    except Exception as e:
        return False, f"Invalid state parameter: {str(e)}"

    # 2. Get service config
    service = get_service(service_id)
    if not service:
        return False, f"Service '{service_id}' not found."

    config = service["oauth_config"]

    # 3. Get client secret (now injected from config.py via registry)
    client_secret = config.get("clientSecret", "")

    # 4. Exchange code for tokens
    token_url = config["tokenUrl"]
    
    # ADDED: Handle Shopify's dynamic token URL
    if service["authType"] == "shopify_oauth":
        if not shop:
            return False, "Shop domain missing during Shopify callback."
        token_url = token_url.replace("{shop}", shop)
        # Note: In a production Shopify app, you MUST verify the HMAC here before proceeding.

    try:
        resp = requests.post(
            token_url,
            data={
                "code": code,
                "client_id": config["clientId"],
                "client_secret": client_secret,
                "redirect_uri": config["redirectUri"],
                "grant_type": "authorization_code",
            },
            timeout=15
        )
        resp.raise_for_status()
        token_data = resp.json()
    except requests.RequestException as e:
        return False, f"Token exchange failed: {str(e)}"

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    
    # Shopify tokens are often permanent (no expires_in)
    expires_in = token_data.get("expires_in")

    if not access_token:
        return False, f"No access_token in response: {token_data}"

    # 5. Calculate expiry time (if applicable)
    expires_at_iso = None
    if expires_in:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        expires_at_iso = expires_at.isoformat()

    # 6. Encrypt and store in vault
    credentials_col = db["project_credentials"]

    # Fetch admin_id from existing project docs or use a placeholder
    existing = credentials_col.find_one({"projectId": project_id})
    admin_id = existing["adminId"] if existing else "unknown"

    credential_doc = {
        "projectId": project_id,
        "adminId": admin_id,
        "serviceId": service_id,
        "authType": service["authType"],
        "credentials": {
            "accessTokenEncrypted": encrypt_credential(access_token),
            "refreshTokenEncrypted": encrypt_credential(refresh_token) if refresh_token else None,
            "tokenExpiresAt": expires_at_iso,
        },
        "status": "connected",
        "connectedAt": datetime.now(timezone.utc),
        "updatedAt": datetime.now(timezone.utc),
        "lastUsedAt": None,
    }
    
    if shop:
        credential_doc["shopDomain"] = shop

    credentials_col.update_one(
        {"projectId": project_id, "serviceId": service_id},
        {"$set": credential_doc},
        upsert=True
    )

    return True, f"{service['serviceName']} connected successfully for project {project_id}."


# ── Step B3: Token Refresher 
def refresh_access_token(credential_doc: dict) -> tuple:
    """
    Uses stored refresh_token to get a new access_token when expired.

    Args:
        credential_doc: A document from project_credentials collection.

    Returns:
        (success: bool, new_access_token_or_error: str)
    """
    service_id = credential_doc.get("serviceId")
    project_id = credential_doc.get("projectId")

    service = get_service(service_id)
    if not service:
        return False, f"Service '{service_id}' not found."

    config = service["oauth_config"]
    creds = credential_doc.get("credentials", {})

    refresh_token_encrypted = creds.get("refreshTokenEncrypted")
    if not refresh_token_encrypted:
        return False, "No refresh token stored. User must re-authenticate."

    try:
        refresh_token = decrypt_credential(refresh_token_encrypted)
    except Exception as e:
        return False, f"Decryption failed: {str(e)}"
        
    client_secret = config.get("clientSecret", "")

    # Exchange refresh token for new access token
    try:
        resp = requests.post(
            config["tokenUrl"],
            data={
                "refresh_token": refresh_token,
                "client_id": config["clientId"],
                "client_secret": client_secret,
                "grant_type": "refresh_token",
            },
            timeout=15
        )
        resp.raise_for_status()
        token_data = resp.json()
    except requests.RequestException as e:
        return False, f"Token refresh failed: {str(e)}"

    new_access_token = token_data.get("access_token")
    expires_in = token_data.get("expires_in", 3600)

    if not new_access_token:
        return False, f"No access_token in refresh response: {token_data}"

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    # Update in DB
    db["project_credentials"].update_one(
        {"projectId": project_id, "serviceId": service_id},
        {"$set": {
            "credentials.accessTokenEncrypted": encrypt_credential(new_access_token),
            "credentials.tokenExpiresAt": expires_at.isoformat(),
            "status": "connected",
            "updatedAt": datetime.now(timezone.utc),
        }}
    )

    return True, new_access_token


# ── Step B4: Get Active Credential
def get_active_credential(project_id: str, service_id: str) -> tuple:
    """
    Fetches credential from DB, checks expiry, refreshes if needed,
    and returns a ready-to-use decrypted access token.

    Returns:
        (success: bool, access_token_or_error: str)
    """
    credential_doc = db["project_credentials"].find_one({
        "projectId": project_id,
        "serviceId": service_id,
    })

    if not credential_doc:
        return False, f"No credential found for project '{project_id}' / service '{service_id}'."

    if credential_doc.get("status") == "disconnected":
        return False, "Integration is disconnected. Please reconnect."

    creds = credential_doc.get("credentials", {})
    expires_at_str = creds.get("tokenExpiresAt")

    # Check if token is expired (with 5 min buffer)
    # Note: If expires_at_str is None (like Shopify permanent tokens), this is skipped.
    if expires_at_str:
        expires_at = datetime.fromisoformat(expires_at_str)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        buffer = timedelta(minutes=5)
        if datetime.now(timezone.utc) + buffer >= expires_at:
            # Token expired — refresh it
            success, result = refresh_access_token(credential_doc)
            if not success:
                # Mark as expired in DB
                db["project_credentials"].update_one(
                    {"projectId": project_id, "serviceId": service_id},
                    {"$set": {"status": "expired", "updatedAt": datetime.now(timezone.utc)}}
                )
                return False, f"Token expired and refresh failed: {result}"
            return True, result

    # Token still valid — decrypt and return
    try:
        access_token = decrypt_credential(creds["accessTokenEncrypted"])
    except Exception as e:
        return False, f"Failed to decrypt access token: {str(e)}"

    # Update lastUsedAt
    db["project_credentials"].update_one(
        {"projectId": project_id, "serviceId": service_id},
        {"$set": {"lastUsedAt": datetime.now(timezone.utc)}}
    )

    return True, access_token
