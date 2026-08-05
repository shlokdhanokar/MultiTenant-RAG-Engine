import os
import io
import time
import uuid
import secrets
import hashlib
import hmac
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, request, jsonify, abort, Response
import logging

logger = logging.getLogger(__name__)

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
        
        request.admin_id = admin.get("adminId")
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
        
        request.admin_id = admin.get("adminId")
        request.project_id = project_id

        # Pass admin and project_id to the route
        return f(admin, project_id, *args, **kwargs)
    return decorated_function
from pymongo import MongoClient
from gridfs import GridFS
from dotenv import load_dotenv
import bson

from phase1_upload.chunker import group_content_by_topic, generate_semantic_chunks, map_images_to_chunks
from phase2_retrieval.rag_logic import generate_text_embedding
from database import perform_semantic_retrieval

# Load environment variables from .env in this folder
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

from logging_config import setup_logging
setup_logging()

MONGODB_URI = os.getenv("MONGODB_URI")
if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI not set in .env")

IMAGE_URL_SIGNING_SECRET = os.getenv("IMAGE_URL_SIGNING_SECRET")
if not IMAGE_URL_SIGNING_SECRET:
    raise RuntimeError(
        "IMAGE_URL_SIGNING_SECRET not set in .env (generate with: "
        "python -c \"import secrets; print(secrets.token_hex(32))\")"
    )
IMAGE_URL_TTL_SECONDS = 24 * 60 * 60  # signed image links expire after 24h

if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
    raise RuntimeError("GEMINI_API_KEY not set in .env")

# MongoDB connection and GridFS bucket
client = MongoClient(MONGODB_URI)
DB_NAME = os.getenv("MONGODB_DB_NAME", "rag_db")
db = client[DB_NAME]

# Marketplace/checkout belongs to a separate app and is disabled while this
# repo focuses on the core RAG platform. Code is kept intact (not deleted) so
# it can be re-enabled by flipping this flag. See phase4_integrations/marketplace_auth.py,
# phase4_integrations/marketplace_service.py, checkout_states.py.
MARKETPLACE_ENABLED = os.getenv("MARKETPLACE_ENABLED", "false").strip().lower() == "true"


def init_gridfs_bucket():
    """
    Establish a connection to MongoDB and initialize a GridFS bucket
    specifically for handling large binary files (PDFs and images).
    """
    return GridFS(db)


fs = init_gridfs_bucket()

# Open provider connections in the background at import time. Cold start costs
# ~13s (TLS handshake plus SDK client construction) and would otherwise be paid
# by the first visitor's first question. Threaded so it never delays boot or
# blocks a health check, and failures inside are logged rather than raised.
def _start_prewarm():
    import threading
    from llm_client import prewarm
    threading.Thread(target=prewarm, name="llm-prewarm", daemon=True).start()

_start_prewarm()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB upload cap

# The demo UI is served from a different origin (Vite in dev, static host in
# prod), so the browser needs explicit permission to call the API.
from flask_cors import CORS
CORS(
    app,
    resources={r"/api/demo/*": {"origins": os.getenv("CORS_ORIGINS", "*").split(",")}},
)

# ===== RATE LIMITING =====
# In-memory storage is fine for a single-instance deployment; if this ever
# scales to multiple app instances, switch storage_uri to a shared Redis URL
# so limits are enforced across instances instead of per-process.
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per hour"],
    storage_uri="memory://",
)

# ===== GLOBAL REQUEST LOGGING =====
from database import log_api_call

@app.before_request
def start_timer():
    request.start_time = time.time()

@app.after_request
def log_incoming_request(response):
    if request.path == '/health':
        return response

    duration_ms = (time.time() - getattr(request, 'start_time', time.time())) * 1000

    payload = None
    if request.is_json:
        try:
            payload = request.get_json(silent=True)
        except Exception:
            pass

    log_data = {
        "type": "incoming",
        "endpoint": request.path,
        "method": request.method,
        "clientIp": request.remote_addr,
        "response_status": response.status_code,
        "duration_ms": round(duration_ms, 2),
        "projectId": getattr(request, 'project_id', None),
        "adminId": getattr(request, 'admin_id', None),
        "request_payload": payload
    }
    
    log_api_call(log_data)
    return response


# ===== OUTGOING REQUEST LOGGING (MONKEY-PATCH) =====
import requests

_original_request = requests.Session.request

def logged_request(self, method, url, **kwargs):
    start_time = time.time()
    try:
        response = _original_request(self, method, url, **kwargs)
        duration_ms = (time.time() - start_time) * 1000
        
        payload = kwargs.get('json') or kwargs.get('data')
        if isinstance(payload, dict):
            # Mask sensitive tokens if needed
            pass
            
        from flask import has_request_context
        
        log_data = {
            "type": "outgoing",
            "endpoint": url,
            "method": method.upper(),
            "response_status": response.status_code,
            "duration_ms": round(duration_ms, 2),
            "request_payload": payload,
            # For outgoing, projectId is harder to extract globally, but we capture the raw call
            "projectId": getattr(request, 'project_id', None) if has_request_context() else None
        }
        log_api_call(log_data)
        return response
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        payload = kwargs.get('json') or kwargs.get('data')
        from flask import has_request_context
        log_data = {
            "type": "outgoing",
            "endpoint": url,
            "method": method.upper(),
            "response_status": 0,
            "duration_ms": round(duration_ms, 2),
            "request_payload": payload,
            "error": str(e),
            "projectId": getattr(request, 'project_id', None) if has_request_context() else None
        }
        log_api_call(log_data)
        raise

requests.Session.request = logged_request


# ===== TOKEN USAGE MONITORING =====

# ==========================================
# PHASE 5: Payment Webhook
# ==========================================

@app.route('/api/payment/webhook', methods=['POST'])
def verify_payment_webhook():
    """
    Background listener function that receives callbacks from the payment gateway
    to confirm whether the user's transaction was successful or failed.
    """
    if not MARKETPLACE_ENABLED:
        abort(404, description="Marketplace integration is disabled on this deployment.")

    try:
        data = request.json
        if not data:
            return jsonify({"success": False, "error": "No JSON payload provided"}), 400

        order_id = data.get("orderId")
        status = data.get("status", "").upper()
        
        logger.info(f"  [WEBHOOK] Received payment update for Order {order_id}: {status}")
        
        if status == "SUCCESS":
            # Here we would typically call the marketplace API to update the order status
            # e.g., requests.post("http://localhost:3000/api/admin/order/updateStatus", ...)
            pass
            
        return jsonify({"success": True, "message": "Webhook processed successfully"}), 200
        
    except Exception as e:
        logger.error(f"  [WEBHOOK] Error processing payment webhook: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==========================================
# ERROR HANDLERS
# ==========================================


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

        logger.info(f"  [GridFS] Uploaded {img_filename} -> {gridfs_id_str}")

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
        logger.info(f"  [EMBED] Generating vector for chunk {chunk['chunk_index']}...")
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


@app.route('/upload/document', methods=['POST'])
@app.route('/upload/pdf', methods=['POST'])  # retained: original PDF-only path
@project_key_required
@limiter.limit("20 per hour", key_func=lambda: getattr(request, "project_id", None) or get_remote_address())
def upload_document(admin, project_id):
    """
    Ingests a document into this project's knowledge base.
    Supported: PDF, DOCX, PPTX, XLSX, and images (OCR).
    """
    from phase1_upload.ingest import analyze_document, get_extension, UnsupportedFileTypeError, SUPPORTED_EXTENSIONS

    if 'file' not in request.files:
        abort(400, description='No file part in the request')
    file = request.files['file']
    if not file.filename:
        abort(400, description='No file selected')

    ext = get_extension(file.filename)
    if ext not in SUPPORTED_EXTENSIONS:
        abort(415, description=f"Unsupported file type '.{ext}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")

    file_bytes = file.read()
    admin_id = admin["adminId"]

    # Increment file upload count for the admin
    db["admindetails"].update_one(
        {"adminId": admin_id},
        {"$inc": {"fileUploadCount": 1}}
    )

    # Use project_id as the knowledge base identifier
    knowledge_base_id = project_id

    # ---- Step 1: Store the raw file in GridFS (backup/reference) ----
    try:
        raw_file_id = fs.put(file_bytes, filename=file.filename)
    except Exception as exc:
        abort(500, description=f'Failed to store raw file: {exc}')

    # ---- Step 2: parse into the shared layout shape (format-specific) ----
    try:
        layout = analyze_document(file_bytes, file.filename)
    except UnsupportedFileTypeError as e:
        abort(415, description=str(e))
    if layout is None:
        abort(422, description=f'Failed to parse the uploaded .{ext} file (it may be corrupt, empty, or an image with no readable text)')

    # ---- Step 3: group_content_by_topic() ----
    topic_groups = group_content_by_topic(layout["semantic_blocks"])

    # ---- Step 4: generate_semantic_chunks() ----
    chunks = generate_semantic_chunks(topic_groups)
    if not chunks:
        abort(422, description='No readable text content was extracted from the uploaded file')

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
        logger.info(f"  [LANG] Detected document language: {kb_language}")

    # ---- Step 7: format_mongodb_documents() (generates embeddings + attaches image IDs) ----
    from phase2_retrieval.rag_logic import EmbeddingGenerationError
    try:
        chunk_docs = format_mongodb_documents(
            chunks, file.filename, raw_file_id, project_id, admin_id, language=kb_language
        )
    except EmbeddingGenerationError as e:
        abort(502, description=f"Embedding service unavailable, please retry the upload: {e}")

    # ---- Step 8: bulk_insert_chunks() ----
    inserted_count = bulk_insert_chunks(chunk_docs)

    return jsonify({
        "chunks_created": inserted_count,
        "images_extracted": len(uploaded_images),
        "message": "Document processed and chunked successfully",
        "file_type": ext,
        "project_id": project_id,
        "raw_fileid": str(raw_file_id),
        "total_pages": layout["total_pages"]
    }), 201


def generate_image_url(base_url, image_id):
    """
    Builds a time-limited, HMAC-signed URL for fetching a GridFS image.
    Needed because WhatsApp's own servers fetch these URLs directly — there's
    no way to attach an API-key header — so the link itself must be unguessable
    and expire, rather than relying on the raw (enumerable) GridFS ObjectId alone.
    """
    expiry = int(time.time()) + IMAGE_URL_TTL_SECONDS
    signature = hmac.new(
        IMAGE_URL_SIGNING_SECRET.encode(), f"{image_id}.{expiry}".encode(), hashlib.sha256
    ).hexdigest()
    return f"{base_url}/image/{image_id}?exp={expiry}&sig={signature}"


def _verify_image_token(image_id, exp, sig):
    if not exp or not sig:
        return False
    try:
        expiry = int(exp)
    except (TypeError, ValueError):
        return False
    if expiry < int(time.time()):
        return False
    expected_sig = hmac.new(
        IMAGE_URL_SIGNING_SECRET.encode(), f"{image_id}.{expiry}".encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_sig, sig)


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
            "url": generate_image_url(base_url, img_id)
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
    and streams it to the browser, provided the signed URL token is valid.
    """
    if not _verify_image_token(image_id, request.args.get("exp"), request.args.get("sig")):
        abort(403, description="Invalid or expired image link")
    try:
        file_obj = fs.get(bson.ObjectId(image_id))
        return Response(file_obj.read(), mimetype='image/jpeg')
    except Exception as e:
        abort(404, description=f"Image not found: {e}")

@app.route('/admin/register', methods=['POST'])
@limiter.limit("5 per hour")
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
@admin_key_required
def list_projects(admin_doc, admin_id):
    """
    Returns all projects belonging to a specific admin.
    Requires the Master Key for the SAME admin_id being requested —
    prevents any tenant from listing another tenant's projects.
    """
    if admin_doc["adminId"] != admin_id:
        abort(403, description="This Master Key does not grant access to the requested admin_id")

    projects = list(db["adminprojects"].find(
        {"adminId": admin_id},
        {"_id": 0, "embedding": 0}  # Exclude internal MongoDB fields
    ))

    return jsonify({"adminId": admin_id, "projects": projects})


@app.route('/admin/usage', methods=['GET'])
@admin_key_required
def admin_usage(admin_doc):
    """
    Per-project token usage / estimated OpenAI cost, aggregated from stored
    chat messages. Optional ?project_id=<id> to scope to a single project.
    """
    from database import get_usage_summary
    project_id = request.args.get("project_id")
    summary = get_usage_summary(admin_doc["adminId"], project_id=project_id)
    return jsonify({"adminId": admin_doc["adminId"], "usage": summary})


def _detect_user_language(query):
    """
    Deterministic language detection. Returns 'hindi', 'hinglish', or 'english'.
    No LLM call — uses character analysis + a word-level Hinglish dictionary.
    """
    query_lower = query.strip().lower()

    # 1. Non-ASCII characters → Hindi script (Devanagari, Arabic, etc.)
    if any(ord(c) > 127 for c in query_lower):
        return "hindi"

    # 2. Known Hinglish words (Hindi words romanized in Latin script).
    #    These words are unambiguously NOT English.
    HINGLISH_WORDS = {
        # Pronouns / particles
        'mujhe', 'muje', 'mereko', 'mere', 'mera', 'meri', 'tumhara', 'tumhari',
        'apna', 'apni', 'apne', 'uska', 'uski', 'unka', 'unki',
        # Verbs
        'chahiye', 'chahie', 'chaiye', 'karo', 'karna', 'karein', 'kardo',
        'batao', 'bata', 'dikhao', 'dikha', 'dekho', 'dekhna', 'dedo',
        'bolo', 'bolna', 'suno', 'sunao', 'jao', 'jana', 'aao', 'aana',
        'lao', 'lelo', 'daal', 'daalo', 'hatao', 'nikalo', 'rakho',
        'bhejo', 'mangao', 'lagao', 'chalo', 'ruko', 'btao',
        # Question words
        'kya', 'kaise', 'kab', 'kahan', 'kyun', 'kaun', 'kitna', 'kitne', 'kidhar',
        # Common words
        'hai', 'hain', 'tha', 'thi', 'hoga', 'hogi', 'nahi', 'nhi', 'nahin',
        'haan', 'haa', 'ji', 'bas', 'mat', 'naa', 'bilkul', 'zaroor', 'zarur',
        'acha', 'accha', 'achha', 'theek', 'thik', 'sahi',
        'bohot', 'bahut', 'bahot', 'zyada', 'kam', 'thoda', 'thodi',
        'lekin', 'magar', 'isliye', 'kyunki', 'toh', 'phir', 'abhi', 'aaj', 'kal',
        'pehle', 'baad', 'wala', 'wali', 'wale',
        'bhai', 'yaar', 'dost',
        # Shopping context
        'narangi', 'seb', 'sabzi', 'sabji', 'doodh', 'chawal', 'atta', 'dal',
        'daal', 'paneer', 'tamatar', 'pyaaz', 'aloo', 'mirch', 'nimbu',
        # Polite / greetings
        'kripya', 'dhanyavad', 'shukriya', 'namaste', 'pranam',
        # Numbers (Hindi)
        'ek', 'teen', 'chaar', 'paanch', 'chhah', 'saat', 'aath', 'nau', 'das',
    }

    query_words = set(query_lower.split())
    if query_words & HINGLISH_WORDS:
        return "hinglish"

    # 3. Default: English
    return "english"


def translate_action_response(text, query):
    """
    Translates a marketplace response to match the user's language.
    Uses deterministic detection first — only calls the LLM if translation is needed.
    """
    lang = _detect_user_language(query)
    logger.info(f"  [TRANSLATE] Detected language: {lang} for query: '{query[:50]}'")

    # English queries → return text as-is, no LLM call needed
    if lang == "english":
        return text

    # Hindi or Hinglish → translate via LLM
    from llm_client import generate_text

    if lang == "hinglish":
        instruction = (
            "Translate SOURCE_TEXT into casual Hinglish (Hindi words written in English/Latin letters). "
            "NEVER use Devanagari script. Keep product names, prices, and emojis unchanged."
        )
    else:  # hindi
        instruction = (
            "Translate SOURCE_TEXT into Hindi (Devanagari script). "
            "Keep product names, prices, and emojis unchanged."
        )

    try:
        resp, _usage = generate_text(
            system_instruction=instruction,
            contents=text,
            temperature=0.0,
            max_output_tokens=4000,
            operation="Action Response Translation",
        )
        result_text = (resp.text or "").strip()
        if result_text.startswith("OUTPUT:"):
            result_text = result_text.replace("OUTPUT:", "", 1).strip()
        return result_text or text
    except Exception as e:
        logger.error(f"  [TRANSLATE] Failed to translate action response: {e}")
        return text

def core_chat_logic(data, admin, project_id):
    """
    Shared core logic for both /chat/v2 and /chat/v3.
    """
    query = data['query']
    phone = data.get('phone')
    raw_user_id = data.get('user_id') or phone
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
    # Otherwise, we generate a deterministic UUID if a phone number is provided,
    # or a random UUID if completely anonymous.
    if client_session_id:
        session_id = client_session_id
    else:
        import uuid
        if phone:
            NAMESPACE_PHONE = uuid.UUID('12345678-1234-5678-1234-567812345678')
            generated_uuid = uuid.uuid5(NAMESPACE_PHONE, str(phone).strip().lower())
            session_id = f"sess_{generated_uuid}"
        else:
            session_id = f"sess_{uuid.uuid4()}"

    # If raw_user_id (email/phone) is provided:
    #   We derive user_id from it via UUID5.
    # If raw_user_id is NOT provided:
    #   The session is anonymous (user_id = None).
    if raw_user_id:
        user_id = generate_user_uuid(raw_user_id)
    else:
        user_id = None

    logger.info(f"  [SESSION] Phone: {phone} -> sessionId: {session_id}, userId: {user_id}")

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
                    "localCart": [],
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

    if MARKETPLACE_ENABLED and marketplace_state and marketplace_state.get("current_step") not in (None, "idle"):
        current_step = marketplace_state["current_step"]
        # ===== HELPER: LLM Semantic Selection =====
        def resolve_selection_with_llm(user_input, options, type_name="option"):
            import json
            from llm_client import generate_text

            options_str = json.dumps([{"id": opt.get("index"), "label": opt.get("label", opt.get("name", str(opt))), "details": opt} for opt in options], default=str)
            prompt = f"You are an AI assistant helping to map a user's free-text utterance to one of the available {type_name} options.\n\nAvailable Options:\n{options_str}\n\nUser Utterance: \"{user_input}\"\n\nAnalyze the utterance. If it semantically matches one of the options, return a JSON object with a single key 'selected_id' containing the integer ID (the 'id' field). If the user is clearly changing the subject, asking a new question, or wanting to cancel (e.g. searching for a product, asking a general question), return {{\"selected_id\": \"BREAKOUT\"}}. Otherwise, if it doesn't match any option, return {{\"selected_id\": null}}."

            try:
                resp, _usage = generate_text(
                    contents=prompt,
                    temperature=0,
                    json_mode=True,
                    max_output_tokens=500,
                    operation="Semantic State Resolver",
                )
                parsed = json.loads(resp.text)
                selected_id = parsed.get("selected_id")
                if str(selected_id) == "BREAKOUT":
                    return "BREAKOUT"
                if selected_id:
                    for opt in options:
                        if opt.get("index") == selected_id:
                            return opt
            except Exception as e:
                logger.error(f"Failed to resolve selection with LLM: {e}")
            return None

        is_breakout = False

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
            return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, None

        elif current_step == "awaiting_otp":
            # The user's message IS the OTP
            from phase4_integrations.marketplace_auth import verify_otp_login
            result = verify_otp_login(query.strip(), session_id, session_obj)
            ai_text = result["message"]
            tenant_config = project_config

            ai_text = translate_action_response(ai_text, query)

            save_chat_message(session_id, "user", query)
            save_chat_message(session_id, "ai", ai_text)
            base_url = request.host_url.rstrip('/')
            return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, None

        # ===== CHECKOUT STATE: Address Selection =====
        elif current_step == "awaiting_address_selection":
            from database import update_marketplace_state
            from phase4_integrations.marketplace_auth import handle_marketplace_action
            addresses = marketplace_state.get("checkout_addresses", [])
            user_input = query.strip()
            
            selected_addr = None
            try:
                idx = int(user_input)
                for a in addresses:
                    if a["index"] == idx:
                        selected_addr = a
                        break
            except ValueError:
                for a in addresses:
                    if a.get("label", "").lower() in user_input.lower() or user_input.lower() in a.get("label", "").lower():
                        selected_addr = a
                        break
            
            if not selected_addr:
                selected_addr = resolve_selection_with_llm(user_input, addresses, "address")
                
            if selected_addr == "BREAKOUT":
                from database import update_marketplace_state
                update_marketplace_state(session_id, {"current_step": "idle"})
                is_breakout = True
            elif not selected_addr:
                ai_text = f"I couldn't match that to any address. Please reply with a number (1-{len(addresses)})."
                tenant_config = project_config
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, None
            
            if not is_breakout:
                import datetime as _dt
                today = _dt.date.today()
                date_options = [
                    {"date": today.isoformat(), "label": "Today", "index": 1},
                    {"date": (today + _dt.timedelta(days=1)).isoformat(), "label": "Tomorrow", "index": 2},
                    {"date": (today + _dt.timedelta(days=2)).isoformat(), "label": "Day After Tomorrow", "index": 3},
                    {"date": "__custom__", "label": "Choose a Custom Date", "index": 4},
                ]
                
                update_marketplace_state(session_id, {
                    "current_step": "awaiting_date_selection",
                    "selectedAddressId": selected_addr["id"],
                    "checkout_dates": date_options,
                })
                
                ai_text = "\U0001F4C5 *Select a Delivery Date:*\n\n"
                for opt in date_options:
                    if opt["date"] == "__custom__":
                        ai_text += f"*{opt['index']}. {opt['label']}*\n"
                    else:
                        ai_text += f"*{opt['index']}. {opt['label']}* ({opt['date']})\n"
                ai_text += "\nReply with the *number* of your preferred delivery date, or tell me the date in words (e.g. \"Thursday\", \"June 25th\")."
                
                buttons = [
                    {"id": "1", "title": "Today"},
                    {"id": "2", "title": "Tomorrow"},
                    {"id": "3", "title": "Day After Tomorrow"},
                ]
                wp_payload = {
                    "type": "session_quick_reply_with_text",
                    "media": {
                        "header": {"text": ""},
                        "body": ai_text,
                        "footer_text": "",
                        "button": buttons,
                    },
                }
                
                tenant_config = project_config
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, wp_payload

        # ===== CHECKOUT STATE: Date Selection =====
        elif current_step == "awaiting_date_selection":
            from database import update_marketplace_state
            from phase4_integrations.marketplace_auth import handle_marketplace_action
            date_options = marketplace_state.get("checkout_dates", [])
            user_input = query.strip()
            
            selected_date = None
            try:
                idx = int(user_input)
                for d in date_options:
                    if d["index"] == idx:
                        selected_date = d
                        break
            except ValueError:
                for d in date_options:
                    if d.get("label", "").lower() in user_input.lower() or user_input.lower() in d.get("label", "").lower():
                        selected_date = d
                        break
            
            if not selected_date:
                selected_date = resolve_selection_with_llm(user_input, date_options, "delivery date")
                
            if selected_date == "BREAKOUT":
                from database import update_marketplace_state
                update_marketplace_state(session_id, {"current_step": "idle"})
                is_breakout = True
            elif not selected_date:
                ai_text = f"I couldn't match that to any date. Please reply with a number (1-{len(date_options)}), or say the date in words."
                tenant_config = project_config
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, None
            
            if not is_breakout:
                if selected_date.get("date") == "__custom__":
                    update_marketplace_state(session_id, {
                        "current_step": "awaiting_custom_date",
                    })
                    ai_text = "\U0001F4C5 Please tell me the date you'd like delivery on.\n\n"
                    ai_text += "You can say things like:\n"
                    ai_text += "• *June 25*\n• *Thursday*\n• *25th of this month*\n• *next Monday*\n\n"
                    ai_text += "Or type the date as *YYYY-MM-DD* (e.g. 2026-06-25)."
                    tenant_config = project_config
                    save_chat_message(session_id, "user", query)
                    save_chat_message(session_id, "ai", ai_text)
                    base_url = request.host_url.rstrip('/')
                    return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, None
                
                import datetime as _dt
                selected_date_str = selected_date["date"]
                is_today = (selected_date_str == _dt.date.today().isoformat())
                now = _dt.datetime.now()
                
                ALL_SLOTS = [
                    {"name": "8 AM - 10 AM",  "startTime": "08:00", "endTime": "10:00"},
                    {"name": "10 AM - 12 PM", "startTime": "10:00", "endTime": "12:00"},
                    {"name": "12 PM - 2 PM",  "startTime": "12:00", "endTime": "14:00"},
                    {"name": "2 PM - 4 PM",   "startTime": "14:00", "endTime": "16:00"},
                    {"name": "4 PM - 6 PM",   "startTime": "16:00", "endTime": "18:00"},
                    {"name": "6 PM - 8 PM",   "startTime": "18:00", "endTime": "20:00"},
                ]
                
                available_slots = []
                for s in ALL_SLOTS:
                    if is_today:
                        slot_start = _dt.datetime.strptime(s["startTime"], "%H:%M").replace(
                            year=now.year, month=now.month, day=now.day
                        )
                        if slot_start <= now:
                            continue
                    available_slots.append(s)
                
                if not available_slots:
                    ai_text = "\u23F0 Sorry, all delivery slots for today have already passed. Please choose a different date."
                    update_marketplace_state(session_id, {
                        "current_step": "awaiting_date_selection",
                    })
                    tenant_config = project_config
                    save_chat_message(session_id, "user", query)
                    save_chat_message(session_id, "ai", ai_text)
                    base_url = request.host_url.rstrip('/')
                    return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, None
                
                slot_options = []
                ai_text = "\u23F0 *Available Delivery Slots:*\n\n"
                for idx, s in enumerate(available_slots, 1):
                    slot_options.append({
                        "id": f"local_slot_{idx}",
                        "name": s["name"],
                        "label": s["name"],
                        "timing": s["name"],
                        "startTime": s["startTime"],
                        "endTime": s["endTime"],
                        "index": idx,
                    })
                    ai_text += f"*{idx}. {s['name']}*\n"
                ai_text += "\nReply with the *number* of the slot you'd like, or say it in words (e.g. \"morning\", \"4 to 6\")."
                
                update_marketplace_state(session_id, {
                    "current_step": "awaiting_slot_selection",
                    "selectedDeliveryDate": selected_date_str,
                    "checkout_slots": slot_options,
                })
                
                buttons = []
                for opt in slot_options[:3]:
                    buttons.append({"id": str(opt["index"]), "title": opt["name"][:20]})
                wp_payload = {
                    "type": "session_quick_reply_with_text",
                    "media": {
                        "header": {"text": "Delivery Slots"},
                        "body": ai_text,
                        "footer_text": "",
                        "button": buttons,
                    },
                }
                
                tenant_config = project_config
                ai_text = translate_action_response(ai_text, query)
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, wp_payload

        # ===== CHECKOUT STATE: Custom Date Entry =====
        elif current_step == "awaiting_custom_date":
            from database import update_marketplace_state
            import datetime as _dt
            user_input = query.strip()
            
            parsed_date = None
            try:
                parsed_date = _dt.datetime.strptime(user_input, "%Y-%m-%d").date()
            except ValueError:
                pass
            
            if not parsed_date:
                for fmt in ["%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%B %d", "%b %d", "%d %B", "%d %b"]:
                    try:
                        d = _dt.datetime.strptime(user_input, fmt).date()
                        if d.year == 1900:
                            d = d.replace(year=_dt.date.today().year)
                        parsed_date = d
                        break
                    except ValueError:
                        continue
            
            if not parsed_date:
                import json
                from llm_client import generate_text
                try:
                    resp, _usage = generate_text(
                        contents=f"Today is {_dt.date.today().isoformat()} ({_dt.date.today().strftime('%A')}). The user said: \"{user_input}\". What date are they referring to? Return ONLY a JSON object with key 'date' in YYYY-MM-DD format. If the user is clearly changing the subject, asking a new question, or wanting to cancel (e.g. searching for a product), return {{\"date\": \"BREAKOUT\"}}. If you cannot determine a date, return {{\"date\": null}}.",
                        temperature=0,
                        json_mode=True,
                        max_output_tokens=200,
                        operation="Custom Date Parser",
                    )
                    result = json.loads(resp.text)
                    date_str = result.get("date")
                    if date_str == "BREAKOUT":
                        from database import update_marketplace_state
                        update_marketplace_state(session_id, {"current_step": "idle"})
                        parsed_date = "BREAKOUT"
                    elif date_str:
                        parsed_date = _dt.datetime.strptime(date_str, "%Y-%m-%d").date()
                except Exception as e:
                    logger.error(f"  [CUSTOM DATE] LLM parse failed: {e}")
            
            if parsed_date == "BREAKOUT":
                is_breakout = True
            elif not parsed_date or parsed_date < _dt.date.today():
                ai_text = "I couldn't understand that date, or it's in the past. Please try again.\n\nExamples: *June 25*, *Thursday*, *next Monday*, or *2026-06-25*."
                tenant_config = project_config
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, None
            
            if not is_breakout:
                selected_date_str = parsed_date.isoformat()
                is_today = (selected_date_str == _dt.date.today().isoformat())
                now = _dt.datetime.now()
                
                ALL_SLOTS = [
                    {"name": "8 AM - 10 AM",  "startTime": "08:00", "endTime": "10:00"},
                    {"name": "10 AM - 12 PM", "startTime": "10:00", "endTime": "12:00"},
                    {"name": "12 PM - 2 PM",  "startTime": "12:00", "endTime": "14:00"},
                    {"name": "2 PM - 4 PM",   "startTime": "14:00", "endTime": "16:00"},
                    {"name": "4 PM - 6 PM",   "startTime": "16:00", "endTime": "18:00"},
                    {"name": "6 PM - 8 PM",   "startTime": "18:00", "endTime": "20:00"},
                ]
                
                available_slots = []
                for s in ALL_SLOTS:
                    if is_today:
                        slot_start = _dt.datetime.strptime(s["startTime"], "%H:%M").replace(
                            year=now.year, month=now.month, day=now.day
                        )
                        if slot_start <= now:
                            continue
                    available_slots.append(s)
                
                if not available_slots:
                    ai_text = "\u23F0 Sorry, all delivery slots for today have already passed. Please choose a different date."
                    update_marketplace_state(session_id, {
                        "current_step": "awaiting_custom_date",
                    })
                    tenant_config = project_config
                    save_chat_message(session_id, "user", query)
                    save_chat_message(session_id, "ai", ai_text)
                    base_url = request.host_url.rstrip('/')
                    return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, None
                
                slot_options = []
                ai_text = f"\U0001F4C5 Delivery on *{parsed_date.strftime('%A, %B %d')}*\n\n\u23F0 *Available Delivery Slots:*\n\n"
                for idx, s in enumerate(available_slots, 1):
                    slot_options.append({
                        "id": f"local_slot_{idx}",
                        "name": s["name"],
                        "label": s["name"],
                        "timing": s["name"],
                        "startTime": s["startTime"],
                        "endTime": s["endTime"],
                        "index": idx,
                    })
                    ai_text += f"*{idx}. {s['name']}*\n"
                ai_text += "\nReply with the *number* of the slot you'd like."
                
                update_marketplace_state(session_id, {
                    "current_step": "awaiting_slot_selection",
                    "selectedDeliveryDate": selected_date_str,
                    "checkout_slots": slot_options,
                })
                
                buttons = []
                for opt in slot_options[:3]:
                    buttons.append({"id": str(opt["index"]), "title": opt["name"][:20]})
                wp_payload = {
                    "type": "session_quick_reply_with_text",
                    "media": {
                        "header": {"text": "Delivery Slots"},
                        "body": ai_text,
                        "footer_text": "",
                        "button": buttons,
                    },
                }
                
                tenant_config = project_config
                ai_text = translate_action_response(ai_text, query)
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, wp_payload

        # ===== CHECKOUT STATE: Slot Selection =====
        elif current_step == "awaiting_slot_selection":
            from database import update_marketplace_state
            from phase4_integrations.marketplace_auth import handle_marketplace_action
            slots = marketplace_state.get("checkout_slots", [])
            user_input = query.strip()
            
            selected_slot = None
            try:
                idx = int(user_input)
                for s in slots:
                    if s["index"] == idx:
                        selected_slot = s
                        break
            except ValueError:
                for s in slots:
                    if s.get("name", "").lower() in user_input.lower() or user_input.lower() in s.get("name", "").lower():
                        selected_slot = s
                        break
            
            if not selected_slot:
                selected_slot = resolve_selection_with_llm(user_input, slots, "delivery time slot")
                
            if selected_slot == "BREAKOUT":
                from database import update_marketplace_state
                update_marketplace_state(session_id, {"current_step": "idle"})
                is_breakout = True
            elif not selected_slot:
                ai_text = f"I couldn't match that to any slot. Please reply with a number (1-{len(slots)})."
                tenant_config = project_config
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, None
            
            if not is_breakout:
                selected_address_id = marketplace_state.get("selectedAddressId", "")
                update_marketplace_state(session_id, {
                    "current_step": "idle",
                    "selectedSlotId": selected_slot["id"],
                    "selectedSlotName": selected_slot.get("name", ""),
                    "selectedSlotTiming": selected_slot.get("timing", ""),
                })
                
                result = handle_marketplace_action(
                    session=session_obj,
                    session_id=session_id,
                    action_id="calculate_delivery_charge",
                    parameters={"customerDeliveryAddressId": selected_address_id}
                )
                ai_text = result["message"]
                wp_payload = result.get("whatsapp_payload")
                
                tenant_config = project_config
                ai_text = translate_action_response(ai_text, query)
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, wp_payload

        # ===== CHECKOUT STATE: Coupons or Place Order =====
        elif current_step == "awaiting_checkout_decision":
            from database import update_marketplace_state
            from phase4_integrations.marketplace_auth import handle_marketplace_action
            user_input = query.strip().lower()
            
            if any(w in user_input for w in ["coupon", "discount", "code", "coupons"]):
                update_marketplace_state(session_id, {"current_step": "idle"})
                result = handle_marketplace_action(
                    session=session_obj,
                    session_id=session_id,
                    action_id="list_coupons",
                    parameters={}
                )
                ai_text = result["message"]
                wp_payload = result.get("whatsapp_payload")
                tenant_config = project_config
                ai_text = translate_action_response(ai_text, query)
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, wp_payload
            elif any(w in user_input for w in ["place", "order", "yes", "proceed", "skip", "no coupon"]):
                update_marketplace_state(session_id, {"current_step": "idle"})
                result = handle_marketplace_action(
                    session=session_obj,
                    session_id=session_id,
                    action_id="create_order",
                    parameters={}
                )
                ai_text = result["message"]
                wp_payload = result.get("whatsapp_payload")
                tenant_config = project_config
                ai_text = translate_action_response(ai_text, query)
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, wp_payload
            else:
                update_marketplace_state(session_id, {"current_step": "idle"})
                is_breakout = True

        # ===== CHECKOUT STATE: Coupon Selection =====
        elif current_step == "awaiting_coupon_selection":
            from database import update_marketplace_state
            from phase4_integrations.marketplace_auth import handle_marketplace_action
            user_input = query.strip()
            
            if user_input.lower() in ("skip", "no", "no coupon", "none"):
                update_marketplace_state(session_id, {"current_step": "idle"})
                result = handle_marketplace_action(
                    session=session_obj,
                    session_id=session_id,
                    action_id="create_order",
                    parameters={}
                )
                ai_text = result["message"]
                wp_payload = result.get("whatsapp_payload")
                tenant_config = project_config
                ai_text = translate_action_response(ai_text, query)
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, wp_payload
            elif len(user_input.split()) > 2:
                # Likely a breakout statement rather than a coupon code
                update_marketplace_state(session_id, {"current_step": "idle"})
                is_breakout = True
            else:
                update_marketplace_state(session_id, {"current_step": "idle"})
                result = handle_marketplace_action(
                    session=session_obj,
                    session_id=session_id,
                    action_id="verify_coupon",
                    parameters={"couponCode": user_input}
                )
                ai_text = result["message"]
                wp_payload = result.get("whatsapp_payload")
                tenant_config = project_config
                ai_text = translate_action_response(ai_text, query)
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, wp_payload

        # ===== CHECKOUT STATE: Order Confirmation =====
        elif current_step == "awaiting_order_confirmation":
            from database import update_marketplace_state
            from phase4_integrations.marketplace_auth import handle_marketplace_action
            user_input = query.strip().lower()
            
            if user_input in ("yes", "y", "confirm", "ha", "haan", "ok", "place order", "place", "proceed"):
                update_marketplace_state(session_id, {"current_step": "idle"})
                result = handle_marketplace_action(
                    session=session_obj,
                    session_id=session_id,
                    action_id="create_order",
                    parameters={}
                )
                ai_text = result["message"]
                wp_payload = result.get("whatsapp_payload")
            elif user_input in ("no", "cancel", "abort", "nahi"):
                update_marketplace_state(session_id, {"current_step": "idle"})
                ai_text = "Order cancelled. No worries! Your items are still in the cart if you change your mind. 🛒"
                wp_payload = None
            else:
                update_marketplace_state(session_id, {"current_step": "idle"})
                is_breakout = True
            
            if not is_breakout:
                tenant_config = project_config
                ai_text = translate_action_response(ai_text, query)
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, wp_payload

        elif current_step == "awaiting_cart_confirmation":
            # The user is responding to "Add X to your cart? Yes/No"
            from database import update_marketplace_state
            answer = query.strip().lower()
            is_yes = answer in ("yes", "yes ✅", "confirm_cart_yes", "y", "ha", "haan", "yes ✅")
            is_no = answer in ("no", "no ❌", "confirm_cart_no", "n", "nahi", "nah", "cancel", "no ❌")

            if is_yes:
                pending_cart = marketplace_state.get("pending_cart", {})
                if not pending_cart:
                    update_marketplace_state(session_id, {"current_step": "idle", "pending_cart": None})
                    ai_text = "Something went wrong — I lost the cart data. Please try adding the item again."
                    tenant_config = project_config
                    save_chat_message(session_id, "user", query)
                    save_chat_message(session_id, "ai", ai_text)
                    base_url = request.host_url.rstrip('/')
                    return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, None

                from phase4_integrations.marketplace_auth import execute_confirmed_cart_add
                result = execute_confirmed_cart_add(session_obj, session_id, pending_cart)
                ai_text = result["message"]
                wp_payload = result.get("whatsapp_payload")
            elif is_no:
                update_marketplace_state(session_id, {"current_step": "idle", "pending_cart": None})
                ai_text = "No problem! Let me know if you'd like to browse more products. 🛍️"
                wp_payload = None
            else:
                # User said something else — treat it as a new query, reset state
                update_marketplace_state(session_id, {"current_step": "idle", "pending_cart": None})
                # Fall through to intent routing below
                pass

            if is_yes or is_no:
                tenant_config = project_config
                ai_text = translate_action_response(ai_text, query)
                if wp_payload and isinstance(wp_payload, dict) and "media" in wp_payload and "body" in wp_payload["media"]:
                    wp_payload["media"]["body"] = translate_action_response(wp_payload["media"]["body"], query)
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, wp_payload
    # ===== GLOBAL GREETING INTERCEPTOR =====
    # If the user says a simple greeting, we handle it directly rather than letting RAG fail.
    is_greeting = query.strip().lower() in ["hi", "hello", "hey", "start", "menu", "/start", "hii", "hiii"]
    if is_greeting:
        from phase4_integrations.intent_router import get_connected_services
        has_marketplace = MARKETPLACE_ENABLED and any(s["serviceId"] == "marketplace" for s in get_connected_services(project_id))

        if has_marketplace:
            from phase4_integrations.marketplace_auth import check_marketplace_auth, update_marketplace_state
            if not check_marketplace_auth(session_obj):
                # User isn't logged in — force the auth flow
                update_marketplace_state(session_id, {
                    "current_step": "awaiting_email",
                    "pending_action": None,
                    "pending_parameters": None,
                })
                ai_text = (
                    "To help you with shopping, I need to verify your identity first.\n\n"
                    "Please share your *email address* to get started."
                )
            else:
                # User is already logged in
                ai_text = "Hello! 👋 You are logged in and ready to shop. What are you looking for today?"

            tenant_config = project_config
            ai_text = translate_action_response(ai_text, query)
            save_chat_message(session_id, "user", query)
            save_chat_message(session_id, "ai", ai_text)
            base_url = request.host_url.rstrip('/')
            return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, None

    # ===== PHASE 4: INTENT ROUTING (Action vs RAG) =====
    from phase4_integrations.intent_router import route_intent
    from phase4_integrations.executor import execute_action, format_action_result

    intent = route_intent(query, project_id, project_config, chat_history)

    if intent["type"] == "action":
        # ── Marketplace actions → dedicated handler ──
        if MARKETPLACE_ENABLED and intent["service_id"] == "marketplace":
            from phase4_integrations.marketplace_auth import handle_marketplace_action
            result = handle_marketplace_action(
                session=session_obj,
                session_id=session_id,
                action_id=intent["action_id"],
                parameters=intent["parameters"]
            )
            ai_text = result["message"]
            wp_payload = result.get("whatsapp_payload")
        else:
            # ── All other integrations → generic executor ──
            result = execute_action(
                project_id=project_id,
                service_id=intent["service_id"],
                action_id=intent["action_id"],
                parameters=intent["parameters"]
            )
            ai_text = format_action_result(result)
            wp_payload = None

        tenant_config = project_config

        ai_text = translate_action_response(ai_text, query)
        if wp_payload and isinstance(wp_payload, dict) and "media" in wp_payload and "body" in wp_payload["media"]:
            wp_payload["media"]["body"] = translate_action_response(wp_payload["media"]["body"], query)

        # Save Turn
        save_chat_message(session_id, "user", query)
        save_chat_message(session_id, "ai", ai_text)

        base_url = request.host_url.rstrip('/')
        return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, wp_payload
    # ===== GLOBAL AUTH GATE FOR MARKETPLACE BOTS =====
    # Catch any query that fell through to RAG. If marketplace is connected,
    # the user MUST be logged in to chat.
    from phase4_integrations.intent_router import get_connected_services
    has_marketplace = MARKETPLACE_ENABLED and any(s["serviceId"] == "marketplace" for s in get_connected_services(project_id))

    if has_marketplace:
        from phase4_integrations.marketplace_auth import check_marketplace_auth, update_marketplace_state
        if not check_marketplace_auth(session_obj):
            update_marketplace_state(session_id, {
                "current_step": "awaiting_email",
                "pending_action": None,
                "pending_parameters": None,
            })
            ai_text = (
                "To help you with shopping, I need to verify your identity first.\n\n"
                "Please share your *email address* to get started."
            )
            tenant_config = project_config
            ai_text = translate_action_response(ai_text, query)
            save_chat_message(session_id, "user", query)
            save_chat_message(session_id, "ai", ai_text)
            base_url = request.host_url.rstrip('/')
            return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, None

    # ===== STANDARD RAG FLOW =====

    # 1. Detect/Translate
    from phase2_retrieval.rag_logic import detect_query_language
    from database import get_knowledge_base_language
    detected_lang = detect_query_language(query)
    kb_language = get_knowledge_base_language(kb_id)
    
    search_query = query
    if detected_lang != kb_language:
        from llm_client import generate_text
        try:
            translate_resp, _usage = generate_text(
                system_instruction=f"Translate the following text to {kb_language}. Return ONLY the translation.",
                contents=query,
                temperature=0.0,
                max_output_tokens=1000,
                operation="Query Translation",
            )
            translated = (translate_resp.text or "").strip()
            if translated:
                search_query = translated
        except Exception as e:
            # Translation is an optimization, not a requirement — fall back to
            # the original query rather than failing the whole request.
            logger.error(f"  [RAG] Query translation failed, using original query: {e}")

    # 2. Retrieval (tenant-scoped vector search, then hybrid re-rank)
    from phase2_retrieval.rag_logic import generate_query_embedding, EmbeddingGenerationError, rerank_chunks
    from database import perform_semantic_retrieval
    try:
        query_embedding = generate_query_embedding(search_query)
    except EmbeddingGenerationError as e:
        logger.error(f"  [RETRIEVAL] Embedding generation failed: {e}")
        return "I'm having trouble reaching the AI service right now — please try again in a moment.", [], project_config, "", session_id, is_expired, project_id, None

    candidates = perform_semantic_retrieval(query_embedding, kb_id, n=4)
    chunks = rerank_chunks(search_query, candidates, n=4)

    if not chunks:
        return "I'm sorry, I couldn't find any information.", [], project_config, "", session_id, is_expired, project_id, None

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
    return ai_text, selected_image_ids, tenant_config, base_url, session_id, is_expired, project_id, None


@app.route('/chat/v2', methods=['POST'])
@project_key_required
@limiter.limit("30 per minute", key_func=lambda: getattr(request, "project_id", None) or get_remote_address())
def chat_v2(admin, project_id):
    """Phase 2: Standard Reply Array output."""
    try:
        data = request.json
        if not data or 'query' not in data:
            abort(400, description="Missing compulsory fields (query).")

        ai_text, selected_image_ids, tenant_config, base_url, session_id, is_expired, project_id, _wp = core_chat_logic(data, admin, project_id)

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
@limiter.limit("30 per minute", key_func=lambda: getattr(request, "project_id", None) or get_remote_address())
def chat_v3(admin, project_id):
    """Phase 3: Agentic Session Message output."""
    try:
        data = request.json
        if not data or 'query' not in data:
            abort(400, description="Missing compulsory fields (query).")

        ai_text, selected_image_ids, tenant_config, base_url, session_id, is_expired, project_id, wp_payload = core_chat_logic(data, admin, project_id)

        # If marketplace returned a pre-built WhatsApp payload, use it directly
        if wp_payload is not None:
            user_info = {"name": data.get("user_name", "User"), "phone": data.get("user_id", "")}
            tag_ids = data.get("tag_ids", [])
            agentic_payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": user_info.get("phone", ""),
                "type": wp_payload["type"],
                "name": user_info.get("name", ""),
                "phone": user_info.get("phone", ""),
                "enable_acculync": True,
                "tagIds": tag_ids,
                "media": wp_payload["media"],
            }
            agentic_payload["session_id"] = session_id
            return jsonify(agentic_payload)

        # Prepare info for formatter
        image_urls = [generate_image_url(base_url, img_id) for img_id in selected_image_ids]
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
        logger.error(f"Chat v3 Error: {e}")
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


# ===== PUBLIC DEMO API =====
# Registered last so it can import from this module without a circular import
# at definition time.
from demo_api import demo_bp
app.register_blueprint(demo_bp)
limiter.limit("60 per minute")(demo_bp)


if __name__ == "__main__":
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
