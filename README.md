# Multi-Tenant RAG Engine with Precision Image Mapping

A high-performance RAG (Retrieval-Augmented Generation) engine built with Python, Flask, and MongoDB. This system is designed to handle multiple distinct knowledge bases (Tenants) simultaneously, featuring a "Physical-First" image retrieval strategy that ensures perfect alignment between text and media.

## 🚀 Key Features

*   **Multi-Tenant Architecture**: Isolate data across multiple industries (Tourism, Healthcare, Education) using a unified `knowledge_base_id` filtering system.
*   **Physical-First Structural Mapping**: Uses PDF Y-coordinates to anchor images to their correct semantic headings, solving the common "misaligned image" problem in RAG pipelines.
*   **Hierarchical Chunking**: Automatically detects H1 (Topic) and H2 (Sub-topic) boundaries to maintain document structure during processing.
*   **Keyword-Based Image Re-ranking**: A custom scoring layer that ranks retrieved images by their keyword relevance to the user's query.
*   **Interactive Button Support**: WhatsApp-style response formatting with dynamic interactive buttons (e.g., "Book Tour", "Contact Admissions") tailored to the tenant's persona.
*   **GridFS Storage**: Securely handles large PDF files and high-resolution images within MongoDB.

## 🛠 Tech Stack

*   **Backend**: Python / Flask
*   **LLM**: Google Gemini (via `google-generativeai`)
*   **Database**: MongoDB Atlas + GridFS
*   **PDF Processing**: PyMuPDF (fitz)
*   **Indexing**: MongoDB $text Search with Weighted Field Support

## 📋 Architecture Overview

1.  **Ingestion Layer**: `pdf_parser.py` extracts text and images -> `chunker.py` performs hierarchical grouping and physical anchoring.
2.  **Storage Layer**: MongoDB stores semantic chunks with `knowledge_base_id` tags.
3.  **Retrieval Layer**: `database.py` performs filtered full-text search.
4.  **Intelligence Layer**: `rag_logic.py` applies tenant-specific personas and guardrails before generating responses via Gemini.

## 🚦 Getting Started

### 1. Installation
```bash
pip install -r requirements.txt
```

### 2. Configuration (.env)
Create a `.env` file in the root directory:
```env
MONGODB_URI=your_mongodb_atlas_uri
MONGODB_DB_NAME=rag_db
GOOGLE_API_KEY=your_gemini_api_key
```

### 3. Run the Server
```bash
python server.py
```

## 🧪 API Documentation

### POST `/upload/pdf`
Uploads and processes a knowledge base.
*   **Body (form-data)**: 
    *   `file`: The PDF document.
    *   `knowledge_base_id`: The tenant identifier (e.g., `tourism`).

### POST `/chat`
Retrieves answers and relevant media.
*   **Body (JSON)**:
    ```json
    {
      "query": "Is scuba available?",
      "knowledge_base_id": "tourism"
    }
    ```

### GET `/image/<image_id>`
Serves extracted images directly from GridFS.

---
Developed by Shlok Dhanokar
