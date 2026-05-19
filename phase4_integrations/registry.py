"""
Phase 4 - Step A2: Integration Registry
Fetches service definitions dynamically from MongoDB and merges with config.py credentials.
"""
import os
import sys

# Add parent directory so we can import database
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from database import db


def get_service(service_id):
    """
    Fetch a single service from the registry by its serviceId, 
    and dynamically inject sensitive credentials from config.py.
    """
    service = db["integration_registry"].find_one(
        {"serviceId": service_id, "isActive": True},
        {"_id": 0}
    )
    
    if not service:
        return None
        
    # Dynamically inject credentials from config.py
    try:
        import config
        creds = config.INTEGRATION_CREDENTIALS.get(service_id)
        
        if creds and service.get("authType") in ["oauth", "shopify_oauth"] and "oauth_config" in service:
            # We assume oauth_config exists in DB without secrets
            service["oauth_config"]["clientId"] = creds.get("clientId", "")
            service["oauth_config"]["clientSecret"] = creds.get("clientSecret", "")
            service["oauth_config"]["redirectUri"] = creds.get("redirectUri", "")
    except ImportError:
        pass
        
    return service


def list_all_services(allowed_integrations=None):
    """
    List active services in the registry.
    If allowed_integrations is provided, only return those specific services.
    """
    query = {"isActive": True}
    
    if allowed_integrations is not None:
        query["serviceId"] = {"$in": allowed_integrations}
        
    return list(db["integration_registry"].find(
        query,
        {"_id": 0}
    ))
