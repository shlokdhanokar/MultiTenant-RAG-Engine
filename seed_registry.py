import os
import sys

# Ensure we can import from rag-engine
sys.path.insert(0, os.path.dirname(__file__))

from database import db
from phase4_integrations.registry import SERVICES

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

    # ── Seed Marketplace Service ──
    print("\nSeeding Marketplace service...")
    from phase4_integrations.marketplace_service import seed_marketplace_service
    seed_marketplace_service()
    print("Marketplace seeding complete!")


def connect_marketplace(project_id, admin_id):
    """
    Helper to connect marketplace to a specific project.
    Run after seeding to enable marketplace for a project.
    """
    from phase4_integrations.marketplace_service import connect_marketplace_to_project
    connect_marketplace_to_project(project_id, admin_id)


if __name__ == "__main__":
    seed()

    # If command-line args are provided, connect marketplace to a project
    if len(sys.argv) >= 3:
        pid = sys.argv[1]
        aid = sys.argv[2]
        print(f"\nConnecting marketplace to project {pid}...")
        connect_marketplace(pid, aid)

