import json
import os
import sys

from flask import Flask
from server import app, core_chat_logic
from database import db
from unittest.mock import patch, MagicMock

# Find an existing admin
admin = db["admindetails"].find_one()
if not admin:
    print("No admin found in DB!")
    exit(1)
    
if not admin.get("projectKeys"):
    # Mock a project config if none exists
    project_id = "test_project"
else:
    project_id = admin["projectKeys"][0]["projectId"]

# Find project config
project_config = db["adminprojects"].find_one({"projectId": project_id})
if not project_config:
    db["adminprojects"].insert_one({
        "projectId": project_id,
        "projectName": "Test",
        "integrations": [
            {
                "serviceId": "marketplace",
                "config": {}
            }
        ],
        "projectInstruction": "Test"
    })
else:
    # Ensure marketplace is active
    integrations = project_config.get("integrations", [])
    if not any(i.get("serviceId") == "marketplace" for i in integrations):
        integrations.append({"serviceId": "marketplace", "config": {}})
        db["adminprojects"].update_one({"projectId": project_id}, {"$set": {"integrations": integrations}})

# Mock project credentials so get_connected_services finds marketplace
if not db["project_credentials"].find_one({"projectId": project_id, "serviceId": "marketplace"}):
    db["project_credentials"].insert_one({
        "projectId": project_id,
        "serviceId": "marketplace",
        "status": "connected"
    })

session_id = "test_local_session_999"
user_id = "+917048809875"

messages = [
    "Search for apple and add 1 to my cart",
    "I want to checkout",
    "1", # Selects Address 1
    "1", # Selects Slot 1
    "Yes, check for coupons",
    "WELCOME20", # Selects the coupon
    "Yes place the order"
]

with app.test_request_context('/chat/v3', base_url="http://localhost:8000"):
    for msg in messages:
        print(f"\n======================================")
        print(f"USER: {msg}")
        
        data = {
            "query": msg,
            "session_id": session_id,
            "user_id": user_id,
            "user_name": "Test User"
        }
        
        try:
            ai_text, _, _, _, _, _, _, wp = core_chat_logic(data, admin, project_id)
            if wp:
                if wp.get("type") == "session_text":
                    print(f"BOT: {wp.get('text')}")
                elif wp.get("type") == "session_interactive_list":
                    media = wp.get("media", {})
                    print(f"BOT (Interactive): {media.get('body')}")
                else:
                    print(f"BOT: {json.dumps(wp)}")
            else:
                print(f"BOT: {ai_text}")
        except Exception as e:
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()

