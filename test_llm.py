import json
from server import core_chat_logic
from database import db

admin = db['admindetails'].find_one()
if not admin:
    print("No admin found!")
    exit(1)

project_keys = admin.get('projectKeys', [])
if not project_keys:
    print("No project keys found!")
    exit(1)

project_id = project_keys[0].get('projectId')
if not project_id:
    print("No project ID found!")
    exit(1)

print(f"Using Project ID: {project_id}")

# The user is testing via postman email user
# Let's send a sequence of messages
session_id = "sess_testing_flow_102"
user_id = "+917048809875" # User's phone number from snippet

messages = [
    "Search for apple and add 1 to my cart",
    "I want to checkout",
    "1", # Selects Address 1
    "1", # Selects Slot 1
    "Yes, check for coupons",
    "WELCOME20", # Selects the coupon
    "Yes place the order"
]

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
        ai_text, _, _, _, _, _, _, wp_payload = core_chat_logic(data, admin, project_id)
        if wp_payload:
            # WhatsApp format payload
            if wp_payload.get("type") == "session_text":
                print(f"BOT: {wp_payload.get('text')}")
            elif wp_payload.get("type") == "session_interactive_list":
                media = wp_payload.get("media", {})
                print(f"BOT (Interactive): {media.get('body')}")
            else:
                print(f"BOT: {wp_payload}")
        else:
            print(f"BOT: {ai_text}")
    except Exception as e:
        print(f"ERROR: {e}")
