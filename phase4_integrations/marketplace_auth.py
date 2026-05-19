"""
Phase 5 - Step B: Marketplace Authentication (OTP → JWT)
Handles the OTP-based login flow where:
  1. User provides phone number
  2. We call the marketplace to send an OTP via SMS
  3. User types the OTP back in chat
  4. We verify it and store the JWT token in the session

All marketplace actions flow through the existing /chat/v3 endpoint.
No new API endpoints are created.
"""
import os
import sys
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from database import db, update_marketplace_state
from phase4_integrations.marketplace_service import (
    load_marketplace_config,
    get_action_endpoint,
)


def check_marketplace_auth(session):
    """
    Checks if the current user session has a valid marketplace login.
    Returns True if authenticated, False otherwise.
    """
    marketplace_auth = session.get("marketplaceAuth")
    if not marketplace_auth:
        return False
    return marketplace_auth.get("isAuthenticated", False)


def get_marketplace_token(session):
    """
    Retrieves the marketplace JWT from the session and returns
    ready-to-use HTTP headers. Returns (success, headers_or_error).
    """
    marketplace_auth = session.get("marketplaceAuth")
    if not marketplace_auth or not marketplace_auth.get("isAuthenticated"):
        return False, "User is not authenticated with the marketplace."

    token = marketplace_auth.get("token")
    if not token:
        return False, "No marketplace token found in session."

    # Decrypt the token
    from phase4_integrations.crypto import decrypt_credential
    try:
        decrypted_token = decrypt_credential(token)
    except Exception as e:
        return False, f"Failed to decrypt marketplace token: {str(e)}"

    return True, {
        "Authorization": f"Bearer {decrypted_token}",
        "Content-Type": "application/json"
    }


def initiate_otp_login(phone, session_id):
    """
    Fires a POST to /user/auth/generate-login-otp to send an OTP SMS
    to the user's phone number.

    Args:
        phone: The user's phone number (e.g., "+917048809875" or "7048809875")
        session_id: Current chat session ID to update state

    Returns:
        dict with "success", "message", and optionally "state"
    """
    # Normalize phone number
    phone = phone.strip()
    if not phone:
        return {
            "success": False,
            "message": "I didn't catch your phone number. Could you please share it again?",
        }

    # Build the request
    endpoint = get_action_endpoint("send_otp")
    if not endpoint:
        return {
            "success": False,
            "message": "Sorry, the marketplace service is not configured yet.",
        }

    payload = {"phone": phone}

    print(f"  [MARKETPLACE] Sending OTP to {phone}")
    print(f"  [MARKETPLACE] Endpoint: {endpoint}")

    try:
        resp = requests.post(
            endpoint,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=15
        )

        print(f"  [MARKETPLACE] OTP Response: {resp.status_code}")

        if resp.status_code < 300:
            # OTP sent successfully — update session state
            update_marketplace_state(session_id, {
                "current_step": "awaiting_otp",
                "phone": phone,
            })

            return {
                "success": True,
                "message": (
                    f"I've sent a verification code (OTP) to *{phone}*. "
                    f"Please type the code here to continue shopping. 🔐"
                ),
            }
        else:
            # API returned an error
            try:
                error_data = resp.json()
                error_msg = error_data.get("message", resp.text[:200])
            except Exception:
                error_msg = resp.text[:200]

            print(f"  [MARKETPLACE] OTP Error: {error_msg}")
            return {
                "success": False,
                "message": f"Sorry, I couldn't send the OTP. The marketplace said: {error_msg}",
            }

    except requests.RequestException as e:
        print(f"  [MARKETPLACE] OTP Request failed: {str(e)}")
        return {
            "success": False,
            "message": "Sorry, I couldn't reach the marketplace right now. Please try again in a moment.",
        }


def handle_marketplace_action(session, session_id, action_id, parameters):
    """
    Central router for all marketplace actions coming from the Intent Router.
    Called by core_chat_logic() when intent["service_id"] == "marketplace".

    For auth actions (send_otp, verify_otp): executes directly.
    For all other actions: checks authentication first.
    """
    print(f"  [MARKETPLACE] Handling action: {action_id}")
    print(f"  [MARKETPLACE] Parameters: {parameters}")

    # ── Auth actions (no login required) ──
    if action_id == "send_otp":
        phone = parameters.get("phone", "")
        return initiate_otp_login(phone, session_id)

    elif action_id == "verify_otp":
        # Future: verify_otp_and_authenticate()
        return {
            "success": False,
            "message": "OTP verification is coming soon!",
        }

    # ── All other actions require authentication ──
    if not check_marketplace_auth(session):
        # User isn't logged in — start the auth flow
        update_marketplace_state(session_id, {
            "current_step": "awaiting_phone",
            "pending_action": action_id,
            "pending_parameters": parameters,
        })
        return {
            "success": False,
            "message": (
                "To help you with shopping, I need to verify your identity first. 🛒\n\n"
                "Please share your *phone number* to get started."
            ),
        }

    # ── Authenticated actions (future implementations) ──
    return {
        "success": False,
        "message": f"The '{action_id}' feature is being built. Stay tuned! 🚧",
    }
