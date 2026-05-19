import os
from dotenv import load_dotenv
from openai import OpenAI

# Load the environment variables from .env
load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")

if not api_key:
    print("❌ ERROR: OPENAI_API_KEY is not set in your .env file.")
    exit(1)

print(f"Testing OpenAI API Key: {api_key[:10]}...{api_key[-5:]}")
print("-" * 40)

client = OpenAI(api_key=api_key)

# 1. Test Embeddings API (Used in Phase 1 Upload)
print("\nTest 1: Embeddings API (text-embedding-3-small)")
try:
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input="Hello World"
    )
    vector = response.data[0].embedding
    print("SUCCESS: Embeddings API is working!")
    print(f"   Generated a vector of length: {len(vector)}")
except Exception as e:
    print("FAILED: Embeddings API threw an error.")
    print(f"   Error Details: {e}")

# 2. Test Chat Completions API (Used in Phase 2 Chat)
print("\nTest 2: Chat Completions API (gpt-4o-mini)")
try:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Say 'hello world'"}],
        max_tokens=10
    )
    print("SUCCESS: Chat Completions API is working!")
    print(f"   AI Reply: {response.choices[0].message.content.strip()}")
except Exception as e:
    print("FAILED: Chat Completions API threw an error.")
    print(f"   Error Details: {e}")

print("-" * 40)
print("\nDIAGNOSIS:")
print("If both tests say 'SUCCESS', your API key is fully working.")
print("If you see 'insufficient_quota', you have run out of OpenAI credits (you need to add $5 to your billing account).")
print("If you see 'invalid_api_key', the key was copied incorrectly or revoked.")
