import os
import requests
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))

# Download a valid JPEG image as raw bytes
print("Downloading image...")
img_data = requests.get("https://images.unsplash.com/photo-1544551763-46a013bb70d5?w=200").content

try:
    print("Testing Gemini Vision API with raw bytes dictionary...")
    model = genai.GenerativeModel('gemini-1.5-flash')
    response = model.generate_content([
        "What is this?",
        {"mime_type": "image/jpeg", "data": img_data}
    ])
    print("Success! Response:", response.text)
except Exception as e:
    print("\nFAILED! Error message:")
    import traceback
    traceback.print_exc()
