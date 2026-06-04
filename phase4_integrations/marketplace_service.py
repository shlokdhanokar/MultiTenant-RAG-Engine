"""
Phase 5 - Step A: Marketplace Service Definition & Registration
Defines the complete Marketplace service (all API actions), loads config from
environment, and converts actions into OpenAI Function Calling tool definitions.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from database import db


# ── Config Loader ──────────────────────────────────────────────────────────

def load_marketplace_config():
    """
    Reads Marketplace-specific environment variables and returns a config dict.
    All marketplace functions pull settings from here instead of hardcoding.
    """
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

    return {
        "base_url": os.getenv("MARKETPLACE_BASE_URL", "https://marketplaceapp.infoware.xyz/api/"),
        "domain_id": os.getenv("MARKETPLACE_DOMAIN_ID", ""),
        "default_store_id": os.getenv("MARKETPLACE_DEFAULT_STORE_ID", ""),
        "default_currency_id": os.getenv("MARKETPLACE_DEFAULT_CURRENCY_ID", ""),
    }


# ── Full Service Definition ───────────────────────────────────────────────

MARKETPLACE_SERVICE = {
    "serviceId": "marketplace",
    "serviceName": "Infoware Marketplace",
    "description": "Multi-vendor marketplace shopping assistant with OTP-based user auth",
    "authType": "marketplace_jwt",
    "isActive": True,

    # Actions exposed to the LLM via OpenAI Function Calling.
    # Only user-facing actions go here. Internal helpers (address lookup,
    # delivery calculation) are called programmatically by the checkout
    # orchestrator, not by the LLM.
    "availableActions": [

        # ── Authentication ──
        {
            "actionId": "send_otp",
            "actionName": "Send Login OTP",
            "description": "Send an OTP to the user's email address to authenticate them for shopping.",
            "method": "POST",
            "endpoint": "user/auth/generate-login-otp",
            "parameters": ["email"],
            "requiresAuth": False,
        },
        {
            "actionId": "verify_otp",
            "actionName": "Verify Login OTP",
            "description": "Verify the OTP code the user typed to complete login.",
            "method": "POST",
            "endpoint": "user/auth/verify-login-otp",
            "parameters": ["email", "otp"],
            "requiresAuth": False,
        },

        # ── Product Discovery ──
        {
            "actionId": "search_products",
            "actionName": "Search Products",
            "description": "Search or browse products in the marketplace catalog. IMPORTANT: Always translate the searchKey to English and normalize to a singular base word (e.g., 'orange' instead of 'oranges', 'apple' instead of 'apples') to ensure the database query works.",
            "method": "POST",
            "endpoint": "user/product/listv4",
            "parameters": ["limit", "offset", "categoryId", "storeId", "searchKey"],
            "requiresAuth": True,
        },
        {
            "actionId": "get_product_details",
            "actionName": "View Product Details",
            "description": "Get full details of a specific product including variants and stock.",
            "method": "POST",
            "endpoint": "user/product/get",
            "parameters": ["productId"],
            "requiresAuth": True,
        },
        {
            "actionId": "list_categories",
            "actionName": "List Product Categories",
            "description": "Show all available shopping categories.",
            "method": "POST",
            "endpoint": "user/category/list",
            "parameters": ["storeId"],
            "requiresAuth": True,
        },

        # ── Cart ──
        {
            "actionId": "add_to_cart",
            "actionName": "Add to Cart",
            "description": "Add a product to the user's shopping cart.",
            "method": "POST",
            "endpoint": "user/cart/add",
            "parameters": ["productId", "quantity", "storeId", "customerId",
                           "customerPhone", "productVarientUomId"],
            "requiresAuth": True,
        },
        {
            "actionId": "view_cart",
            "actionName": "View Cart",
            "description": "Show all items in the user's shopping cart.",
            "method": "POST",
            "endpoint": "user/cart/list",
            "parameters": ["customerId", "storeId"],
            "requiresAuth": True,
        },
        {
            "actionId": "remove_from_cart",
            "actionName": "Remove from Cart",
            "description": "Remove an item from the user's cart.",
            "method": "POST",
            "endpoint": "user/cart/remove",
            "parameters": ["cartId"],
            "requiresAuth": True,
        },

        # ── Orders ──
        {
            "actionId": "create_order",
            "actionName": "Place Order",
            "description": "Place a new order with the items in the cart.",
            "method": "POST",
            "endpoint": "user/order/addv4",
            "parameters": ["totalAmount", "paymentType", "customerPhone",
                           "customerId", "paidAmount", "orderType", "currencyId",
                           "customerDeliveryAddressId", "deliveryDate",
                           "deliverySlotId", "deliverySlotTiming",
                           "deliverySlotName", "deliveryCharge"],
            "requiresAuth": True,
        },
        {
            "actionId": "list_orders",
            "actionName": "List Orders",
            "description": "Show the user's order history.",
            "method": "POST",
            "endpoint": "user/order/list",
            "parameters": ["customerId"],
            "requiresAuth": True,
        },
        {
            "actionId": "get_order_status",
            "actionName": "Track Order",
            "description": "Get tracking details for a specific order.",
            "method": "POST",
            "endpoint": "user/order/get",
            "parameters": ["orderId"],
            "requiresAuth": True,
        },

        # ── Returns ──
        {
            "actionId": "initiate_return",
            "actionName": "Initiate Return",
            "description": "Start a return or refund for a delivered order.",
            "method": "POST",
            "endpoint": "user/orderReturn/add",
            "parameters": ["orderId", "reason"],
            "requiresAuth": True,
        },
    ],

    # ── Internal Actions (not exposed to LLM, called programmatically) ──
    "internalActions": [
        {"actionId": "get_profile", "endpoint": "user/auth/getUserProfile", "method": "GET"},
        {"actionId": "get_settings", "endpoint": "user/setting", "method": "GET"},
        {"actionId": "list_addresses", "endpoint": "user/customerDeliveryAddress/list", "method": "POST"},
        {"actionId": "add_address", "endpoint": "user/customerDeliveryAddress/add", "method": "POST"},
        {"actionId": "set_default_address", "endpoint": "user/customerDeliveryAddress/setDefault", "method": "POST"},
        {"actionId": "get_delivery_slots", "endpoint": "user/deliverySlot/list", "method": "POST"},
        {"actionId": "calculate_delivery", "endpoint": "user/deliveryCharge/calculate", "method": "POST"},
        {"actionId": "list_coupons", "endpoint": "user/coupon/list", "method": "POST"},
        {"actionId": "verify_coupon", "endpoint": "user/coupon/verify", "method": "POST"},
        {"actionId": "update_cart", "endpoint": "user/cart/edit", "method": "POST"},
        {"actionId": "edit_order", "endpoint": "user/order/edit", "method": "POST"},
        {"actionId": "add_favorite", "endpoint": "user/favoriteProduct/add", "method": "POST"},
        {"actionId": "remove_favorite", "endpoint": "user/favoriteProduct/remove", "method": "POST"},
        {"actionId": "add_rating", "endpoint": "user/productRating/add", "method": "POST"},
        {"actionId": "notify_availability", "endpoint": "user/productAvailabilityNotification/add", "method": "POST"},
        {"actionId": "list_notifications", "endpoint": "user/notification/list", "method": "POST"},
        {"actionId": "mark_all_read", "endpoint": "user/notification/markAllAsRead", "method": "POST"},
        {"actionId": "get_wallets", "endpoint": "customer/wallets", "method": "GET"},
        {"actionId": "get_wallet_transactions", "endpoint": "customer/wallets/transactions", "method": "GET"},
        {"actionId": "get_return", "endpoint": "user/orderReturn/get", "method": "POST"},
        {"actionId": "list_returns", "endpoint": "user/orderReturn/list", "method": "POST"},
        {"actionId": "add_media", "endpoint": "user/media/add", "method": "POST"},
        {"actionId": "list_banners", "endpoint": "user/banner/list", "method": "POST"},
        {"actionId": "edit_customer", "endpoint": "user/customer/edit", "method": "POST"},
        {"actionId": "list_countries", "endpoint": "user/country/list", "method": "POST"},
        {"actionId": "list_states", "endpoint": "user/state/list", "method": "POST"},
        {"actionId": "list_cities", "endpoint": "user/city/list", "method": "POST"},
    ],
}


# ── Seeder ─────────────────────────────────────────────────────────────────

def seed_marketplace_service():
    """
    Inserts/updates the Marketplace service definition into the
    integration_registry MongoDB collection. Safe to run multiple times (upsert).
    """
    registry = db["integration_registry"]

    # Build a clean copy for MongoDB (no internal Python references)
    service_doc = dict(MARKETPLACE_SERVICE)

    # Merge runtime config into the document
    config = load_marketplace_config()
    service_doc["baseUrl"] = config["base_url"]
    service_doc["defaultStoreId"] = config["default_store_id"]
    service_doc["defaultCurrencyId"] = config["default_currency_id"]

    registry.update_one(
        {"serviceId": "marketplace"},
        {"$set": service_doc},
        upsert=True
    )
    print("  -> Seeded 'marketplace' service into integration_registry")
    return True


def connect_marketplace_to_project(project_id, admin_id):
    """
    Creates a project_credentials entry to mark marketplace as 'connected'
    for a project. This allows the Intent Router to see marketplace actions.
    
    Unlike OAuth/API-key integrations, marketplace auth is per-user (via OTP),
    so this entry has no encrypted token — it just enables the service.
    """
    from datetime import datetime, timezone

    config = load_marketplace_config()

    credential_doc = {
        "projectId": project_id,
        "adminId": admin_id,
        "serviceId": "marketplace",
        "authType": "marketplace_jwt",
        "credentials": {
            "storeId": config["default_store_id"],
            "currencyId": config["default_currency_id"],
            "baseUrl": config["base_url"],
        },
        "status": "connected",
        "connectedAt": datetime.now(timezone.utc),
        "updatedAt": datetime.now(timezone.utc),
    }

    db["project_credentials"].update_one(
        {"projectId": project_id, "serviceId": "marketplace"},
        {"$set": credential_doc},
        upsert=True
    )
    print(f"  -> Marketplace connected to project {project_id}")
    return True


# ── Action-to-Tool Converter ──────────────────────────────────────────────

def register_marketplace_actions():
    """
    Reads marketplace actions from the MongoDB registry and converts them into
    OpenAI Function Calling-compatible tool definitions.

    Returns a list of tool dicts ready for the Intent Router.
    This is dynamic — if you add actions to the registry and re-seed,
    the tools update automatically with zero code changes.
    """
    service = db["integration_registry"].find_one(
        {"serviceId": "marketplace", "isActive": True},
        {"_id": 0}
    )

    if not service:
        print("  [MARKETPLACE] Service not found in registry. Run seed first.")
        return []

    tools = []
    for action in service.get("availableActions", []):
        # Build parameter schema
        properties = {}
        for param in action.get("parameters", []):
            # Type inference for known numeric fields
            if param in ("limit", "offset", "quantity", "totalAmount",
                         "paidAmount", "deliveryCharge", "rating"):
                param_type = "number"
            else:
                param_type = "string"

            properties[param] = {
                "type": param_type,
                "description": f"The {param} value"
            }

        tool = {
            "type": "function",
            "function": {
                "name": f"marketplace__{action['actionId']}",
                "description": action.get("description", action["actionName"]),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": [p for p in action.get("parameters", [])
                                 if p not in ("offset", "categoryId", "limit")]
                }
            }
        }
        tools.append(tool)

    print(f"  [MARKETPLACE] Registered {len(tools)} actions as OpenAI tools")
    return tools


# ── Helpers ────────────────────────────────────────────────────────────────

def get_marketplace_base_url():
    """Returns the marketplace base URL from the registry or env."""
    config = load_marketplace_config()
    return config["base_url"].rstrip("/")


def get_action_endpoint(action_id):
    """
    Looks up the full URL for a given action_id by checking both
    availableActions and internalActions in the registry.
    """
    config = load_marketplace_config()
    base = config["base_url"].rstrip("/")

    # Check all actions in the service definition
    all_actions = (MARKETPLACE_SERVICE.get("availableActions", []) +
                   MARKETPLACE_SERVICE.get("internalActions", []))

    for action in all_actions:
        if action["actionId"] == action_id:
            return f"{base}/{action['endpoint']}"

    return None
