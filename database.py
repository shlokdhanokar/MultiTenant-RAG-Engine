import hashlib
import os
import uuid as _uuid
from pymongo import MongoClient
from dotenv import load_dotenv

# Load environment variables from .env in this folder
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

MONGODB_URI = os.getenv("MONGODB_URI")
client = MongoClient(MONGODB_URI)
db = client[os.getenv("MONGODB_DB_NAME", "rag_db")]

# Fixed namespace for deterministic UUID5 generation (never change this)
_NAMESPACE = _uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def generate_user_uuid(raw_user_id):
    """
    Generates a deterministic UUID from the raw user identifier (email / phone).
    The same input always produces the same UUID.
    """
    normalized = raw_user_id.strip().lower()
    return str(_uuid.uuid5(_NAMESPACE, normalized))




def verify_master_key(api_key):
    """
    Verifies the Master API Key (apikey) used for admin-level operations.
    """
    if not api_key:
        return None
    hashed_key = hashlib.sha256(api_key.encode()).hexdigest()
    return db["admindetails"].find_one({"masterKeyHash": hashed_key})

def verify_project_key(api_key):
    """
    Verifies a Project API Key (X-API-Key) used for Chat/Upload.
    """
    if not api_key:
        return None
    hashed_key = hashlib.sha256(api_key.encode()).hexdigest()
    return db["admindetails"].find_one({"projectKeys.hashedKey": hashed_key})

def validate_admin(admin_id):
    """
    Checks if an admin with the given adminId exists.
    """
    if not admin_id:
        return False
    return db["admindetails"].find_one({"adminId": admin_id}) is not None


def get_project_templates(project_id):
    """
    Retrieves the list of approved templates for a specific project.
    """
    project = db["adminprojects"].find_one({"projectId": project_id})
    if project:
        return project.get("templates", [])
    return []

def get_knowledge_base_language(knowledge_base_id):
    """
    Returns the language of the specified knowledge base.
    """
    chunks_collection = db["chunks"]
    sample_chunk = chunks_collection.find_one({"knowledge_base_id": knowledge_base_id})
    if sample_chunk:
        return sample_chunk.get("language", "English")
    return "English"

def get_project_config(project_id):
    """
    Fetch tenant-specific formatting and instructions.
    """
    return db["adminprojects"].find_one({"projectId": project_id})

def perform_semantic_retrieval(query_embedding, knowledge_base_id, n=4):
    """
    Performs a vector search in MongoDB to find the most relevant context chunks.
    """
    chunks_collection = db["chunks"]
    pipeline = [
        {
            "$vectorSearch": {
                "index": "vector_index",
                "path": "embedding",
                "queryVector": query_embedding,
                "numCandidates": 1000,
                "limit": 1000
            }
        },
        {
            "$match": {"knowledge_base_id": knowledge_base_id}
        },
        {
            "$limit": n
        },
        {
            "$project": {
                "text": 1,
                "topic_name": 1,
                "associated_image_ids": 1,
                "score": {"$meta": "vectorSearchScore"}
            }
        }
    ]
    return list(chunks_collection.aggregate(pipeline))

def get_or_create_session(session_id, user_id, admin_id, project_id):
    """
    User-persistent session management with session/user linkage validation.
    Raises ValueError if session exists but is associated with a different userId.
    """
    from datetime import datetime, timezone
    sessions = db["chathistories"]
    session = sessions.find_one({"sessionId": session_id})

    if session:
        # Validate that the user_id matches the session's userId (only if user_id is provided)
        if user_id is not None and session.get("userId") is not None:
            if session.get("userId") != user_id:
                raise ValueError("the sessionid or userid is invalid")
        return session

    new_session = {
        "sessionId": session_id,
        "userId": user_id,
        "adminId": admin_id,
        "projectId": project_id,
        "messages": [],
        "createdAt": datetime.now(timezone.utc),
        "updatedAt": datetime.now(timezone.utc),
    }
    sessions.insert_one(new_session)
    return new_session

def is_session_expired(session_obj):
    """
    Decision Layer: Checks if the session is active or expired based on 24hr window.
    """
    if not session_obj or not session_obj.get("updatedAt"):
        return False
        
    from datetime import datetime, timedelta, timezone
    last_activity = session_obj["updatedAt"]
    
    # Ensure both are offset-aware or offset-naive before comparison
    if last_activity.tzinfo is not None:
        now = datetime.now(timezone.utc)
    else:
        now = datetime.utcnow()
        
    return (now - last_activity) > timedelta(hours=24)

def get_chat_history(session_id):
    """
    Retrieve message history for context memory.
    """
    sessions = db["chathistories"]
    session = sessions.find_one({"sessionId": session_id})
    if not session or not session.get("messages"):
        return []
    
    # Return formatted history for OpenAI
    return [{"sender": msg["sender"], "content": msg["content"]} for msg in session["messages"][-10:]]

def save_chat_message(session_id, sender, content, token_info=None):
    """
    Push a new message into the session's messages array.
    """
    from datetime import datetime, timezone
    from bson import ObjectId
    
    sessions = db["chathistories"]
    message = {
        "_id": ObjectId(),
        "sender": sender,
        "content": content,
        "promptTokens": token_info.get("input_tokens", 0) if token_info else 0,
        "completionTokens": token_info.get("output_tokens", 0) if token_info else 0,
        "totalTokens": token_info.get("total_tokens", 0) if token_info else 0,
        "createdAt": datetime.now(timezone.utc),
        "updatedAt": datetime.now(timezone.utc),
    }
    
    sessions.update_one(
        {"sessionId": session_id},
        {
            "$push": {"messages": message},
            "$set": {"updatedAt": datetime.now(timezone.utc)}
        }
    )


# ===== MARKETPLACE STATE MANAGEMENT =====

def get_marketplace_state(session_id):
    """
    Retrieves the marketplace shopping state from the user's session.
    Returns None if no marketplace state exists yet.
    """
    sessions = db["chathistories"]
    session = sessions.find_one({"sessionId": session_id})
    if not session:
        return None
    return session.get("marketplaceState")


def update_marketplace_state(session_id, state_data):
    """
    Updates the marketplace shopping state on the user's session.
    Merges the provided state_data into the existing marketplaceState.
    """
    from datetime import datetime, timezone
    sessions = db["chathistories"]

    # Build the $set dict by prefixing each key with "marketplaceState."
    set_dict = {f"marketplaceState.{k}": v for k, v in state_data.items()}
    set_dict["updatedAt"] = datetime.now(timezone.utc)

    sessions.update_one(
        {"sessionId": session_id},
        {"$set": set_dict}
    )


def clear_marketplace_state(session_id):
    """
    Resets the marketplace state back to idle.
    Called when user says 'cancel' or session expires.
    """
    from datetime import datetime, timezone
    sessions = db["chathistories"]
    sessions.update_one(
        {"sessionId": session_id},
        {
            "$set": {
                "marketplaceState": {"current_step": "idle"},
                "updatedAt": datetime.now(timezone.utc),
            }
        }
    )


def log_api_call(log_data):
    """
    Log an API request (incoming or outgoing) to the api_logs collection.
    """
    from datetime import datetime, timezone
    # Add timestamp if not present
    if "timestamp" not in log_data:
        log_data["timestamp"] = datetime.now(timezone.utc)
    
    # Fire and forget into MongoDB
    try:
        db["api_logs"].insert_one(log_data)
    except Exception as e:
        print(f"Failed to log API call: {e}")

if __name__ == "__main__":
    try:
        client.admin.command('ping')
        print("Connected to MongoDB!")
    except Exception as e:
        print("Failed:", e)
