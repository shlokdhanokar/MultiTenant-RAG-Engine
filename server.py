import os
from flask import Flask, request, jsonify, abort
from pymongo import MongoClient
from gridfs import GridFS
from dotenv import load_dotenv

from phase1_upload.pdf_parser import analyze_document_layout
from phase1_upload.chunker import group_content_by_topic, generate_semantic_chunks, map_images_to_chunks

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


if __name__ == "__main__":
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
