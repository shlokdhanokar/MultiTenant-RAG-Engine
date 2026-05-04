import os
from flask import Flask, request, jsonify, abort, Response
from pymongo import MongoClient
from gridfs import GridFS
from dotenv import load_dotenv
import bson

from phase1_upload.pdf_parser import analyze_document_layout
from phase1_upload.chunker import group_content_by_topic, generate_semantic_chunks, map_images_to_chunks
from database import perform_semantic_retrieval
from phase2_retrieval.rag_logic import generate_rag_response

# Load environment variables from .env in this folder
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

MONGODB_URI = os.getenv("MONGODB_URI")
if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI not set in .env")

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


def upload_images_to_gridfs(images, source_filename, fs):
    """
    Iterate through the extracted images, upload the raw byte streams to GridFS,
    and capture the generated ObjectId for each successful upload.

    Returns:
        A list of dicts: [{page_number, gridfs_id}, ...]
    """
    uploaded = []
    for img in images:
        img_filename = f"{source_filename}_page{img['page_number']}.{img['image_ext']}"
        gridfs_id = fs.put(
            img["image_bytes"],
            filename=img_filename,
        )
        uploaded.append({
            "page_number": img["page_number"],
            "gridfs_id": str(gridfs_id),
            "bbox": img.get("bbox")
        })
    return uploaded


def construct_chunk_metadata(chunk, source_file, source_file_id, knowledge_base_id, language="en"):
    """
    Attach crucial metadata to every generated chunk, including:
    knowledge_base_id, topic_name, page_number, chunk_index, and language.
    """
    return {
        "knowledge_base_id": knowledge_base_id,
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


def format_mongodb_documents(chunks, source_file, source_file_id, knowledge_base_id, language="en"):
    """
    Format the semantically chunked text and their associated metadata
    (including the newly generated GridFS image_ids) into BSON-compatible
    JSON objects ready for insert_many().
    """
    docs = []
    for chunk in chunks:
        doc = construct_chunk_metadata(
            chunk, source_file, source_file_id, knowledge_base_id, language
        )
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


@app.route('/health', methods=['GET'])
def health():
    try:
        client.admin.command('ping')
        return jsonify({"status": "ok"})
    except Exception as exc:
        abort(500, description=str(exc))


@app.route('/upload/pdf', methods=['POST'])
def upload_pdf():
    if 'file' not in request.files:
        abort(400, description='No file part in the request')
    file = request.files['file']
    if file.filename == '' or not file.filename.lower().endswith('.pdf'):
        abort(415, description='Only PDF files are accepted')

    pdf_bytes = file.read()

    # Derive knowledge_base_id from filename (e.g. "education.pdf" -> "education")
    knowledge_base_id = os.path.splitext(file.filename)[0].lower().replace(" ", "_")

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

    # ---- Step 5: upload_images_to_gridfs() ----
    uploaded_images = upload_images_to_gridfs(layout["images"], file.filename, fs)

    # ---- Step 6: map_images_to_chunks() ----
    chunks = map_images_to_chunks(chunks, uploaded_images)

    # ---- Step 7: format_mongodb_documents() (calls construct_chunk_metadata internally) ----
    chunk_docs = format_mongodb_documents(
        chunks, file.filename, raw_file_id, knowledge_base_id
    )

    # ---- Step 8: bulk_insert_chunks() ----
    inserted_count = bulk_insert_chunks(chunk_docs)

    return jsonify({
        "message": "PDF processed and chunked successfully",
        "knowledge_base_id": knowledge_base_id,
        "raw_file_id": str(raw_file_id),
        "total_pages": layout["total_pages"],
        "chunks_created": inserted_count,
        "images_extracted": len(uploaded_images),
    }), 201


def format_whatsapp_payload(ai_text, chunks, tenant_config, base_url, query=""):
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
    
    # Score each image by how many query keywords appear in its parent chunk's text
    # This ensures the scuba image ranks highest for a scuba query, regardless of MongoDB's text score
    query_words = set(word.lower() for word in query.split() if len(word) > 3)  # skip short words like "is", "at"
    
    scored_images = []  # list of (score, image_id)
    seen = set()
    for chunk in chunks:
        chunk_text_lower = chunk.get("text", "").lower()
        # Count how many query keywords appear in this chunk
        keyword_hits = sum(1 for w in query_words if w in chunk_text_lower)
        for img_id in chunk.get("associated_image_ids", []):
            if img_id not in seen:
                scored_images.append((keyword_hits, img_id))
                seen.add(img_id)
    
    # Sort by keyword relevance (highest first), take top 2 but ONLY if they have relevance
    scored_images.sort(key=lambda x: x[0], reverse=True)
    for score, img_id in scored_images[:2]:
        if score > 0:  # Only include images with actual keyword relevance
            payload["reply"].append({
                "type": "image",
                "url": f"{base_url}/image/{img_id}"
            })
                
    # Add interactive buttons from the tenant config
    if tenant_config.get("buttons"):
        payload["reply"].append({
            "type": "buttons",
            "options": tenant_config["buttons"]
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


@app.route('/chat', methods=['POST'])
def chat():
    """
    Main Chat endpoint: Retrieval -> AI Generation -> WhatsApp Formatting.
    """
    try:
        data = request.json
        if not data or 'query' not in data or 'knowledge_base_id' not in data:
            abort(400, description="Missing 'query' or 'knowledge_base_id'")
            
        query = data['query']
        kb_id = data['knowledge_base_id']
        
        # 1. perform_semantic_retrieval()
        chunks = perform_semantic_retrieval(query, kb_id, n=4)
        
        # DEBUG LOGS
        print(f"\n--- DEBUG: Retrived {len(chunks)} chunks for query: '{query}' ---")
        for i, c in enumerate(chunks):
            print(f"[{i}] Topic: {c['topic_name']} | Score: {c.get('score')}")
        
        if not chunks:
            return jsonify({
                "reply": [{"type": "text", "content": "I'm sorry, I couldn't find any information about that in the knowledge base."}]
            })
        
        # 2. generate_rag_response()
        ai_text, tenant_config = generate_rag_response(query, chunks, kb_id)
        
        # 3. format_whatsapp_payload()
        base_url = request.host_url.rstrip('/')
        response_payload = format_whatsapp_payload(ai_text, chunks, tenant_config, base_url, query)
        
        return jsonify(response_payload)
    except Exception as e:
        print(f"CHAT ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


if __name__ == "__main__":
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
