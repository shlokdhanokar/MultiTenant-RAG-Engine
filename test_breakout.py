import urllib.request, json, sys

query = sys.argv[1] if len(sys.argv) > 1 else "add kinoo orange"

req = urllib.request.Request('http://localhost:8000/chat/v3', method='POST')
req.add_header('Content-Type', 'application/json')
req.add_header('x-apikey', 'sk_proj_c98026fc9ebab9c2a1ee298945aeceaea878448496683739')
data = json.dumps({'query': query, 'session_id': 'sess_efa42fa5-ea9c-5c75-8d6a-e8f892a6c6c8'}).encode('utf-8')
try:
    with urllib.request.urlopen(req, data=data) as f:
        print(f.read().decode('utf-8'))
except Exception as e:
    print('Error:', e)
