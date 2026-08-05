from database import db, update_marketplace_state, generate_user_uuid, add_to_local_cart, get_local_cart, remove_from_local_cart, update_local_cart_quantity, get_marketplace_state, clear_local_cart
"""
Phase 5 - Step B: Marketplace Authentication (Email OTP → JWT)
Handles the OTP-based login flow where:
  1. User provides email address
  2. We call the marketplace to send an OTP via email
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


def initiate_otp_login(email, session_id):
    """
    Fires a POST to /user/auth/generate-login-otp to send an OTP
    to the user's email address.

    Args:
        email: The user's email address (e.g., "user@example.com")
        session_id: Current chat session ID to update state

    Returns:
        dict with "success", "message", and optionally "state"
    """
    # Normalize email
    email = email.strip().lower()
    if not email or "@" not in email:
        return {
            "success": False,
            "message": "That doesn't look like a valid email address. Could you please share it again?",
        }

    # Build the request
    endpoint = get_action_endpoint("send_otp")
    if not endpoint:
        return {
            "success": False,
            "message": "Sorry, the marketplace service is not configured yet.",
        }

    config = load_marketplace_config()
    domain_id = config.get("domain_id", "")
    payload = {"identifier": email, "domainId": domain_id}

    print(f"  [MARKETPLACE] Sending OTP to {email}")
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
                "email": email,
            })

            return {
                "success": True,
                "message": (
                    f"I've sent a verification code (OTP) to *{email}*. "
                    f"Please check your inbox and type the code here to continue shopping."
                ),
            }
        else:
            # API returned an error
            try:
                error_data = resp.json()
                error_msg = error_data.get("message", error_data.get("error", resp.text[:200]))
            except Exception:
                error_msg = resp.text[:200]

            print(f"  [MARKETPLACE] OTP Error: {error_msg}")
            return {
                "success": False,
                "message": f"Sorry, I couldn't send the OTP. The marketplace said: *{error_msg}*",
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
    email = marketplace_state.get("email", "")
    if not email:
        return {
            "success": False,
            "message": "Something went wrong — I lost your email address. Please start the login again by sharing your email.",
        }

    otp = otp.strip()
    if not otp:
        return {
            "success": False,
            "message": "I didn't catch the OTP. Please type the verification code you received via email.",
        }

    # Build the request to verify-login-otp
    endpoint = get_action_endpoint("verify_otp")
    if not endpoint:
        return {
            "success": False,
            "message": "Sorry, the marketplace service is not configured yet.",
        }

    config = load_marketplace_config()
    domain_id = config.get("domain_id", "")
    payload = {
        "identifier": email,
        "domainId": domain_id,
        "otp": otp,
    }

    print(f"  [MARKETPLACE] Verifying OTP for {email}")
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
                or resp_data.get("data", {}).get("user", {}).get("id")
                or resp_data.get("data", {}).get("customer", {}).get("id")
                or email  # Robust fallback to email if no ID is found in the response
            )
            new_user_id = generate_user_uuid(email)

            # Store encrypted token + auth flag in the session document and link it to the user's UUID
            sessions = db["chathistories"]
            sessions.update_one(
                {"sessionId": session_id},
                {
                    "$set": {
                        "userId": new_user_id,
                        "marketplaceAuth": {
                            "isAuthenticated": True,
                            "token": encrypted_token,
                            "email": email,
                            "customerId": customer_id,
                            "authenticatedAt": datetime.now(timezone.utc),
                        },
                        "marketplaceState": {
                            "current_step": "idle",
                            "email": None,
                        },
                        "updatedAt": datetime.now(timezone.utc),
                    }
                },
            )

            print(f"  [MARKETPLACE] Authentication successful for {email}. Linked session {session_id} to user {new_user_id}")

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
                        f"How can i help you?"
                    ),
                    "pending_action": pending_action,
                    "pending_parameters": marketplace_state.get("pending_parameters", {}),
                    "new_user_id": new_user_id,
                }

            return {
                "success": True,
                "message": (
                    f"{greeting}You're now logged in and ready to shop!\n\n"
                    "You can browse products, add items to your cart, or check your orders."
                ),
                "new_user_id": new_user_id,
            }

        else:
            # API returned an error (wrong OTP, expired, etc.)
            try:
                error_data = resp.json()
                error_msg = error_data.get("message", error_data.get("error", resp.text[:200]))
            except Exception:
                error_msg = resp.text[:200]

            print(f"  [MARKETPLACE] OTP Verification Error: {error_msg}")
            return {
                "success": False,
                "message": (
                    f"The OTP verification failed: *{error_msg}*\n\n"
                    "Please try again or type your email address to request a new code."
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



def execute_confirmed_cart_add(session, session_id, pending_cart):
    """
    Called when the user confirms "Yes" to the add-to-cart prompt.
    Saves the item to the LOCAL session-scoped cart (not the marketplace API).
    The marketplace cart API is only called during checkout.
    """
    print(f"  [MARKETPLACE] Adding to LOCAL cart: {pending_cart.get('product_name')}")

    product_name = pending_cart.get("product_name", "item")
    quantity = pending_cart.get("quantity", 1)
    price = pending_cart.get("product_price", 0.0)

    # Build local cart item
    cart_item = {
        "productId": pending_cart.get("productId"),
        "productVarientUomId": pending_cart.get("productVarientUomId"),
        "name": product_name,
        "price": price,
        "quantity": quantity,
        "storeId": pending_cart.get("storeId"),
        "customerId": pending_cart.get("customerId"),
        "customerPhone": pending_cart.get("customerPhone"),
    }

    add_to_local_cart(session_id, cart_item)

    # Reset state
    update_marketplace_state(session_id, {"current_step": "idle", "pending_cart": None})

    price_str = f" — ${price:.2f}" if price else ""
    final_message = f"✅ *Added to Cart!*\n\n"
    final_message += f"🛒 *{product_name}* x {quantity}{price_str}\n\n"

    return {
        "success": True,
        "message": final_message,
        "whatsapp_payload": {
            "type": "session_quick_reply_with_text",
            "media": {
                "header": {"text": ""},
                "body": final_message.strip(),
                "footer_text": "",
                "button": [
                    {"id": "view_cart", "title": "View Cart 🛒"},
                    {"id": "continue_shopping", "title": "Keep Shopping"},
                ]
            }
        }
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
        email = parameters.get("email", "")
        return initiate_otp_login(email, session_id)

    elif action_id == "verify_otp":
        email = parameters.get("email", "")
        otp = parameters.get("otp", "")
        # Build a minimal marketplace_state for the verify function
        state = {"email": email}
        return verify_otp_and_authenticate(otp, session_id, state)

    # ── All other actions require authentication ──
    if not check_marketplace_auth(session):
        # User isn't logged in — start the auth flow
        update_marketplace_state(session_id, {
            "current_step": "awaiting_email",
            "pending_action": action_id,
            "pending_parameters": parameters,
        })
        return {
            "success": False,
            "message": (
                "To help you with shopping, I need to verify your identity first.\n\n"
                "Please share your *email address* to get started."
            ),
        }

    # ── Authenticated actions ──
    # User is logged in. Grab their headers (with the decrypted token)
    success, headers = get_marketplace_token(session)
    if not success:
        return {
            "success": False,
            "message": "Sorry, your login session expired or is invalid. Please log in again.",
        }

    # Execute the actual marketplace action
    config = load_marketplace_config()
    
    # We must format the parameters properly and merge the action schema
    from phase4_integrations.registry import get_service
    service_def = get_service("marketplace")
    action_schema = next((a for a in service_def["availableActions"] if a["actionId"] == action_id), None)
    
    if not action_schema:
        return {
            "success": False,
            "message": f"Could not find the definition for marketplace action '{action_id}'.",
        }

    # Merge implicit parameters (like customerId, storeId) that the LLM doesn't provide
    if "storeId" in action_schema.get("parameters", []) and action_id not in ("view_cart",):
        parameters["storeId"] = config.get("default_store_id")
    if "currencyId" in action_schema.get("parameters", []):
        parameters["currencyId"] = config.get("default_currency_id")
        
    # If the action requires customerId, get it from the session
    if "customerId" in action_schema.get("parameters", []):
        session_cust_id = session.get("marketplaceAuth", {}).get("customerId")
        if not session_cust_id:
            session_cust_id = session.get("marketplaceAuth", {}).get("email")
        parameters["customerId"] = session_cust_id

    # Resolve product index to actual productId for get_product_details
    if action_id == "get_product_details":
        prod_id_param = parameters.get("productId", "")
        is_index = False
        try:
            # Check if it's a numeric index
            idx = int(str(prod_id_param).strip())
            is_index = True
        except ValueError:
            pass

        if is_index:
            marketplace_state = session.get("marketplaceState", {})
            last_products = marketplace_state.get("last_products_shown", [])
            if last_products and 0 < idx <= len(last_products):
                resolved_id = last_products[idx - 1].get("productId")
                if resolved_id:
                    parameters["productId"] = resolved_id
                    print(f"  [MARKETPLACE] Resolved index {idx} to productId {resolved_id} from cache")

    # ── Cart: add_to_cart interception ──
    # The LLM provides productId and quantity, but the marketplace API
    # also requires productVarientUomId, customerPhone, and storeId.
    if action_id == "add_to_cart":
        marketplace_state = session.get("marketplaceState", {})
        last_products = marketplace_state.get("last_products_shown", [])
        auth_data = session.get("marketplaceAuth", {})

        print(f"  [MARKETPLACE] Cart DEBUG: last_products count = {len(last_products)}")
        print(f"  [MARKETPLACE] Cart DEBUG: LLM productId = '{parameters.get('productId')}'")
        print(f"  [MARKETPLACE] Cart DEBUG: LLM productVarientUomId = '{parameters.get('productVarientUomId')}'")

        # Inject customerPhone from session auth or session userId
        if not parameters.get("customerPhone"):
            phone = auth_data.get("phone") or auth_data.get("userProfile", {}).get("phone") or session.get("userId", "")
            parameters["customerPhone"] = phone

        # Resolve productId from cache
        prod_id_param = str(parameters.get("productId", "")).strip()
        resolved_product = None
        
        if len(prod_id_param) <= 2 and prod_id_param.isdigit():
            # It's an index like "1" or "2"
            idx = int(prod_id_param)
            if last_products and 0 < idx <= len(last_products):
                resolved_product = last_products[idx - 1]
                parameters["productId"] = resolved_product["productId"]
                print(f"  [MARKETPLACE] Cart: Resolved index {idx} to productId {resolved_product['productId']}")
            else:
                print(f"  [MARKETPLACE] Cart DEBUG: Index {idx} out of range (have {len(last_products)} products)")
        else:
            # It might be a UUID or a product name
            if last_products:
                # First try exact UUID match
                resolved_product = next((p for p in last_products if str(p.get("productId")).lower() == prod_id_param.lower()), None)
                if resolved_product:
                    parameters["productId"] = resolved_product["productId"]
                    print(f"  [MARKETPLACE] Cart: Matched UUID {prod_id_param} to cached product {resolved_product.get('name')}")
                else:
                    # Fallback to name match
                    for p in last_products:
                        if p.get("name", "").lower() in prod_id_param.lower() or prod_id_param.lower() in p.get("name", "").lower():
                            resolved_product = p
                            parameters["productId"] = p["productId"]
                            print(f"  [MARKETPLACE] Cart: Resolved name '{prod_id_param}' to productId {p['productId']}")
                            break
            else:
                print(f"  [MARKETPLACE] Cart DEBUG: No cached products to search!")

        print(f"  [MARKETPLACE] Cart DEBUG: resolved_product = {resolved_product}")

        # Clean up invalid 'undefined' or 'null' generated by the LLM
        p_uom_id = parameters.get("productVarientUomId")
        if isinstance(p_uom_id, str) and p_uom_id.lower() in ("undefined", "null", "none", ""):
            parameters.pop("productVarientUomId", None)

        # Inject productVarientUomId from cache if not provided
        if not parameters.get("productVarientUomId"):
            cached_uom_id = resolved_product.get("productVarientUomId") if resolved_product else None
            
            if cached_uom_id:
                parameters["productVarientUomId"] = cached_uom_id
                print(f"  [MARKETPLACE] Cart: Injected cached productVarientUomId {cached_uom_id}")
            else:
                print(f"  [MARKETPLACE] Cart: Variant missing, starting robust listv4 fetch...")
                search_key = resolved_product.get("name", prod_id_param) if resolved_product else prod_id_param
                try:
                    search_url = f"{config.get('base_url', '').rstrip('/')}/user/product/listv4"
                    search_payload = {"storeId": config.get("default_store_id"), "searchKey": search_key, "limit": 5}
                    search_resp = requests.post(search_url, headers=headers, json=search_payload, timeout=10)
                    if search_resp.status_code < 300:
                        rows = search_resp.json().get("data", {}).get("rows", [])
                        if rows:
                            matched = rows[0]
                            real_id = matched.get("id") or matched.get("uuid") or matched.get("productId")
                            parameters["productId"] = real_id
                            resolved_product = matched
                except Exception as e:
                    print(f"  [MARKETPLACE] Cart: Failed listv4 search: {e}")
                
                # Extract variants from the resolved product (which is now the full API object)
                if resolved_product:
                    variants = resolved_product.get("varients") or resolved_product.get("variants") or resolved_product.get("productVariants") or []
                    if variants:
                        first_v = variants[0]
                        uoms = first_v.get("productVarientUoms") or first_v.get("productVariantUoms") or []
                        if uoms:
                            parameters["productVarientUomId"] = uoms[0].get("id") or uoms[0].get("productVarientUomId")
                        else:
                            parameters["productVarientUomId"] = first_v.get("productVarientUomId") or first_v.get("id")
                        print(f"  [MARKETPLACE] Cart: Successfully extracted variant {parameters.get('productVarientUomId')} from resolved product.")
                    else:
                        print(f"  [MARKETPLACE] Cart: Product genuinely has no variants in the catalog data.")

        # If productVarientUomId is still missing, return an error early
        if action_id == "add_to_cart" and not parameters.get("productVarientUomId"):
            return {
                "success": False,
                "message": "Sorry, this product is currently missing variant data in the marketplace catalog and cannot be added to the cart. Please try a different product (like product 1).",
            }

        # Default quantity to 1 if not provided
        if not parameters.get("quantity"):
            parameters["quantity"] = 1

        # ── CONFIRMATION STEP ──
        # Instead of calling the cart API right away, save the pending item
        # and ask for a Yes/No confirmation.
        product_name = "item"
        product_price = 0.0
        if resolved_product:
            # Robust name extraction
            langs = resolved_product.get("productLanguages", [])
            product_name = langs[0].get("name") if langs else (resolved_product.get("name") or resolved_product.get("title") or "item")
            
            # Robust price extraction
            price_val = resolved_product.get("price") or resolved_product.get("sellingPrice") or resolved_product.get("mrp")
            if not price_val:
                varients = resolved_product.get("variants") or resolved_product.get("varients", [])
                if varients:
                    uoms = varients[0].get("productVariantUoms") or varients[0].get("productVarientUoms")
                    if uoms:
                        price_val = uoms[0].get("inventory", {}).get("price", 0)

            try:
                product_price = float(price_val or 0)
            except (ValueError, TypeError):
                product_price = 0.0

        quantity = parameters.get("quantity", 1)

        # Save the fully-resolved parameters so we can fire the API call
        # when the user confirms.
        pending_cart = {
            "productId": parameters.get("productId"),
            "productVarientUomId": parameters.get("productVarientUomId"),
            "quantity": quantity,
            "storeId": parameters.get("storeId"),
            "customerId": parameters.get("customerId"),
            "customerPhone": parameters.get("customerPhone"),
            "product_name": product_name,
            "product_price": product_price,
        }
        update_marketplace_state(session_id, {
            "current_step": "awaiting_cart_confirmation",
            "pending_cart": pending_cart,
        })

        price_str = f" — ${product_price:.2f}" if product_price else ""
        confirm_body = (
            f"Add *{product_name}* (x{quantity}{price_str}) to your cart?"
        )

        return {
            "success": True,
            "message": confirm_body,
            "whatsapp_payload": {
                "type": "session_quick_reply_with_text",
                "media": {
                    "header": {"text": ""},
                    "body": confirm_body,
                    "footer_text": "",
                    "button": [
                        {"id": "confirm_cart_yes", "title": "Yes ✅"},
                        {"id": "confirm_cart_no", "title": "No ❌"},
                    ]
                }
            }
        }

    # ── Cart: view_cart — use LOCAL session-scoped cart ──
    if action_id == "view_cart":
        cart = get_local_cart(session_id)

        if not cart:
            return {
                "success": True,
                "message": "🛒 Your cart is empty! Search for products to start shopping.",
            }

        total = 0.0
        final_message = "🛒 *Your Shopping Cart*\n\n"
        cached_cart = []

        for idx, item in enumerate(cart, 1):
            item_name = item.get("name", "Unknown Item")
            item_price = float(item.get("price", 0))
            qty = float(item.get("quantity", 1))
            line_total = item_price * qty
            total += line_total

            final_message += f"*{idx}. {item_name}*\n"
            final_message += f"   Qty: {qty:g} | Price: ${item_price:.2f} | Subtotal: ${line_total:.2f}\n\n"

            cached_cart.append({
                "cartItemId": item.get("cartItemId"),
                "name": item_name,
                "quantity": qty,
                "price": item_price,
            })

        final_message += f"💰 *Total: ${total:.2f}*\n\n"
        final_message += "To remove an item, say *'Remove item 1'*\n"
        final_message += "To checkout, say *'Place my order'*"

        # Cache for remove_from_cart index resolution
        update_marketplace_state(session_id, {"last_cart_shown": cached_cart})

        return {
            "success": True,
            "message": final_message,
        }

    # ── Cart: remove_from_cart — use LOCAL session-scoped cart ──
    if action_id == "remove_from_cart":
        cart_id_param = parameters.get("cartId", "")
        try:
            idx = int(str(cart_id_param).strip())
        except (ValueError, TypeError):
            idx = 0

        removed = remove_from_local_cart(session_id, idx)
        if removed:
            return {
                "success": True,
                "message": f"🗑️ *Removed '{removed.get('name', 'item')}' from your cart.*\n\nSay *'View my cart'* to see your updated cart.",
            }
        else:
            return {
                "success": False,
                "message": f"Could not find item #{idx} in your cart. Say *'View my cart'* to see your items.",
            }

    # ── Cart: update_cart_quantity — use LOCAL session-scoped cart ──
    if action_id == "update_cart_quantity":
        cart_id_param = parameters.get("cartId", "")
        qty_param = parameters.get("quantity", 1)
        
        try:
            idx = int(str(cart_id_param).strip())
        except (ValueError, TypeError):
            idx = 0
            
        try:
            new_qty = float(qty_param)
        except (ValueError, TypeError):
            new_qty = 1.0

        updated = update_local_cart_quantity(session_id, idx, new_qty)
        if updated:
            if new_qty <= 0:
                msg = f"🗑️ *Removed '{updated.get('name', 'item')}' from your cart.*\n\nSay *'View my cart'* to see your updated cart."
            else:
                msg = f"✅ *Updated '{updated.get('name', 'item')}' quantity to {new_qty:g}.*\n\nSay *'View my cart'* to see your updated cart."
                
            return {
                "success": True,
                "message": msg,
            }
        else:
            return {
                "success": False,
                "message": f"Could not find item #{idx} in your cart. Say *'View my cart'* to see your items.",
            }

    # ── Orders: list_orders — strip customerId since the API rejects it ──
    if action_id == "list_orders":
        parameters.pop("customerId", None)

    if action_id == "add_delivery_address":
        if "latitude" not in parameters:
            parameters["latitude"] = 28.7041
        if "longitude" not in parameters:
            parameters["longitude"] = 77.1025
            
    if action_id == "get_delivery_slots":
        parameters["storeId"] = config.get("default_store_id")
        if "date" not in parameters:
            import datetime as _dt
            parameters["date"] = _dt.date.today().isoformat()
        parameters.setdefault("offset", 0)
        parameters.setdefault("limit", 50)

    if action_id == "calculate_delivery_charge":
        cart = get_local_cart(session_id)
        order_amount = sum(float(item.get("price", 0)) * float(item.get("quantity", 1)) for item in cart)
        parameters["orderAmount"] = order_amount
        parameters["storeId"] = config.get("default_store_id")

    if action_id == "verify_coupon":
        cart = get_local_cart(session_id)
        cart_total = sum(float(item.get("price", 0)) * float(item.get("quantity", 1)) for item in cart)
        parameters["cartTotal"] = cart_total

    if action_id == "create_order":
        import datetime
        import base64
        
        cart = get_local_cart(session_id)
        state = get_marketplace_state(session_id)
        
        # 1. Extract customerId from JWT token
        customer_id = None
        auth_header = headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            parts = token.split(".")
            if len(parts) >= 2:
                payload = parts[1]
                # Add padding if needed
                payload += "=" * ((4 - len(payload) % 4) % 4)
                try:
                    customer_id = base64.urlsafe_b64decode(payload).decode('utf-8')
                    if customer_id and customer_id.startswith('"'):
                        import json
                        try:
                            # It might be a JSON object, or just a raw string
                            parsed = json.loads(customer_id)
                            if isinstance(parsed, dict) and "id" in parsed:
                                customer_id = parsed["id"]
                            elif isinstance(parsed, str):
                                customer_id = parsed
                        except:
                            pass
                except Exception as e:
                    print(f"Error decoding JWT: {e}")
                    
        customer_phone = "+910000000000" # Fallback
        
        store_id = config.get("default_store_id")
        base_url = config.get("base_url", "").rstrip("/")
        
        # 2. Sync Local Cart to Backend Cart
        cart_add_url = f"{base_url}/user/cart/add"
        for item in cart:
            cart_payload = {
                "productId": item.get("productId"),
                "quantity": float(item.get("quantity", 1)),
                "storeId": store_id,
                "customerId": customer_id,
                "customerPhone": customer_phone,
                "productVarientUomId": item.get("productVarientUomId")
            }
            # Quietly hit cart/add to sync it for the backend
            requests.post(cart_add_url, headers=headers, json=cart_payload)
            
        # 3. Construct the massive Order payload
        cart_total = sum(float(item.get("price", 0)) * float(item.get("quantity", 1)) for item in cart)
        del_charge = state.get("deliveryCharge", 0.0)
        coupon_discount = state.get("couponDiscount", 0.0)
        final_total = cart_total + del_charge - coupon_discount
        
        parameters["totalAmount"] = final_total
        parameters["paymentType"] = parameters.get("paymentType", "COD")
        parameters["customerPhone"] = customer_phone
        parameters["customerId"] = customer_id
        parameters["returnAmount"] = 0.0
        parameters["paidAmount"] = final_total
        parameters["orderType"] = "LIVE"
        parameters["currencyId"] = config.get("currencyId", "e3d2f722-1b69-4b31-a350-0503159294e9") # Default from user payload
        parameters["checkOrderTiming"] = False
        
        # Pull address/slot from state if not provided by LLM
        parameters["customerDeliveryAddressId"] = parameters.get("customerDeliveryAddressId") or state.get("selectedAddressId")
        parameters["deliverySlotId"] = parameters.get("deliverySlotId") or state.get("selectedSlotId")
        parameters["deliverySlotName"] = parameters.get("deliverySlotName") or state.get("selectedSlotName", "")
        
        # The API returns empty timing sometimes but requires it to place an order
        timing = parameters.get("deliverySlotTiming") or state.get("selectedSlotTiming", "")
        if not timing:
            timing = "10:00 AM - 6:00 PM" # Safe fallback
        parameters["deliverySlotTiming"] = timing
        
        if "deliveryDate" not in parameters:
            # Use the date the user explicitly selected, or fall back to today
            selected_date = state.get("selectedDeliveryDate", "")
            if selected_date:
                parameters["deliveryDate"] = selected_date
            else:
                import datetime as _dt
                parameters["deliveryDate"] = _dt.date.today().isoformat()
            
        parameters["convenienceFee"] = 0.0
        parameters["deliveryCharge"] = del_charge
        parameters["handlingCharges"] = 0.0
        if state.get("appliedCoupon"):
            parameters["couponCode"] = state.get("appliedCoupon")
        elif "couponCode" in parameters:
            del parameters["couponCode"]

    # Run it!
    method = action_schema.get("method", "GET").upper()
    base_url = config.get("base_url", "").rstrip("/")
    endpoint = f"{base_url}/{action_schema.get('endpoint', '').lstrip('/')}"

    print(f"  [MARKETPLACE] Executing {action_id} at {endpoint}")
    print(f"  [MARKETPLACE] Payload: {parameters}")

    exec_result = {
        "success": False,
        "service": "Infoware Marketplace",
        "action": action_schema.get("actionName", action_id),
        "data": None,
        "error": None
    }

    try:
        if action_id == "list_wallets_and_transactions":
            print(f"  [MARKETPLACE] WALLET Fetch -> {endpoint}")
            # Make the first call for wallet balance
            resp1 = requests.get(endpoint, headers=headers, params=parameters, timeout=15)
            # Make the second call for transactions
            tx_endpoint = f"{base_url}/customer/wallets/transactions"
            print(f"  [MARKETPLACE] WALLET TX Fetch -> {tx_endpoint}")
            resp2 = requests.get(tx_endpoint, headers=headers, params=parameters, timeout=15)
            
            print(f"  [MARKETPLACE] WALLET RESPS: {resp1.status_code}, {resp2.status_code}")
            if resp1.status_code < 300 and resp2.status_code < 300:
                exec_result["success"] = True
                exec_result["data"] = {
                    "wallet": resp1.json(),
                    "transactions": resp2.json()
                }
            else:
                exec_result["error"] = "Failed to fetch wallet details."
                print(f"  [MARKETPLACE] Wallet error resp: {resp1.text[:100]} | {resp2.text[:100]}")
                
        elif method == "GET":
            resp = requests.get(endpoint, headers=headers, params=parameters, timeout=15)
        else:
            resp = requests.post(endpoint, headers=headers, json=parameters, timeout=15)
            
        if action_id != "list_wallets_and_transactions":
            print(f"  [MARKETPLACE] Response: {resp.status_code}")
            
            if resp.status_code < 300:
                json_data = resp.json()
                if isinstance(json_data, dict) and json_data.get("success") is False:
                    # HTTP 200 but logical failure
                    exec_result["error"] = json_data.get("message", json_data.get("error", "API Error"))
                else:
                    exec_result["success"] = True
                    exec_result["data"] = json_data
            else:
                try:
                    error_data = resp.json()
                    exec_result["error"] = error_data.get("message", error_data.get("error", resp.text[:200]))
                except Exception:
                    exec_result["error"] = resp.text[:200]
    except Exception as e:
        exec_result["error"] = str(e)
    
    # Format the result for the user
    final_message = None
    whatsapp_payload = None
    
    if exec_result["success"]:
        resp_data = exec_result["data"]
        
        if action_id == "search_products":
            print(f"  [MARKETPLACE] SEARCH PARAMETERS: {parameters}")
            # Extract products list from typical wraps
            products = []
            if isinstance(resp_data, list):
                products = resp_data
            elif isinstance(resp_data, dict):
                data = resp_data.get("data", resp_data)
                if isinstance(data, list):
                    products = data
                elif isinstance(data, dict):
                    products = data.get("rows", data.get("docs", data.get("products", data.get("data", []))))
            
            cached_products = []
            for p in products:
                p_id = p.get("productId") or p.get("id") or p.get("_id") or p.get("uuid")
                
                langs = p.get("productLanguages", [])
                p_name = langs[0].get("name") if langs else (p.get("name") or p.get("title") or "Unnamed Product")
                
                # Extract variant UOM ID and price from the nested structure
                varients = p.get("variants") or p.get("varients") or p.get("productVarientUoms") or p.get("productVariants") or []
                p_variant_uom_id = None
                p_company_id = None
                
                if varients:
                    first_v = varients[0]
                    uoms = first_v.get("productVariantUoms") or first_v.get("productVarientUoms")
                    if uoms:
                        uom = uoms[0]
                        p_variant_uom_id = uom.get("id") or uom.get("productVarientUomId") or uom.get("uuid")
                        inventory = uom.get("inventory", {})
                        p_price = inventory.get("price", 0.0)
                        p_company_id = inventory.get("companyId")
                    else:
                        p_variant_uom_id = first_v.get("productVarientUomId") or first_v.get("id") or first_v.get("uuid")
                        inventory = first_v.get("inventory", {})
                        p_price = inventory.get("price") or first_v.get("sellingPrice") or first_v.get("price") or 0.0
                        p_company_id = inventory.get("companyId")
                        if not p_price:
                            p_price = p.get("sellingPrice") or p.get("price") or p.get("mrp") or 0.0
                else:
                    p_price = p.get("sellingPrice") or p.get("price") or p.get("mrp") or 0.0
                cached_products.append({
                    "productId": p_id,
                    "name": p_name,
                    "price": p_price,
                    "productVarientUomId": p_variant_uom_id,
                    "companyId": p_company_id,
                })
            
            # Cache in database
            update_marketplace_state(session_id, {"last_products_shown": cached_products})
            # Update local session dictionary
            if "marketplaceState" not in session:
                session["marketplaceState"] = {}
            session["marketplaceState"]["last_products_shown"] = cached_products
            
            if not cached_products:
                final_message = "I couldn't find any products matching your search criteria. Please try another query."
            else:
                # Build interactive list payload for WhatsApp
                rows = []
                for idx, p in enumerate(cached_products, 1):
                    price_str = f"${p['price']:.2f}" if isinstance(p['price'], (int, float)) else f"${p['price']}"
                    rows.append({
                        "id": str(idx),
                        "title": str(p['name'])[:24],
                        "description": f"Price: {price_str}"[:72]
                    })

                whatsapp_payload = {
                    "type": "session_interactive_list",
                    "media": {
                        "header": {"text": ""},
                        "body": "Here are the products I found for you. Tap an item to add it to your cart!",
                        "footer_text": "",
                        "button_text": "View Products",
                        "button": [{
                            "section_title": "Products",
                            "row": rows
                        }]
                    }
                }

                # Also build a plain-text fallback
                final_message = "Here are the products I found for you:\n\n"
                for idx, p in enumerate(cached_products, 1):
                    price_str = f"${p['price']:.2f}" if isinstance(p['price'], (int, float)) else f"${p['price']}"
                    final_message += f"*{idx}. {p['name']}*\n"
                    final_message += f"   Price: {price_str}\n\n"
                final_message += "Tap an item from the list to add it to your cart!"
                
        elif action_id == "get_product_details":
            product_data = resp_data.get("data", resp_data)
            p_name = product_data.get("name") or product_data.get("title") or "Unnamed Product"
            p_desc = product_data.get("description") or "No description available."
            p_price = product_data.get("sellingPrice") or product_data.get("price")
            p_mrp = product_data.get("mrp")
            p_stock = product_data.get("stock") or product_data.get("totalStock", 0)
            
            final_message = f"🛍️ *{p_name}*\n"
            if p_price is not None:
                price_val = float(p_price) if isinstance(p_price, (int, float, str)) and str(p_price).replace('.', '', 1).replace('-', '', 1).isdigit() else 0.0
                mrp_val = float(p_mrp) if isinstance(p_mrp, (int, float, str)) and str(p_mrp).replace('.', '', 1).replace('-', '', 1).isdigit() else 0.0
                
                price_str = f"${price_val:.2f}"
                final_message += f"💵 *Price:* {price_str}"
                if mrp_val > price_val:
                    final_message += f" ~(Original: ${mrp_val:.2f})~"
                final_message += "\n"
            
            stock_num = int(p_stock) if isinstance(p_stock, (int, float, str)) and str(p_stock).isdigit() else 0
            stock_status = "In Stock" if stock_num > 0 else "Out of Stock"
            final_message += f"📦 *Availability:* {stock_status} ({stock_num} items left)\n\n"
            final_message += f"📝 *Description:*\n{p_desc}\n\n"
            
            # Parse variants
            variants = product_data.get("variants") or product_data.get("productVarientUoms") or product_data.get("productVariants") or []
            if variants:
                final_message += "✨ *Available Options (Variants):*\n"
                for v_idx, v in enumerate(variants, 1):
                    v_id = v.get("productVarientUomId") or v.get("id") or v.get("uuid")
                    v_name = v.get("uomName") or v.get("variantName") or v.get("name") or f"Option {v_idx}"
                    v_price = v.get("sellingPrice") or v.get("price") or p_price
                    v_stock = v.get("stock") or v.get("quantity", 0)
                    
                    val_price = float(v_price) if isinstance(v_price, (int, float, str)) and str(v_price).replace('.', '', 1).replace('-', '', 1).isdigit() else 0.0
                    final_message += f"• *{v_name}*: ${val_price:.2f} (Stock: {v_stock}) | ID: `{v_id}`\n"
                final_message += "\n"
                
            # Parse reviews / ratings if present
            reviews = product_data.get("reviews") or product_data.get("productRatings") or []
            avg_rating = product_data.get("averageRating") or product_data.get("rating")
            if avg_rating:
                final_message += f"⭐ *Rating:* {avg_rating}/5 based on {len(reviews)} reviews\n"
                
        elif action_id == "list_categories":
            categories_list = resp_data.get("data", resp_data)
            if not isinstance(categories_list, list):
                categories_list = categories_list.get("rows", categories_list.get("docs", categories_list.get("categories", [])))
                
            if not categories_list:
                final_message = "I couldn't find any shopping categories at the moment."
            else:
                final_message = "Explore our top shopping categories:\n\n"
                for idx, cat in enumerate(categories_list[:10], 1):
                    langs = cat.get("categoryLanguages", [])
                    cat_name = langs[0].get("name") if langs else (cat.get("name") or "Unnamed Category")
                    cat_id = cat.get("categoryId") or cat.get("id") or cat.get("uuid")
                    cat_desc = langs[0].get("description") if langs else (cat.get("description") or "Browse products in this category")
                    final_message += f"{idx}. *{cat_name}*\n"
                    final_message += f"   ID: `{cat_id}`\n"
                    final_message += f"   _{cat_desc}_\n\n"
                final_message += "Select one of the categories to start shopping!"

        elif action_id == "list_orders":
            orders = exec_result.get("data", {}).get("data", {}).get("rows", [])
            if not orders:
                final_message = "You don't have any orders yet! Type 'search' to find products."
            else:
                final_message = "📦 *Your Recent Orders*\n\n"
                for idx, order in enumerate(orders[:5], 1): # Show up to 5
                    order_no = order.get("orderNumber", "Unknown")
                    status = str(order.get("orderStatus", "N/A")).replace("_", " ").title()
                    total = order.get("totalAmount", 0)
                    date_str = order.get("orderDate", "")[:10]  # Just grab the YYYY-MM-DD
                    
                    final_message += f"*{idx}. Order {order_no}*\n"
                    final_message += f"   Status: {status} | Total: ${total:.2f} | Date: {date_str}\n"
                    
                    # Add product details
                    order_products = order.get("orderProducts", [])
                    if order_products:
                        final_message += "   Items:\n"
                        for op in order_products:
                            qty = float(op.get("quantity", 1))
                            
                            # Extract product name from orderProductDetails
                            details = op.get("orderProductDetails", [])
                            name = "Unknown Product"
                            if details and len(details) > 0:
                                name = details[0].get("name", "Unknown Product")
                                
                            final_message += f"   - {name} (x{qty:g})\n"
                            
                    final_message += "\n"

        elif action_id == "list_delivery_addresses":
            addresses = resp_data.get("data", {}).get("rows", [])
            if not addresses:
                final_message = "You don't have any saved delivery addresses yet. Tell me your address details (label, street, city, postal code) to add one!"
            else:
                # Save addresses to state for the state machine to use
                addr_options = []
                final_message = "Sure, let's checkout! Please choose your delivery address from the list below.\n\n📍 *Your Saved Addresses:*\n\n"
                for idx, addr in enumerate(addresses, 1):
                    label = addr.get("addressLabel", f"Address {idx}")
                    street = addr.get("streetAddress", "")
                    city = addr.get("city", "")
                    state = addr.get("state", "")
                    pincode = addr.get("postalCode", "")
                    addr_id = addr.get("id", "")
                    addr_options.append({"id": addr_id, "label": label, "index": idx})
                    final_message += f"*{idx}. {label}*\n"
                    final_message += f"{street}, {city}, {state} - {pincode}\n\n"
                final_message += "Reply with the *number* of the address you want to use for delivery."
                
                # Set state so the next user reply is intercepted by the state machine
                update_marketplace_state(session_id, {
                    "current_step": "awaiting_address_selection",
                    "checkout_addresses": addr_options,
                })

        elif action_id == "add_delivery_address":
            addr = resp_data.get("data", {})
            label = addr.get("addressLabel", "New Address")
            addr_id = addr.get("id", "")
            final_message = f"✅ Successfully added your delivery address *{label}*!\n"
            if addr_id:
                final_message += f"`ID: {addr_id}`\n"
            final_message += "\nYou can now use this address for checkout!"

        elif action_id == "get_delivery_slots":
            slots_data = resp_data.get("data", [])
            if isinstance(slots_data, dict):
                rows = slots_data.get("rows", [slots_data])
            elif isinstance(slots_data, list):
                rows = slots_data
            else:
                rows = []
                
            if not rows:
                final_message = "No delivery slots are currently available for that date. Please try a different date."
            else:
                slot_options = []
                final_message = "\u23F0 *Available Delivery Slots:*\n\n"
                for idx, slot in enumerate(rows, 1):
                    slot_id = slot.get("id", "")
                    name = slot.get("slotName", f"Slot {idx}")
                    start_time = slot.get("startTime", "")
                    end_time = slot.get("endTime", "")
                    # Build a human-readable timing string like "9:00 AM - 12:00 PM"
                    if start_time and end_time:
                        try:
                            from datetime import datetime as _dtparse
                            st = _dtparse.strptime(start_time, "%H:%M").strftime("%I:%M %p").lstrip("0")
                            et = _dtparse.strptime(end_time, "%H:%M").strftime("%I:%M %p").lstrip("0")
                            timing = f"{st} - {et}"
                        except Exception:
                            timing = f"{start_time} - {end_time}"
                    else:
                        timing = slot.get("slotTiming", "")
                    
                    duration = slot.get("durationMinutes", "")
                    fee = slot.get("deliveryCharge", 0)
                    slot_options.append({
                        "id": slot_id, "name": name, "timing": timing, "index": idx,
                        "startTime": start_time, "endTime": end_time,
                    })
                    final_message += f"*{idx}. {name}*\n"
                    if timing:
                        final_message += f"   \u23F0 {timing}\n"
                    if duration:
                        final_message += f"   Duration: {duration} mins\n"
                    if fee:
                        final_message += f"   Delivery Fee: \u20B9{fee}\n"
                    final_message += "\n"
                final_message += "Reply with the *number* of the slot you'd like."
                
                # Set state so the next user reply is intercepted
                update_marketplace_state(session_id, {
                    "current_step": "awaiting_slot_selection",
                    "checkout_slots": slot_options,
                })

        elif action_id == "calculate_delivery_charge":
            charge_data = resp_data.get("data", {})
            charge = charge_data.get("deliveryCharge", 0.0)
            distance = charge_data.get("distance", "Unknown")
            
            # Save it to marketplace state so create_order can use it!
            update_marketplace_state(session_id, {"deliveryCharge": charge})
            
            final_message = f"🚚 *Shipping Calculation*\n\n"
            final_message += f"Based on your selected address, the delivery charge is *${charge}*.\n\n"
            final_message += "Would you like to check for coupons or proceed directly to Place Order?\n\n"
            final_message += "Reply *coupons* or *place order*."
            
            # Set state so the next user reply is intercepted
            update_marketplace_state(session_id, {
                "current_step": "awaiting_checkout_decision",
            })

        elif action_id == "list_coupons":
            coupons = resp_data.get("data", {}).get("rows", [])
            if not coupons:
                final_message = "No coupons are currently available."
            else:
                final_message = "🎟️ *Available Coupons:*\n\n"
                for idx, coupon in enumerate(coupons, 1):
                    code = coupon.get("code", "")
                    name = coupon.get("name", "")
                    desc = coupon.get("description", "")
                    discount = coupon.get("discount", 0)
                    is_pct = coupon.get("isPercentage", False)
                    discount_str = f"{discount}%" if is_pct else f"${discount}"
                    
                    final_message += f"*{idx}. {name}* (Code: `{code}`)\n"
                    final_message += f"   Discount: {discount_str}\n"
                    final_message += f"   _{desc}_\n\n"
                final_message += "Reply with the *coupon code* you want to apply, or say *skip* to proceed without a coupon."
                
                # Save coupon codes for state machine
                coupon_codes = [c.get("code", "") for c in coupons]
                update_marketplace_state(session_id, {
                    "current_step": "awaiting_coupon_selection",
                    "checkout_coupon_codes": coupon_codes,
                })

        elif action_id == "verify_coupon":
            coupon_data = resp_data.get("data", {})
            discount_amount = coupon_data.get("discountAmount", 0.0)
            final_amount = coupon_data.get("finalAmount", 0.0)
            code = parameters.get("couponCode", "the code")
            
            # Save the applied coupon to state
            update_marketplace_state(session_id, {
                "appliedCoupon": code,
                "couponDiscount": discount_amount
            })
            
            final_message = f"✅ *Coupon Applied!*\n\n"
            final_message += f"You saved *${discount_amount}* by using `{code}`.\n"
            final_message += f"Your new cart total is *${final_amount}*.\n\n"
            final_message += "Reply *yes* to place your order, or *cancel* to abort."
            
            update_marketplace_state(session_id, {
                "current_step": "awaiting_order_confirmation",
            })

        elif action_id == "create_order":
            order_data = resp_data.get("data", {}).get("order", {})
            order_number = order_data.get("orderNumber", "Unknown")
            order_id = order_data.get("id", order_data.get("orderId", "N/A"))
            order_status = order_data.get("orderStatus", "ORDER_PLACED")
            payment_type = order_data.get("paymentType", "COD")
            
            # Clear the local cart on success!
            clear_local_cart(session_id)
            
            if payment_type in ["ONLINE", "PREPAID", "PARTIAL"]:
                payment_url = f"https://checkout.infoware.com/pay/{order_id}?session={session_id}"
                
                final_message = f"🎉 *Order Created!*\n\n"
                final_message += f"Your order number is: *{order_number}*\n"
                final_message += f"Total Amount: *${parameters.get('totalAmount', 0)}*\n\n"
                final_message += f"Please complete your secure payment using the link below:\n👉 {payment_url}\n\n"
                final_message += "Once paid, you will receive a confirmation message."
            else:
                final_message = f"🎉 *Order Placed Successfully!*\n\n"
                final_message += f"Your order number is: *{order_number}*\n"
                final_message += f"Status: {order_status}\n\n"
                final_message += "Thank you for shopping with us! You can track your order status anytime by saying 'Track my orders'."

        elif action_id == "list_wallets_and_transactions":
            wallet_data = resp_data.get("wallet", {}).get("data", {})
            balance = wallet_data.get("balance", 0.0)
            status = wallet_data.get("status", "ACTIVE")
            
            final_message = f"💳 *Digital Wallet Balance: ${balance}* ({status})\n\n"
            
            transactions = resp_data.get("transactions", {}).get("data", [])
            if not transactions:
                final_message += "You don't have any recent wallet transactions."
            else:
                final_message += "*Recent Transactions:*\n"
                for tx in transactions[:5]:
                    date_str = tx.get("createdAt", "").split("T")[0]
                    tx_type = "🟢 Added" if tx.get("type") == "CREDIT" else "🔴 Used"
                    amt = tx.get("amount", 0)
                    desc = tx.get("description", "")
                    final_message += f"• {date_str}: {tx_type} ${amt} ({desc})\n"

    if final_message is None:
        # Fallback to the generic action formatter
        from phase4_integrations.executor import format_action_result
        final_message = format_action_result(exec_result)

    result = {
        "success": exec_result["success"],
        "message": final_message,
    }
    # Attach structured WhatsApp payload if we built one
    if whatsapp_payload is not None:
        result["whatsapp_payload"] = whatsapp_payload
    return result
