import sys
sys.path.insert(0, '.')

# Import just the detection function logic (inline for testing)
def _detect_user_language(query):
    query_lower = query.strip().lower()
    if any(ord(c) > 127 for c in query_lower):
        return "hindi"
    HINGLISH_WORDS = {
        'mujhe', 'muje', 'mereko', 'mere', 'mera', 'meri', 'tumhara', 'tumhari',
        'apna', 'apni', 'apne', 'uska', 'uski', 'unka', 'unki',
        'chahiye', 'chahie', 'chaiye', 'karo', 'karna', 'karein', 'kardo',
        'batao', 'bata', 'dikhao', 'dikha', 'dekho', 'dekhna', 'dedo',
        'bolo', 'bolna', 'suno', 'sunao', 'jao', 'jana', 'aao', 'aana',
        'lao', 'lelo', 'daal', 'daalo', 'hatao', 'nikalo', 'rakho',
        'bhejo', 'mangao', 'lagao', 'chalo', 'ruko', 'btao',
        'kya', 'kaise', 'kab', 'kahan', 'kyun', 'kaun', 'kitna', 'kitne', 'kidhar',
        'hai', 'hain', 'tha', 'thi', 'hoga', 'hogi', 'nahi', 'nhi', 'nahin',
        'haan', 'haa', 'ji', 'bas', 'mat', 'naa', 'bilkul', 'zaroor', 'zarur',
        'acha', 'accha', 'achha', 'theek', 'thik', 'sahi',
        'bohot', 'bahut', 'bahot', 'zyada', 'kam', 'thoda', 'thodi',
        'lekin', 'magar', 'isliye', 'kyunki', 'toh', 'phir', 'abhi', 'aaj', 'kal',
        'pehle', 'baad', 'wala', 'wali', 'wale',
        'bhai', 'yaar', 'dost',
        'narangi', 'seb', 'sabzi', 'sabji', 'doodh', 'chawal', 'atta', 'dal',
        'daal', 'paneer', 'tamatar', 'pyaaz', 'aloo', 'mirch', 'nimbu',
        'kripya', 'dhanyavad', 'shukriya', 'namaste', 'pranam',
        'ek', 'teen', 'chaar', 'paanch', 'chhah', 'saat', 'aath', 'nau', 'das',
    }
    query_words = set(query_lower.split())
    if query_words & HINGLISH_WORDS:
        return "hinglish"
    return "english"

# Test cases
tests = [
    # English (should NOT translate)
    ("hi", "english"),
    ("hello", "english"),
    ("show apple types", "english"),
    ("I want to buy oranges", "english"),
    ("view cart", "english"),
    ("add apple to cart", "english"),
    ("shlokdhanokar366@gmail.com", "english"),
    ("123456", "english"),
    ("show me products", "english"),
    ("checkout", "english"),
    ("yes", "english"),
    ("no", "english"),
    
    # Hinglish (should translate to Hinglish)
    ("haan", "hinglish"),
    ("mujhe narangi chahiye", "hinglish"),
    ("mujhe apple chahiye", "hinglish"),
    ("cart dikhao", "hinglish"),
    ("kya hai ye", "hinglish"),
    ("order karo", "hinglish"),
    ("aur dikhao", "hinglish"),
    
    # Hindi script (should translate to Hindi)
    ("हाँ", "hindi"),
    ("मुझे सेब चाहिए", "hindi"),
    ("कार्ट दिखाओ", "hindi"),
]

print("=" * 60)
passed = 0
failed = 0
for query, expected in tests:
    result = _detect_user_language(query)
    status = "PASS" if result == expected else "FAIL"
    if status == "FAIL":
        failed += 1
        print(f"  {status}: '{query}' -> {result} (expected {expected})")
    else:
        passed += 1
        print(f"  {status}: '{query}' -> {result}")

print("=" * 60)
print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
