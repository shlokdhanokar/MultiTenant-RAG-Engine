import os
import sys
from openai import OpenAI
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8')

load_dotenv('.env')
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

prompt = """You are a translator matching the exact language, dialect, and alphabet of USER_QUERY.
Translate SOURCE_TEXT so it seamlessly matches the style of USER_QUERY.

Examples:
USER_QUERY: "view cart"
SOURCE_TEXT: "Added to cart!"
OUTPUT: Added to cart!

USER_QUERY: "haan"
SOURCE_TEXT: "Added to cart!"
OUTPUT: Cart mein add ho gaya!

USER_QUERY: "mujhe apple chahiye"
SOURCE_TEXT: "Here are your products"
OUTPUT: Ye rahe aapke products

USER_QUERY: "हाँ"
SOURCE_TEXT: "Added to cart!"
OUTPUT: कार्ट में जोड़ा गया!

CRITICAL: If USER_QUERY contains Hindi words written in English letters (Hinglish), you MUST output Hinglish using ONLY English letters. Never output Devanagari script for Hinglish queries."""

def test_translation(query):
    r = client.chat.completions.create(
        model='gpt-4o-mini', 
        messages=[
            {'role': 'system', 'content': prompt}, 
            {'role': 'user', 'content': f'USER_QUERY: {query}\n\nSOURCE_TEXT:\nAdded to cart!\n\nKinoo Orange x 1 — $50.00'}
        ],
        temperature=0.0
    )
    print(f"[{query}]:", r.choices[0].message.content)

test_translation("haan")
test_translation("हाँ")
test_translation("yes")
test_translation("mujhe narangi chahiye")
