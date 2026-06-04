import os
import io
import time
import uuid
import secrets
import hashlib
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, request, jsonify, abort, Response

# ===== MIDDLEWARE DECORATORS =====

def admin_key_required(f):
    """Middleware to require Master API Key (apikey) in headers."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from database import verify_master_key
        api_key = request.headers.get("apikey")
        if not api_key:
            abort(401, description="Missing apikey header (Master Key required)")
        
        admin = verify_master_key(api_key)
        if not admin:
            abort(401, description="Invalid Master API Key")
        
        # Pass the admin object to the route
        return f(admin, *args, **kwargs)
    return decorated_function

def project_key_required(f):
    """Middleware to require Project API Key (x-apikey) in headers."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from database import db
        import hashlib
        x_api_key = request.headers.get("x-apikey")
        if not x_api_key:
            abort(401, description="Missing x-apikey header")
        
        hashed_key = hashlib.sha256(x_api_key.encode()).hexdigest()
        
        # Find the admin who owns this key AND identify the specific project
        admin = db["admindetails"].find_one({"projectKeys.hashedKey": hashed_key})
        if not admin:
            abort(401, description="Invalid or unauthorized Project API Key")
        
        # Extract the specific projectId linked to this key
        project_id = next((pk["projectId"] for pk in admin["projectKeys"] if pk["hashedKey"] == hashed_key), None)
        
        # Pass admin and project_id to the route
        return f(admin, project_id, *args, **kwargs)
    return decorated_function
from pymongo import MongoClient
from gridfs import GridFS
from dotenv import load_dotenv
import bson
from openai import OpenAI

from phase1_upload.pdf_parser import analyze_document_layout
from phase1_upload.chunker import group_content_by_topic, generate_semantic_chunks, map_images_to_chunks
from phase2_retrieval.rag_logic import generate_text_embedding
from database import perform_semantic_retrieval

# Load environment variables from .env in this folder
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

MONGODB_URI = os.getenv("MONGODB_URI")
if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI not set in .env")

# Configure OpenAI client
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# MongoDB connection and GridFS bucket
client = MongoClient(MONGODB_URI)
DB_NAME = os.getenv("MONGODB_DB_NAME", "rag_db")
db = client[DB_NAME]


def init_gridfs_bucket():
    """
    Establish a connection to MongoDB and initialize a GridFS bucket
    specifically for handling large binary files (PDFs and images).
    """
    return GridFS(db)


fs = init_gridfs_bucket()

app = Flask(__name__)


# ===== TOKEN USAGE MONITORING =====

def log_token_usage(operation, model, usage):
    """
    Log token usage and estimated cost after every OpenAI API call.
    Helps monitor spend and optimize token consumption.
    """
    # Pricing per 1M tokens (as of May 2026)
    PRICING = {
        "text-embedding-3-small": {"input": 0.02},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    }
    
    model_pricing = PRICING.get(model, {"input": 0.0, "output": 0.0})
    
    input_tokens = getattr(usage, 'prompt_tokens', 0) or getattr(usage, 'total_tokens', 0)
    output_tokens = getattr(usage, 'completion_tokens', 0)
    total_tokens = getattr(usage, 'total_tokens', 0)
    
    input_cost = (input_tokens / 1_000_000) * model_pricing.get("input", 0)
    output_cost = (output_tokens / 1_000_000) * model_pricing.get("output", 0)
    total_cost = input_cost + output_cost
    
    print(f"  [TOKENS] {operation} | Model: {model}")
    print(f"           Input: {input_tokens} | Output: {output_tokens} | Total: {total_tokens}")
    print(f"           Cost: ${total_cost:.6f}")
    
    return {
        "operation": operation,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cost_usd": total_cost,
    }


# ===== OPENAI API WRAPPERS =====



# ===== IMAGE HANDLING (SIMPLIFIED) =====

def upload_images_to_gridfs(images, source_filename, fs, knowledge_base_id=""):
    """
    Upload extracted images to GridFS.
    No more AI captioning or separate embedding — images are mapped
    to text chunks by physical position in the PDF instead.
    """
    uploaded = []
    for i, img in enumerate(images):
        img_filename = f"{source_filename}_page{img['page_number']}.{img['image_ext']}"
        gridfs_id = fs.put(
            img["image_bytes"],
            filename=img_filename,
        )
        gridfs_id_str = str(gridfs_id)

        print(f"  [GridFS] Uploaded {img_filename} -> {gridfs_id_str}")

        uploaded.append({
            "page_number": img["page_number"],
            "gridfs_id": gridfs_id_str,
            "bbox": img.get("bbox")
        })
    return uploaded


# ===== CHUNK FORMATTING =====

def construct_chunk_metadata(chunk, source_file, source_file_id, project_id, admin_id, language="English"):
    """
    Attach crucial metadata to every generated chunk, including:
    project_id, admin_id, topic_name, page_number, chunk_index, and language.
    """
    return {
        "project_id": project_id,      # New: Multi-tenant project link
        "admin_id": admin_id,          # New: Multi-tenant admin link
        "knowledge_base_id": project_id, # Keep for backward compatibility
        "source_file": source_file,
        "source_file_id": str(source_file_id),
        "chunk_index": chunk["chunk_index"],
        "topic_name": chunk["header"],
        "text": chunk["text"],
        "word_count": chunk["word_count"],
        "page_start": chunk["page_start"],
        "page_end": chunk["page_end"],
        "associated_image_ids": chunk.get("associated_image_ids", []),
        "language": language,
    }


def format_mongodb_documents(chunks, source_file, source_file_id, project_id, admin_id, language="English"):
    """
    Format the semantically chunked text and their associated metadata
    into BSON-compatible objects ready for MongoDB.
    """
    docs = []
    for chunk in chunks:
        # Generate semantic vector for the chunk text using OpenAI
        print(f"  [EMBED] Generating vector for chunk {chunk['chunk_index']}...")
        embedding = generate_text_embedding(chunk["text"])
        
        # We pass the admin_id and project_id here
        doc = construct_chunk_metadata(
            chunk, source_file, source_file_id, project_id, admin_id, language
        )
        doc["embedding"] = embedding
        docs.append(doc)
    return docs


def bulk_insert_chunks(chunk_docs):
    """
    Execute a bulk insert operation using insert_many() to efficiently write
    all chunk documents into the chunks collection in a single network call.
    """
    if not chunk_docs:
        return 0
    chunks_collection = db["chunks"]
    result = chunks_collection.insert_many(chunk_docs)
    return len(result.inserted_ids)


# ===== API ENDPOINTS =====

@app.route('/health', methods=['GET'])
def health():
    try:
        client.admin.command('ping')
        return jsonify({"status": "ok"})
    except Exception as exc:
        abort(500, description=str(exc))


@app.route('/upload/pdf', methods=['POST'])
@project_key_required
def upload_pdf(admin, project_id):
    if 'file' not in request.files:
        abort(400, description='No file part in the request')
    file = request.files['file']
    if file.filename == '' or not file.filename.lower().endswith('.pdf'):
        abort(415, description='Only PDF files are accepted')

    pdf_bytes = file.read()
    admin_id = admin["adminId"]

    # Increment file upload count for the admin
    db["admindetails"].update_one(
        {"adminId": admin_id},
        {"$inc": {"fileUploadCount": 1}}
    )

    # Use project_id as the knowledge base identifier
    knowledge_base_id = project_id 


    # ---- Step 1: Store the raw PDF in GridFS (backup/reference) ----
    try:
        raw_file_id = fs.put(pdf_bytes, filename=file.filename)
    except Exception as exc:
        abort(500, description=f'Failed to store raw PDF: {exc}')

    # ---- Step 2: analyze_document_layout() ----
    layout = analyze_document_layout(pdf_bytes)
    if layout is None:
        abort(500, description='Failed to parse the PDF')

    # ---- Step 3: group_content_by_topic() ----
    topic_groups = group_content_by_topic(layout["semantic_blocks"])

    # ---- Step 4: generate_semantic_chunks() ----
    chunks = generate_semantic_chunks(topic_groups)

    # ---- Step 5: upload_images_to_gridfs() (simplified — no captioning) ----
    uploaded_images = upload_images_to_gridfs(layout["images"], file.filename, fs, knowledge_base_id)

    # ---- Step 6: map_images_to_chunks() ----
    chunks = map_images_to_chunks(chunks, uploaded_images)

    # Detect language from the first chunk or all chunks
    from phase2_retrieval.rag_logic import detect_query_language
    kb_language = "English"
    if chunks:
        # Sample text from first few chunks
        sample_text = " ".join([c["text"] for c in chunks[:3]])
        kb_language = detect_query_language(sample_text)
        print(f"  [LANG] Detected PDF language: {kb_language}")

    # ---- Step 7: format_mongodb_documents() (generates embeddings + attaches image IDs) ----
    chunk_docs = format_mongodb_documents(
        chunks, file.filename, raw_file_id, project_id, admin_id, language=kb_language
    )

    # ---- Step 8: bulk_insert_chunks() ----
    inserted_count = bulk_insert_chunks(chunk_docs)

    return jsonify({
        "chunks_created": inserted_count,
        "images_extracted": len(uploaded_images),
        "message": "PDF processed and chunked successfully",
        "project_id": project_id,
        "raw_fileid": str(raw_file_id),
        "total_pages": layout["total_pages"]
    }), 201


def format_whatsapp_payload(ai_text, selected_image_ids, tenant_config, base_url):
    """
    Formats the AI's response into a WhatsApp-style JSON format 
    (text bubbles, image bubbles, and interactive buttons).
    """
    payload = {
        "reply": [
            {
                "type": "text",
                "content": ai_text
            }
        ]
    }
    
    # Add images from the retrieved chunks
    for img_id in selected_image_ids:
        payload["reply"].append({
            "type": "image",
            "url": f"{base_url}/image/{img_id}"
        })
                
    # Add interactive buttons from the tenant config
    if tenant_config.get("buttons"):
        payload["reply"].append({
            "type": "buttons",
            "buttons": tenant_config["buttons"] # Changed from 'options' to 'buttons'
        })
        
    return payload


@app.route('/image/<image_id>', methods=['GET'])
def serve_image_endpoint(image_id):
    """
    Retrieves the actual image data from MongoDB GridFS 
    and streams it to the browser.
    """
    try:
        file_obj = fs.get(bson.ObjectId(image_id))
        return Response(file_obj.read(), mimetype='image/jpeg')
    except Exception as e:
        abort(404, description=f"Image not found: {e}")

@app.route('/admin/register', methods=['POST'])
def register_admin():
    """
    Creates a new admin entry in the admindetails collection.
    The adminId can be custom-provided by the client, otherwise auto-generated.
    """
    import uuid
    import secrets
    import hashlib
    from datetime import datetime, timezone
    data = request.json or {}
    
    # Compulsory requirements
    compulsory = ['companyName', 'companyPersona']
    for field in compulsory:
        if field not in data:
            abort(400, description=f"Missing compulsory field: {field}")

    admin_collection = db["admindetails"]

    # Use client-provided adminId if present, otherwise auto-generate
    admin_id = data.get('adminId') or str(uuid.uuid4())
    
    # Check if admin already exists
    if admin_collection.find_one({"adminId": admin_id}):
        abort(409, description="Admin with this adminId already exists")
    
    # Generate a secure Master API Key (apikey)
    master_key = f"sk_master_{secrets.token_hex(24)}"
    hashed_master = hashlib.sha256(master_key.encode()).hexdigest()

    # Default limit from ENV if not provided specifically in registration
    env_limit = int(os.getenv("MAX_API_KEYS_LIMIT", 5))
    max_keys = int(data.get('maxApiKeys', env_limit))
    plan_type = data.get('planType', 'free')

    new_admin = {
        "adminId": admin_id,
        "masterKeyHash": hashed_master,
        "projectKeys": [], # Array to hold project-specific X-API-Keys
        "companyName": data['companyName'],
        "companyPersona": data['companyPersona'],
        "planType": plan_type,
        "maxApiKeys": max_keys,
        "fileUploadCount": 0,
        "systemPrompt": "",
        "createdAt": datetime.now(timezone.utc),
        "updatedAt": datetime.now(timezone.utc),
    }
    admin_collection.insert_one(new_admin)

    return jsonify({
        "adminId": admin_id, 
        "apiKey": master_key, # This is the "apikey" for Super Admin tasks
        "message": "Admin registered successfully",
        "planType": plan_type
    }), 201

@app.route('/admin/project', methods=['POST'])
@admin_key_required
def create_project(admin_doc):
    """
    Creates a new project. 
    Requires Master Key (apikey) in 'apikey' header.
    Generates and returns an X-API-Key for the project.
    """
    import uuid
    import secrets
    import hashlib
    from datetime import datetime, timezone

    # Check key limit using global ENV setting if not set on admin
    admin_limit = admin_doc.get("maxApiKeys", int(os.getenv("MAX_API_KEYS_LIMIT", 5)))
    if len(admin_doc.get("projectKeys", [])) >= admin_limit:
        abort(403, description=f"Project API key limit reached ({admin_limit}).")

    data = request.json
    compulsory = ['projectName', 'projectInstruction', 'projectGuardrails', 'buttons']
    for field in compulsory:
        if not data or field not in data:
            abort(400, description=f"Missing compulsory field: {field}")

    project_id = str(uuid.uuid4())
    
    # Generate a unique X-API-Key for this project
    x_api_key = f"sk_proj_{secrets.token_hex(24)}"
    hashed_xkey = hashlib.sha256(x_api_key.encode()).hexdigest()

    new_project = {
        "adminId": admin_doc["adminId"],
        "projectId": project_id,
        "projectName": data['projectName'],
        "projectDescription": data.get('projectDescription', ''),
        "projectStatus": "active",
        "projectInstruction": data['projectInstruction'],
        "projectGuardrails": data['projectGuardrails'],
        "buttons": data['buttons'],
        "templates": data.get('templates', []), # Store pre-approved templates
        "tokenLimit": data.get('tokenLimit', None),
        "createdAt": datetime.now(timezone.utc),
        "updatedAt": datetime.now(timezone.utc),
    }
    db["adminprojects"].insert_one(new_project)

    # Store the hashed Project Key in the admin document
    db["admindetails"].update_one(
        {"adminId": admin_doc["adminId"]},
        {"$push": {"projectKeys": {"projectId": project_id, "hashedKey": hashed_xkey}}}
    )

    return jsonify({
        "x-apikey": x_api_key, # The Project Key for Chat/Upload
        "projectId": project_id, 
        "message": "Project created successfully"
    }), 201
@app.route('/admin/projects/<admin_id>', methods=['GET'])
def list_projects(admin_id):
    """
    Returns all projects belonging to a specific admin.
    """
    from database import validate_admin
    if not validate_admin(admin_id):
        abort(404, description="Admin not found")

    projects = list(db["adminprojects"].find(
        {"adminId": admin_id},
        {"_id": 0, "embedding": 0}  # Exclude internal MongoDB fields
    ))

    return jsonify({"adminId": admin_id, "projects": projects})

def translate_action_response(text, query):
    from phase2_retrieval.rag_logic import openai_client
    try:
        translate_resp = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a translation engine. Your task is to translate the SOURCE_TEXT into the EXACT SAME language and script as the USER_QUERY. If the query is Hinglish (Hindi written in English letters), translate the SOURCE_TEXT into casual, conversational Hinglish (using normal everyday words, DO NOT use pure or formal Hindi). DO NOT return or answer the USER_QUERY. Return ONLY the translated SOURCE_TEXT. Preserve all formatting, markdown, and emojis exactly."},
                {"role": "user", "content": f"USER_QUERY: {query}\n\nSOURCE_TEXT:\n{text}"}
            ],
            temperature=0.0,
        )
        return translate_resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"  [TRANSLATE] Failed to translate action response: {e}")
        return text

def core_chat_logic(data, admin, project_id):
    """
    Shared core logic for both /chat/v2 and /chat/v3.
    """
    query = data['query']
    raw_user_id = data.get('user_id')
    client_session_id = data.get('session_id')
    admin_id = admin["adminId"]

    # Validate admin exists
    from database import get_project_config, get_or_create_session, get_chat_history, save_chat_message
    from database import generate_user_uuid

    # Fetch project config
    project_config = get_project_config(project_id)
    if not project_config:
        abort(404, description="Project not found.")

    kb_id = project_id

    # ===== SECURE SESSION ALLOCATION =====
    # We maintain a common session ID throughout (created at first query step).
    # If client_session_id is provided, we continue to use it.
    # Otherwise, we generate a new UUID-based session ID.
    if client_session_id:
        session_id = client_session_id
    else:
        import uuid
        session_id = f"sess_{uuid.uuid4()}"

    # If raw_user_id (email/phone) is provided:
    #   We derive user_id from it via UUID5.
    # If raw_user_id is NOT provided:
    #   The session is anonymous (user_id = None).
    if raw_user_id:
        user_id = generate_user_uuid(raw_user_id)
    else:
        user_id = None

    print(f"  [SESSION] Raw user: {raw_user_id} -> userId: {user_id}, sessionId: {session_id}")

    # Create or find the session
    session_obj = get_or_create_session(session_id, user_id, admin_id, project_id)

    # Decision Layer: Check if session is expired (>24 hours since last activity)
    from database import is_session_expired
    is_expired = is_session_expired(session_obj)

    if is_expired:
        from datetime import datetime, timezone
        from database import db
        # Reset the session to anonymous, clear chat history and reset marketplace state
        sessions = db["chathistories"]
        sessions.update_one(
            {"sessionId": session_id},
            {
                "$set": {
                    "userId": None,
                    "messages": [],
                    "marketplaceAuth": {
                        "isAuthenticated": False,
                        "token": None,
                        "email": None,
                        "customerId": None
                    },
                    "marketplaceState": {"current_step": "idle"},
                    "updatedAt": datetime.now(timezone.utc)
                }
            }
        )
        # Refresh the session object from database
        session_obj = sessions.find_one({"sessionId": session_id})
        # Since the session expired, the user is now anonymous (user_id = None)
        user_id = None

    # Fetch history
    chat_history = get_chat_history(session_id)

    # ===== PHASE 5: MARKETPLACE STATE CHECK (before intent routing) =====
    # If the user is mid-flow (e.g., we asked for their email or OTP),
    # we handle the message directly without burning LLM tokens on intent routing.
    from database import get_marketplace_state
    marketplace_state = get_marketplace_state(session_id)

    if marketplace_state and marketplace_state.get("current_step") not in (None, "idle"):
        current_step = marketplace_state["current_step"]

        if current_step == "awaiting_email":
            # The user's message IS their email address
            from phase4_integrations.marketplace_auth import initiate_otp_login
            result = initiate_otp_login(query.strip(), session_id)
            ai_text = result["message"]
            tenant_config = project_config

            ai_text = translate_action_response(ai_text, query)

            save_chat_message(session_id, "user", query)
            save_chat_message(session_id, "ai", ai_text)

            base_url = request.host_url.rstrip('/')
            return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id

        elif current_step == "awaiting_otp":
            query_clean = query.strip()
            # If the user typed an email address instead of an OTP, restart the flow
            if "@" in query_clean and "." in query_clean:
                from phase4_integrations.marketplace_auth import initiate_otp_login
                result = initiate_otp_login(query_clean, session_id)
            else:
                # The user's message IS the OTP code
                from phase4_integrations.marketplace_auth import verify_otp_and_authenticate
                result = verify_otp_and_authenticate(query_clean, session_id, marketplace_state)
            
            ai_text = result["message"]
            tenant_config = project_config

            ai_text = translate_action_response(ai_text, query)

            save_chat_message(session_id, "user", query)
            save_chat_message(session_id, "ai", ai_text)

            base_url = request.host_url.rstrip('/')
            return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id

    # ===== PHASE 4: INTENT ROUTING (Action vs RAG) =====
    from phase4_integrations.intent_router import route_intent
    from phase4_integrations.executor import execute_action, format_action_result

    intent = route_intent(query, project_id, project_config, chat_history)

    if intent["type"] == "action":
        # ── Marketplace actions → dedicated handler ──
        if intent["service_id"] == "marketplace":
            from phase4_integrations.marketplace_auth import handle_marketplace_action
            result = handle_marketplace_action(
                session=session_obj,
                session_id=session_id,
                action_id=intent["action_id"],
                parameters=intent["parameters"]
            )
            ai_text = result["message"]
        else:
            # ── All other integrations → generic executor ──
            result = execute_action(
                project_id=project_id,
                service_id=intent["service_id"],
                action_id=intent["action_id"],
                parameters=intent["parameters"]
            )
            ai_text = format_action_result(result)

        tenant_config = project_config

        ai_text = translate_action_response(ai_text, query)

        # Save Turn
        save_chat_message(session_id, "user", query)
        save_chat_message(session_id, "ai", ai_text)

        base_url = request.host_url.rstrip('/')
        return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id

    # ===== STANDARD RAG FLOW =====

    # 1. Detect/Translate
    from phase2_retrieval.rag_logic import detect_query_language
    from database import get_knowledge_base_language
    detected_lang = detect_query_language(query)
    kb_language = get_knowledge_base_language(kb_id)
    
    search_query = query
    if detected_lang != kb_language:
        from phase2_retrieval.rag_logic import openai_client
        translate_resp = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": f"Translate the following text to {kb_language}. Return ONLY the translation."},
                {"role": "user", "content": query}
            ],
            temperature=0.0,
        )
        search_query = translate_resp.choices[0].message.content.strip()
        try:
            usage = translate_resp.usage
            from token_logger import log_openai_expenditure
            log_openai_expenditure("Query Translation", "gpt-4o-mini", usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)
        except Exception as log_err:
            print(f"Failed to log translation tokens: {log_err}")

    # 2. Retrieval
    from phase2_retrieval.rag_logic import generate_text_embedding
    from database import perform_semantic_retrieval
    query_embedding = generate_text_embedding(search_query)
    chunks = perform_semantic_retrieval(query_embedding, kb_id, n=4)
    
    if not chunks:
        return "I'm sorry, I couldn't find any information.", [], project_config, "", session_id, is_expired, project_id

    # 3. Generation
    from phase2_retrieval.rag_logic import generate_rag_response
    ai_text, tenant_config, token_info = generate_rag_response(query, chunks, project_config, chat_history)

    # Save Turn
    save_chat_message(session_id, "user", query)
    save_chat_message(session_id, "ai", ai_text, token_info)

    # 4. Image Parsing
    import re
    selected_image_ids = []
    image_match = re.search(r'\[IMAGE:\s*([^\]]+)\]', ai_text)
    if image_match:
        ids_str = image_match.group(1)
        selected_image_ids = [i.strip() for i in ids_str.split(',') if i.strip()]
        ai_text = ai_text.replace(image_match.group(0), "").strip()

    base_url = request.host_url.rstrip('/')
    return ai_text, selected_image_ids, tenant_config, base_url, session_id, is_expired, project_id


@app.route('/chat/v2', methods=['POST'])
@project_key_required
def chat_v2(admin, project_id):
    """Phase 2: Standard Reply Array output."""
    try:
        data = request.json
        if not data or 'query' not in data:
            abort(400, description="Missing compulsory fields (query).")

        ai_text, selected_image_ids, tenant_config, base_url, session_id, is_expired, project_id = core_chat_logic(data, admin, project_id)

        # Return Phase 2 Format
        payload = format_whatsapp_payload(ai_text, selected_image_ids, tenant_config, base_url)
        payload["session_id"] = session_id
        return jsonify(payload)
    except ValueError as ve:
        if "the sessionid or userid is invalid" in str(ve):
            return jsonify({"error": "the sessionid or userid is invalid"}), 400
        raise
    except Exception as e:
        abort(500, description=str(e))


@app.route('/chat/v3', methods=['POST'])
@project_key_required
def chat_v3(admin, project_id):
    """Phase 3: Agentic Session Message output."""
    try:
        data = request.json
        if not data or 'query' not in data:
            abort(400, description="Missing compulsory fields (query).")

        ai_text, selected_image_ids, tenant_config, base_url, session_id, is_expired, project_id = core_chat_logic(data, admin, project_id)

        # Prepare info for formatter
        image_urls = [f"{base_url}/image/{img_id}" for img_id in selected_image_ids]
        buttons = tenant_config.get("buttons", [])
        user_info = {"name": data.get("user_name", "User"), "phone": data.get("user_id", "")}
        tag_ids = data.get("tag_ids", [])

        # NEW: Fetch templates for decision layer
        from database import get_project_templates
        templates = get_project_templates(project_id)

        from phase3_formatting.message_formatter import format_rag_to_session_message
        agentic_payload, _ = format_rag_to_session_message(
            rag_text=ai_text,
            image_urls=image_urls,
            buttons=buttons,
            user_info=user_info,
            query=data["query"],
            tag_ids=tag_ids,
            is_expired=is_expired,
            templates=templates
        )

        agentic_payload["session_id"] = session_id
        return jsonify(agentic_payload)
    except ValueError as ve:
        if "the sessionid or userid is invalid" in str(ve):
            return jsonify({"error": "the sessionid or userid is invalid"}), 400
        raise
    except Exception as e:
        print(f"Chat v3 Error: {e}")
        import traceback
        traceback.print_exc()
        abort(500, description=str(e))

# ===== PHASE 4: INTEGRATION MANAGEMENT =====

@app.route('/integrations/registry', methods=['GET'])
@admin_key_required
def list_registry(admin_doc):
    """Lists all available services from the Master Menu that this admin has access to."""
    from phase4_integrations.registry import list_all_services
    
    # Check what integrations the Super Admin has allowed for this admin
    allowed = admin_doc.get("allowedIntegrations", None)
    
    services = list_all_services(allowed_integrations=allowed)
    return jsonify({"services": services})


@app.route('/integrations/apikey/connect', methods=['POST'])
@admin_key_required
def connect_apikey(admin_doc):
    """Scenario B: Validate, encrypt, and store an API key."""
    data = request.json
    if not data or 'serviceId' not in data or 'projectId' not in data or 'apiKey' not in data:
        abort(400, description="Missing fields: serviceId, projectId, apiKey")

    allowed = admin_doc.get("allowedIntegrations", None)
    if allowed is not None and data["serviceId"] not in allowed:
        abort(403, description=f"You do not have permission to connect to {data['serviceId']}")

    from phase4_integrations.apikey_handler import connect_api_key
    success, message = connect_api_key(
        project_id=data["projectId"],
        admin_id=admin_doc["adminId"],
        service_id=data["serviceId"],
        api_key=data["apiKey"]
    )

    if success:
        return jsonify({"status": "connected", "message": message}), 200
    else:
        abort(400, description=message)


@app.route('/integrations/oauth/initiate', methods=['POST'])
@admin_key_required
def oauth_initiate(admin_doc):
    """Scenario A: Generates the OAuth redirect URL (e.g., Google Login screen)."""
    data = request.json
    if not data or 'serviceId' not in data or 'projectId' not in data:
        abort(400, description="Missing fields: serviceId, projectId")

    allowed = admin_doc.get("allowedIntegrations", None)
    if allowed is not None and data["serviceId"] not in allowed:
        abort(403, description=f"You do not have permission to connect to {data['serviceId']}")

    from phase4_integrations.oauth_handler import get_oauth_redirect_url
    success, result = get_oauth_redirect_url(
        service_id=data["serviceId"],
        project_id=data["projectId"],
        shop=data.get("shop")  # Optional, for Shopify
    )

    if success:
        return jsonify({"redirectUrl": result}), 200
    else:
        abort(400, description=result)


@app.route('/integrations/oauth/callback', methods=['GET'])
def oauth_callback():
    """Scenario A: The public URL that providers redirect to with the auth code."""
    code = request.args.get('code')
    state = request.args.get('state')
    shop = request.args.get('shop')  # Often provided by Shopify in callback

    if not code or not state:
        abort(400, description="Missing code or state parameter.")

    from phase4_integrations.oauth_handler import handle_oauth_callback
    success, message = handle_oauth_callback(code=code, state=state, shop=shop)

    if success:
        # In production, redirect the user back to your frontend dashboard
        return jsonify({"status": "success", "message": message}), 200
    else:
        abort(400, description=message)


@app.route('/integrations/status/<project_id>', methods=['GET'])
@admin_key_required
def integration_status(admin_doc, project_id):
    """Lists all connected integrations for a specific project."""
    credentials = list(db["project_credentials"].find(
        {"projectId": project_id, "adminId": admin_doc["adminId"]},
        {"_id": 0, "credentials": 0}  # Never expose encrypted tokens
    ))
    return jsonify({"projectId": project_id, "integrations": credentials})


@app.route('/integrations/disconnect', methods=['POST'])
@admin_key_required
def disconnect_integration(admin_doc):
    """Removes a service connection from the vault."""
    data = request.json
    if not data or 'serviceId' not in data or 'projectId' not in data:
        abort(400, description="Missing fields: serviceId, projectId")

    result = db["project_credentials"].delete_one({
        "projectId": data["projectId"],
        "adminId": admin_doc["adminId"],
        "serviceId": data["serviceId"]
    })

    if result.deleted_count > 0:
        return jsonify({"status": "disconnected", "message": f"{data['serviceId']} removed."})
    else:
        abort(404, description="Integration not found.")


if __name__ == "__main__":
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
