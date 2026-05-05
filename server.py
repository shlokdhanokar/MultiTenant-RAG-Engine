import os
import io
import time
from flask import Flask, request, jsonify, abort, Response
from pymongo import MongoClient
from gridfs import GridFS
from dotenv import load_dotenv
import bson
import google.generativeai as genai
from PIL import Image

from phase1_upload.pdf_parser import analyze_document_layout
from phase1_upload.chunker import group_content_by_topic, generate_semantic_chunks, map_images_to_chunks
from database import perform_semantic_retrieval, store_image_caption_and_vector, perform_image_vector_search

# Load environment variables from .env in this folder
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

MONGODB_URI = os.getenv("MONGODB_URI")
if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI not set in .env")

# Configure Gemini API
genai.configure(api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))

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


def generate_image_caption(image_bytes, image_ext="jpeg"):
    """
    Send raw image bytes directly to Gemini Vision (bypassing PIL)
    and get a descriptive caption.
    """
    try:
        # Map common extensions to MIME types
        ext_to_mime = {
            "jpeg": "image/jpeg", "jpg": "image/jpeg",
            "png": "image/png", "webp": "image/webp",
            "gif": "image/gif", "bmp": "image/bmp",
        }
        mime_type = ext_to_mime.get(image_ext.lower(), "image/jpeg")
        
        model = genai.GenerativeModel('gemini-2.0-flash')
        response = model.generate_content([
            "Describe this image in 1-2 concise sentences. Focus on the main subject and activity shown.",
            {"mime_type": mime_type, "data": image_bytes}
        ])
        return response.text.strip()
    except Exception as e:
        print(f"Caption generation failed: {e}")
        import traceback
        traceback.print_exc()
        return "Image content not available"


def generate_text_embedding(text):
    """
    Convert text into a 768-dimensional vector using Gemini Embeddings.
    """
    try:
        result = genai.embed_content(
            model="models/text-embedding-004",
            content=text,
            task_type="retrieval_document"
        )
        return result['embedding']
    except Exception as e:
        print(f"Embedding generation failed: {e}")
        return [0.0] * 768  # Fallback zero vector


def upload_images_to_gridfs(images, source_filename, fs, knowledge_base_id=""):
    """
    Iterate through the extracted images, upload the raw byte streams to GridFS,
    generate AI captions and embeddings, and store them in MongoDB.
    Includes rate-limit safety pauses.
    """
    uploaded = []
    for i, img in enumerate(images):
        if i > 0:
            print(f"  [Rate Limit Safety] Pausing for 2 seconds before image {i+1}...")
            time.sleep(2)

        img_filename = f"{source_filename}_page{img['page_number']}.{img['image_ext']}"
        gridfs_id = fs.put(
            img["image_bytes"],
            filename=img_filename,
        )
        gridfs_id_str = str(gridfs_id)

        # Generate AI caption with simple retry logic
        caption = "Image content not available"
        for attempt in range(2):
            try:
                caption = generate_image_caption(img["image_bytes"], img.get("image_ext", "jpeg"))
                if caption != "Image content not available":
                    break
            except Exception as e:
                if "429" in str(e) and attempt == 0:
                    print("  [Rate Limit] Hit limit, retrying in 5 seconds...")
                    time.sleep(5)
                else:
                    break
        
        print(f"  [Caption] {img_filename}: {caption}")
        
        # Generate Vector Embedding for the caption
        embedding = generate_text_embedding(caption)
        
        # Store in MongoDB image_captions collection
        store_image_caption_and_vector(gridfs_id_str, caption, embedding, knowledge_base_id, source_filename)

        uploaded.append({
            "page_number": img["page_number"],
            "gridfs_id": gridfs_id_str,
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
    uploaded_images = upload_images_to_gridfs(layout["images"], file.filename, fs, knowledge_base_id)

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
    
    # Add images identified via Vector Search
    for img_id in selected_image_ids:
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
        
        # 1. perform_semantic_retrieval() for Text Context
        chunks = perform_semantic_retrieval(query, kb_id, n=4)
        
        # DEBUG LOGS
        print(f"\n--- DEBUG: Retrieved {len(chunks)} chunks for query: '{query}' ---")
        for i, c in enumerate(chunks):
            print(f"[{i}] Topic: {c['topic_name']} | Score: {c.get('score')}")
        
        if not chunks:
            return jsonify({
                "reply": [{"type": "text", "content": "I'm sorry, I couldn't find any information about that in the knowledge base."}]
            })
            
        # 2. Vector Search for Images
        # Convert user query to embedding for retrieval
        query_embedding = generate_text_embedding(query)
        # Search for top 2 closest image captions
        image_results = perform_image_vector_search(query_embedding, kb_id, limit=2)
        
        selected_image_ids = []
        for img_doc in image_results:
            # Only include images with a reasonable similarity score to avoid random matches
            score = img_doc.get("score", 0)
            print(f"  [Vector Search] Found image {img_doc['gridfs_id']} with score: {score:.3f} | Caption: {img_doc['caption']}")
            if score > 0.65:
                selected_image_ids.append(img_doc['gridfs_id'])
        
        # 3. generate_rag_response() for text answer
        # Note: We pass the chunks for context as usual
        from phase2_retrieval.rag_logic import generate_rag_response
        ai_text, tenant_config = generate_rag_response(query, chunks, kb_id)
        
        # 4. format_whatsapp_payload()
        base_url = request.host_url.rstrip('/')
        response_payload = format_whatsapp_payload(ai_text, selected_image_ids, tenant_config, base_url)
        
        return jsonify(response_payload)
    except Exception as e:
        print(f"CHAT ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


if __name__ == "__main__":
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
