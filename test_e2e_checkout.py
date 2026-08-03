import requests
import time
import sys
import os
import json

sys.stdout.reconfigure(encoding='utf-8')
os.environ["PYTHONIOENCODING"] = "utf-8"

BASE_URL = "http://localhost:8000"
API_KEY = "sk_proj_c98026fc9ebab9c2a1ee298945aeceaea878448496683739"
SESSION_ID = "sess_test_e2e_001"

headers = {
    "x-apikey": API_KEY,
    "Content-Type": "application/json"
}

def send(msg):
    print(f"\n{'='*60}")
    print(f"  USER: {msg}")
    print(f"{'='*60}")
    payload = {"query": msg, "session_id": SESSION_ID}
    resp = requests.post(f"{BASE_URL}/chat/v3", headers=headers, json=payload, timeout=60)
    data = resp.json()
    text = data.get("text", "")
    dtype = data.get("type", "")
    if dtype == "session_interactive_list":
        media = data.get("media", {})
        text = media.get("body", text)
    elif dtype == "session_quick_reply_with_text":
        media = data.get("media", {})
        text = media.get("body", text)
    safe = text.encode('ascii', 'replace').decode('ascii')
    print(f"  TYPE: {dtype}")
    print(f"  BOT: {safe[:1000]}")
    return data

# Step 0: Ensure we have something in cart
r = send("search for apple")
time.sleep(3)

r = send("add 1 apple to my cart")
time.sleep(3)

r = send("yes")
time.sleep(3)

# Step 1: Checkout -> should show addresses
r = send("I want to checkout")
time.sleep(3)

# Step 2: Select address -> should now show DATES (not slots!)
r = send("1")
time.sleep(3)

# Step 3: Select date -> should show SLOTS for that date
r = send("1")
time.sleep(3)

# Step 4: Select slot -> should show delivery charge + coupon/place order
r = send("1")
time.sleep(3)

# Step 5: Place order directly
r = send("place order")
time.sleep(3)

print(f"\n{'='*60}")
print("  TEST COMPLETE")
print(f"{'='*60}")
