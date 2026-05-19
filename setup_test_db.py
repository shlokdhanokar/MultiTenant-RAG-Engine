import os
import sys
import hashlib
from datetime import datetime, timezone

# Ensure we can import from the rag-engine module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database import db

def setup_test_data():
    """
    Sets up dummy data in MongoDB so the user can test the APIs in Postman.
    """
    print("Setting up test data for Postman...")

    # 1. Setup Dummy OAuth Client IDs in the Registry
    services = ["google_calendar", "shopify", "slack", "calendly"]
    for service_id in services:
        result = db["integration_registry"].update_one(
            {"serviceId": service_id},
            {"$set": {"oauth_config.clientId": f"dummy_client_id_for_{service_id}"}}
        )
        if result.modified_count > 0:
            print(f"Added dummy clientId to '{service_id}'")

    # 2. Setup a Dummy Admin with a known Master API Key
    test_api_key = "test-master-key-123"
    hashed_key = hashlib.sha256(test_api_key.encode()).hexdigest()

    admin_doc = {
        "adminId": "admin_test_999",
        "companyName": "Postman Test Company",
        "masterKeyHash": hashed_key,
        "createdAt": datetime.now(timezone.utc),
        "updatedAt": datetime.now(timezone.utc),
        "allowedIntegrations": ["infoware_marketplace", "shopify", "google_calendar"]
    }

    db["admindetails"].update_one(
        {"adminId": "admin_test_999"},
        {"$set": admin_doc},
        upsert=True
    )
    print(f"\nCreated/Updated dummy admin.")
    
    # 3. Setup a Dummy Project Key
    test_project_key = "test-project-key-123"
    hashed_project_key = hashlib.sha256(test_project_key.encode()).hexdigest()
    
    db["admindetails"].update_one(
        {"adminId": "admin_test_999"},
        {"$set": {
            "projectKeys": [
                {"projectId": "test_project_123", "hashedKey": hashed_project_key, "name": "Test Project"}
            ]
        }}
    )

    print("-" * 40)
    print("READY FOR POSTMAN TESTING")
    print(f"Master API Key (apikey): {test_api_key}")
    print(f"Project API Key (x-apikey): {test_project_key}")
    print(f"Project ID: test_project_123")
    print("-" * 40)

if __name__ == "__main__":
    setup_test_data()
