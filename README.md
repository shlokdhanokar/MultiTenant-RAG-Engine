<div align="center">
  
# 🧠 Multi-Tenant RAG Engine
### Enterprise-Grade, Media-Aware Retrieval-Augmented Generation

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-Backend-green?style=for-the-badge&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![MongoDB](https://img.shields.io/badge/MongoDB-Atlas-47A248?style=for-the-badge&logo=mongodb&logoColor=white)](https://mongodb.com)
[![Gemini](https://img.shields.io/badge/Google-Gemini_AI-orange?style=for-the-badge&logo=google&logoColor=white)](https://ai.google.dev/)
[![Integrations](https://img.shields.io/badge/Integrations-Ready-8A2BE2?style=for-the-badge&logo=webhooks&logoColor=white)](#-app-integration-ecosystem)

A professional-grade, scalable RAG pipeline engineered to handle multiple distinct knowledge bases simultaneously. Built for precision, it features a revolutionary **"Physical-First"** image mapping strategy to guarantee pixel-perfect alignment between retrieved text and its associated media. Beyond basic RAG, this engine serves as a dynamic AI hub capable of interfacing with a wide range of external tools and APIs.

[Explore Features](#-core-innovations) • [Integration Ecosystem](#-app-integration-ecosystem) • [View Architecture](#-architecture) • [Getting Started](#-quick-start)

</div>
--


## ✨ Core Innovations

### 🏢 True Multi-Tenancy
Build once, serve many. Our architecture uses strict `knowledge_base_id` boundaries. A single unified engine can securely power a "Travel Guide" for tourism, a "Property Agent" for real estate, and a "Health Assistant" for hospitals—all with fully isolated data and customized AI personas.

### 📍 Physical-First Image Mapping
Traditional PDF parsers lose context when extracting images. We built a custom algorithm that records the exact **Y-Coordinate** of every heading and image. Images are dynamically "anchored" to the text physically appearing above them, entirely eliminating the "leaking images" problem.

### 🎯 Contextual & Keyword-Scored Retrieval
We don't just rely on standard vector or text search. Images and document chunks are re-ranked in real-time based on **query keyword density** within their parent chunks, ensuring the most semantically relevant media is always prioritized in the generative AI output.

---

## 🔗 App Integration Ecosystem

The RAG Engine isn't just about reading documents; it's designed to take action. The architecture supports a seamless plug-and-play ecosystem for external applications. 

By integrating function calling and intelligent intent routing, the engine can interact with third-party APIs to execute real-world tasks, including but not limited to:
- 📅 **Scheduling:** Calendly, Google Calendar
- 🛒 **E-Commerce:** Custom Marketplaces, Shopify
- 💬 **Communication:** Slack, WhatsApp, Microsoft Teams
- 📊 **CRM & Data:** Salesforce, HubSpot

*Note: The engine uses intelligent routing to decide whether a user's prompt requires searching the RAG knowledge base or triggering an integrated application webhook.*

---

## 🏗️ System Architecture

```mermaid
graph TD
    subgraph Client [Client Interface]
        A[PDF Document Upload]
        B[User Chat Query]
    end

    subgraph Ingestion Pipeline
        C[PDF Parser]
        D[Semantic Chunker]
        E[Physical Y-Coord Mapper]
    end

    subgraph Database Layer
        F[(MongoDB Chunks)]
        G[(GridFS Media)]
    end

    subgraph Retrieval & Action Pipeline
        H{Intent Router}
        I[Weighted Text Search]
        J[Keyword Relevance Scorer]
        K[Gemini AI Generator]
        L[Third-Party App Integrations]
    end

    A --> C
    C -- "Extract Text & Images" --> D
    D -- "Group by H1/H2" --> E
    E -- "Anchor Images" --> F
    E -- "Store Binary" --> G

    B --> H
    H -- "General Knowledge" --> I
    H -- "External Task" --> L
    I -- "Fetch Context" --> J
    J -- "Rank Media" --> K
    K -- "Generate Payload" --> Client
    L -- "Execute Action" --> Client
```

---

## 🚀 Quick Start

### Prerequisites
- Python 3.9+
- MongoDB Atlas cluster
- Google Gemini API Key

### 1. Installation
Clone the repository and install dependencies:
```bash
git clone https://github.com/shlokdhanokar/MultiTenant-RAG-Engine.git
cd MultiTenant-RAG-Engine
pip install -r requirements.txt
```

### 2. Configuration
Create a `.env` file in the project root:
```env
MONGODB_URI=your_mongodb_atlas_connection_string
MONGODB_DB_NAME=rag_db
GOOGLE_API_KEY=your_gemini_api_key
```

### 3. Launch the Server
```bash
python server.py
```
> The server will start locally on `http://localhost:8000`

---

## 🔌 API Reference

### 📤 1. Ingest Knowledge Base
`POST /upload/pdf`

Upload a PDF and assign it to a specific tenant.
- **Content-Type**: `multipart/form-data`
- **Body**:
  - `file`: (File) The PDF document.
  - `knowledge_base_id`: (Text) e.g., `tourism`

### 💬 2. Query the Engine
`POST /chat`

Retrieve context-aware AI answers with injected media and interactive buttons.
- **Content-Type**: `application/json`
- **Body**:
  ```json
  {
      "query": "Is scuba available at your resort?",
      "knowledge_base_id": "tourism"
  }
  ```

### 🖼️ 3. Fetch Media
`GET /image/<image_id>`

Directly serve high-resolution images stored in MongoDB GridFS.

---

<div align="center">
  <p>Engineered with ❤️ by <b>Shlok Dhanokar</b></p>
</div>
