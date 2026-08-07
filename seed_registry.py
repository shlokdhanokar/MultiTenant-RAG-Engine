import os
import sys

# Ensure we can import from rag-engine
sys.path.insert(0, os.path.dirname(__file__))

from database import db
try:
    from phase4_integrations.registry import SERVICES
except ImportError:
    SERVICES = {}

def seed():
    print("Seeding Integration Registry into MongoDB...")
    registry_collection = db["integration_registry"]
    
    count = 0
    for service_id, service_data in SERVICES.items():
        # Clone the dictionary to avoid modifying the original
        clean_service = dict(service_data)
        clean_service["serviceId"] = service_id
        
        # Strip out secrets from oauth_config if present
        if clean_service.get("authType") == "oauth" and "oauth_config" in clean_service:
            oauth_conf = dict(clean_service["oauth_config"])
            oauth_conf.pop("clientId", None)
            oauth_conf.pop("clientSecretEncrypted", None)
            oauth_conf.pop("redirectUri", None)
            clean_service["oauth_config"] = oauth_conf

        # Upsert into MongoDB
        registry_collection.update_one(
            {"serviceId": service_id},
            {"$set": clean_service},
            upsert=True
        )
        print(f"  -> Uploaded service '{service_id}'")
        count += 1
        
    print(f"Success! {count} services successfully seeded into MongoDB.")


if __name__ == "__main__":
    seed()
