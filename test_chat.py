import requests
import json

url = "http://localhost:8000/chat"
payload = {
    "query": "information about scuba diving?",
    "knowledge_base_id": "tourism"
}
headers = {"Content-Type": "application/json"}

response = requests.post(url, json=payload)
print(json.dumps(response.json(), indent=4))
