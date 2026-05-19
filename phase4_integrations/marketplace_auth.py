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
from datetime import datetime, timezone

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
                    f"Please type the code here to continue shopping."
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


def verify_otp_and_authenticate(otp, session_id, marketplace_state):
    """
    Validates the OTP via POST /user/auth/verify-login-otp.
    On success:
      - Encrypts the returned JWT Bearer token using AES Fernet
      - Caches it in the user's chathistories session document (marketplaceAuth)
      - Resets the marketplace flow state to 'idle'
      - Calls fetch_user_profile() to cache user metadata

    Args:
        otp: The OTP code the user typed (e.g., "123456")
        session_id: Current chat session ID
        marketplace_state: The current marketplaceState dict from the session

    Returns:
        dict with "success" and "message"
    """
    phone = marketplace_state.get("phone", "")
    if not phone:
        return {
            "success": False,
            "message": "Something went wrong — I lost your phone number. Please start the login again by sharing your phone number.",
        }

    otp = otp.strip()
    if not otp:
        return {
            "success": False,
            "message": "I didn't catch the OTP. Please type the verification code you received via SMS.",
        }

    # Build the request to verify-login-otp
    endpoint = get_action_endpoint("verify_otp")
    if not endpoint:
        return {
            "success": False,
            "message": "Sorry, the marketplace service is not configured yet.",
        }

    payload = {
        "phone": phone,
        "otp": otp,
    }

    print(f"  [MARKETPLACE] Verifying OTP for {phone}")
    print(f"  [MARKETPLACE] Endpoint: {endpoint}")

    try:
        resp = requests.post(
            endpoint,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=15,
        )

        print(f"  [MARKETPLACE] Verify OTP Response: {resp.status_code}")

        if resp.status_code < 300:
            resp_data = resp.json()

            # Extract the JWT token from the response
            # The API may return it in different fields — check common patterns
            token = (
                resp_data.get("token")
                or resp_data.get("accessToken")
                or resp_data.get("data", {}).get("token")
                or resp_data.get("data", {}).get("accessToken")
            )

            if not token:
                print(f"  [MARKETPLACE] No token in response: {resp_data}")
                return {
                    "success": False,
                    "message": "Login succeeded but I couldn't retrieve your access token. Please try again.",
                }

            # Encrypt the JWT before storing
            from phase4_integrations.crypto import encrypt_credential
            encrypted_token = encrypt_credential(token)

            # Extract customerId if present
            customer_id = (
                resp_data.get("customerId")
                or resp_data.get("data", {}).get("customerId")
                or resp_data.get("userId")
                or resp_data.get("data", {}).get("userId")
            )

            # Store encrypted token + auth flag in the session document
            sessions = db["chathistories"]
            sessions.update_one(
                {"sessionId": session_id},
                {
                    "$set": {
                        "marketplaceAuth": {
                            "isAuthenticated": True,
                            "token": encrypted_token,
                            "phone": phone,
                            "customerId": customer_id,
                            "authenticatedAt": datetime.now(timezone.utc),
                        },
                        "updatedAt": datetime.now(timezone.utc),
                    }
                },
            )

            # Reset the marketplace flow state to idle
            update_marketplace_state(session_id, {
                "current_step": "idle",
                "phone": None,
            })

            print(f"  [MARKETPLACE] Authentication successful for {phone}")

            # Fetch and cache user profile in the background
            # Re-read the session to get the freshly stored token
            updated_session = sessions.find_one({"sessionId": session_id})
            profile_result = fetch_user_profile(updated_session, session_id)
            if profile_result["success"]:
                user_name = profile_result.get("profile", {}).get("name", "")
                greeting = f"Welcome back, *{user_name}*! " if user_name else "Welcome! "
            else:
                greeting = "You're all set! "

            # Check if there was a pending action before auth
            pending_action = marketplace_state.get("pending_action")
            if pending_action:
                return {
                    "success": True,
                    "message": (
                        f"{greeting}You're now logged in.\n\n"
                        f"Let me continue with your request..."
                    ),
                    "pending_action": pending_action,
                    "pending_parameters": marketplace_state.get("pending_parameters", {}),
                }

            return {
                "success": True,
                "message": (
                    f"{greeting}You're now logged in and ready to shop!\n\n"
                    "You can browse products, add items to your cart, or check your orders."
                ),
            }

        else:
            # API returned an error (wrong OTP, expired, etc.)
            try:
                error_data = resp.json()
                error_msg = error_data.get("message", resp.text[:200])
            except Exception:
                error_msg = resp.text[:200]

            print(f"  [MARKETPLACE] OTP Verification Error: {error_msg}")
            return {
                "success": False,
                "message": (
                    f"The OTP verification failed: {error_msg}\n\n"
                    "Please try again or type your phone number to request a new code."
                ),
            }

    except requests.RequestException as e:
        print(f"  [MARKETPLACE] OTP Verify Request failed: {str(e)}")
        return {
            "success": False,
            "message": "Sorry, I couldn't reach the marketplace right now. Please try again in a moment.",
        }


def fetch_user_profile(session, session_id):
    """
    Calls GET /user/auth/getUserProfile using the Bearer token from the session
    to fetch and cache user metadata (name, email, UUID) directly in the
    session's marketplaceAuth document.

    Args:
        session: The full session document from chathistories
        session_id: The session ID string

    Returns:
        dict with "success", "message", and optionally "profile"
    """
    # Get the decrypted token headers
    success, headers_or_error = get_marketplace_token(session)
    if not success:
        print(f"  [MARKETPLACE] Cannot fetch profile — not authenticated: {headers_or_error}")
        return {
            "success": False,
            "message": headers_or_error,
        }

    headers = headers_or_error

    # Build the profile endpoint URL
    config = load_marketplace_config()
    base_url = config["base_url"].rstrip("/")
    endpoint = f"{base_url}/user/auth/getUserProfile"

    print(f"  [MARKETPLACE] Fetching user profile from: {endpoint}")

    try:
        resp = requests.get(
            endpoint,
            headers=headers,
            timeout=15,
        )

        print(f"  [MARKETPLACE] Profile Response: {resp.status_code}")

        if resp.status_code < 300:
            resp_data = resp.json()

            # Extract profile data — handle nested "data" wrapper if present
            profile_data = resp_data.get("data", resp_data)

            # Normalize common fields
            user_profile = {
                "name": (
                    profile_data.get("name")
                    or profile_data.get("firstName", "")
                ),
                "email": profile_data.get("email", ""),
                "customerId": (
                    profile_data.get("customerId")
                    or profile_data.get("id")
                    or profile_data.get("uuid")
                    or ""
                ),
                "phone": profile_data.get("phone", ""),
            }

            # Cache the profile in the session's marketplaceAuth
            sessions = db["chathistories"]
            sessions.update_one(
                {"sessionId": session_id},
                {
                    "$set": {
                        "marketplaceAuth.userProfile": user_profile,
                        "marketplaceAuth.customerId": user_profile["customerId"],
                        "updatedAt": datetime.now(timezone.utc),
                    }
                },
            )

            print(f"  [MARKETPLACE] Profile cached: {user_profile.get('name')} ({user_profile.get('customerId')})")

            return {
                "success": True,
                "message": "Profile fetched successfully.",
                "profile": user_profile,
            }

        else:
            try:
                error_data = resp.json()
                error_msg = error_data.get("message", resp.text[:200])
            except Exception:
                error_msg = resp.text[:200]

            print(f"  [MARKETPLACE] Profile Error: {error_msg}")
            return {
                "success": False,
                "message": f"Could not fetch profile: {error_msg}",
            }

    except requests.RequestException as e:
        print(f"  [MARKETPLACE] Profile Request failed: {str(e)}")
        return {
            "success": False,
            "message": f"Profile request failed: {str(e)}",
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
        phone = parameters.get("phone", "")
        otp = parameters.get("otp", "")
        # Build a minimal marketplace_state for the verify function
        state = {"phone": phone}
        return verify_otp_and_authenticate(otp, session_id, state)

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
                "To help you with shopping, I need to verify your identity first.\n\n"
                "Please share your *phone number* to get started."
            ),
        }

    # ── Authenticated actions (future implementations) ──
    return {
        "success": False,
        "message": f"The '{action_id}' feature is being built. Stay tuned!",
    }
