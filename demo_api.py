"""
Public demo API powering the showcase UI.

These endpoints are deliberately unauthenticated: the UI is a public
demonstration, and requiring visitors to obtain an API key defeats the point.
Safety comes from scope rather than auth — the routes only ever touch projects
explicitly registered as demo projects, they are aggressively rate limited, and
visitor uploads land in ephemeral projects that expire.

Beyond answering questions, most endpoints also return the pipeline's internal
state (candidate scores, stage timings, token accounting) so the UI can show
how the answer was produced rather than just the answer.
"""
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, jsonify, request

from database import db

logger = logging.getLogger(__name__)

demo_bp = Blueprint("demo", __name__, url_prefix="/api/demo")

# Visitor-created knowledge bases are disposable; without a TTL the demo
# database would grow without bound.
EPHEMERAL_TTL_HOURS = 6
MAX_CANDIDATES_SHOWN = 15
TOP_N = 4


def _demo_projects():
    """Curated demo tenants, newest first."""
    return list(db["adminprojects"].find(
        {"isDemo": True},
        {"_id": 0, "projectId": 1, "projectName": 1, "projectDescription": 1,
         "demoIcon": 1, "demoSampleQuestions": 1, "projectInstruction": 1,
         "projectGuardrails": 1, "isEphemeral": 1},
    ).sort("createdAt", 1))


def _resolve_project(project_id):
    """Only demo projects are reachable through this API."""
    project = db["adminprojects"].find_one({"projectId": project_id, "isDemo": True})
    if not project:
        abort(404, description="Unknown or non-demo project")
    if project.get("isEphemeral") and project.get("expiresAt"):
        expires = project["expiresAt"]
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            abort(410, description="This temporary knowledge base has expired. Please upload again.")
    return project


@demo_bp.route("/tenants", methods=["GET"])
def list_tenants():
    """
    Backs the multi-tenant switcher: several isolated knowledge bases served by
    one engine, each with its own persona, guardrails and documents.
    """
    projects = _demo_projects()
    for p in projects:
        p["chunkCount"] = db["chunks"].count_documents({"knowledge_base_id": p["projectId"]})
        p["documents"] = db["chunks"].distinct("source_file", {"knowledge_base_id": p["projectId"]})
    return jsonify({"tenants": projects})


@demo_bp.route("/chunks/<project_id>", methods=["GET"])
def list_chunks(project_id):
    """
    Chunk explorer: how a document was split, the heading hierarchy that drove
    the split, and which images anchored to each chunk.
    """
    _resolve_project(project_id)
    chunks = list(db["chunks"].find(
        {"knowledge_base_id": project_id},
        {"_id": 0, "embedding": 0},
    ).sort("chunk_index", 1))

    for c in chunks:
        c["imageUrls"] = [_signed_image_url(i) for i in c.get("associated_image_ids", [])]

    by_source = {}
    for c in chunks:
        by_source.setdefault(c.get("source_file", "unknown"), []).append(c)

    return jsonify({
        "projectId": project_id,
        "totalChunks": len(chunks),
        "documents": [
            {
                "sourceFile": name,
                "chunkCount": len(items),
                "totalWords": sum(i.get("word_count", 0) for i in items),
                "chunks": items,
            }
            for name, items in by_source.items()
        ],
    })


def _signed_image_url(image_id):
    """
    Host-relative, unlike the WhatsApp path which needs an absolute URL because
    Meta's servers fetch it themselves. Here the consumer is the demo UI in a
    browser, and that UI is served from more than one origin — nginx on the box
    and a Vercel deployment that reverse-proxies /image/ back here. An absolute
    APP_BASE_URL would send the browser straight to the origin host, defeating
    the proxy. The signature covers only image_id and expiry, so dropping the
    host does not affect verification.
    """
    from server import generate_image_url
    return generate_image_url("", image_id)


# Extensions a browser can render inline. Anything else is sent as a download,
# since an inline Content-Disposition on an unrenderable type just produces a
# blank frame rather than a useful preview.
_INLINE_TYPES = {
    ".pdf":  "application/pdf",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".txt":  "text/plain",
}


@demo_bp.route("/documents/<project_id>", methods=["GET"])
def list_documents(project_id):
    """
    The source documents behind a knowledge base, with enough metadata for the
    UI to decide whether it can preview each one inline.
    """
    _resolve_project(project_id)

    # One representative chunk per source file carries the GridFS id of the
    # original upload, which is what the preview streams back.
    seen = {}
    for c in db["chunks"].find(
        {"knowledge_base_id": project_id},
        {"_id": 0, "source_file": 1, "source_file_id": 1, "word_count": 1, "page_end": 1},
    ):
        name = c.get("source_file")
        if not name:
            continue
        entry = seen.setdefault(name, {
            "sourceFile": name,
            "fileId": c.get("source_file_id"),
            "chunkCount": 0,
            "totalWords": 0,
            "pages": 0,
        })
        entry["chunkCount"] += 1
        entry["totalWords"] += c.get("word_count", 0)
        entry["pages"] = max(entry["pages"], c.get("page_end") or 0)

    docs = []
    for entry in seen.values():
        ext = os.path.splitext(entry["sourceFile"])[1].lower()
        entry["extension"] = ext
        entry["canPreviewInline"] = ext in _INLINE_TYPES
        entry["url"] = f"/api/demo/document/{project_id}/{entry['sourceFile']}"
        docs.append(entry)

    docs.sort(key=lambda d: d["sourceFile"])
    return jsonify({"projectId": project_id, "documents": docs})


@demo_bp.route("/document/<project_id>/<path:source_file>", methods=["GET"])
def get_document(project_id, source_file):
    """
    Streams the original uploaded file out of GridFS so the UI can preview the
    exact document the answers were grounded in.

    Scoped by project rather than taking a raw GridFS id: the id alone would let
    a visitor pull any file in the bucket, including other tenants' uploads.
    """
    import gridfs
    from bson import ObjectId
    from flask import Response

    _resolve_project(project_id)

    chunk = db["chunks"].find_one(
        {"knowledge_base_id": project_id, "source_file": source_file},
        {"_id": 0, "source_file_id": 1},
    )
    if not chunk or not chunk.get("source_file_id"):
        abort(404, description="No stored original for that document")

    fs = gridfs.GridFS(db)
    try:
        stored = fs.get(ObjectId(chunk["source_file_id"]))
    except Exception:
        abort(404, description="Stored original is no longer available")

    ext = os.path.splitext(source_file)[1].lower()
    mime = _INLINE_TYPES.get(ext, "application/octet-stream")
    disposition = "inline" if ext in _INLINE_TYPES else "attachment"

    return Response(
        stored.read(),
        mimetype=mime,
        headers={
            "Content-Disposition": f'{disposition}; filename="{os.path.basename(source_file)}"',
            "Cache-Control": "private, max-age=3600",
            # Same-origin framing only: the preview is rendered in an iframe by
            # our own UI, and nothing else should be able to embed it.
            "X-Frame-Options": "SAMEORIGIN",
        },
    )


def _retrieve_with_trace(query, project_id):
    """
    Runs the retrieval half of the pipeline while recording what happened at
    each stage, so the UI can show the ranking decisions rather than just the
    final four chunks.
    """
    from phase2_retrieval.rag_logic import generate_query_embedding, rerank_chunks
    from database import perform_semantic_retrieval

    timings = {}

    t0 = time.perf_counter()
    query_embedding = generate_query_embedding(query)
    timings["embedQueryMs"] = round((time.perf_counter() - t0) * 1000, 1)

    t0 = time.perf_counter()
    candidates = perform_semantic_retrieval(
        query_embedding, project_id, n=TOP_N, candidate_pool=MAX_CANDIDATES_SHOWN
    )
    timings["vectorSearchMs"] = round((time.perf_counter() - t0) * 1000, 1)

    vector_order = [c.get("topic_name") for c in
                    sorted(candidates, key=lambda c: c.get("score", 0), reverse=True)]

    t0 = time.perf_counter()
    top = rerank_chunks(query, candidates, n=TOP_N)
    timings["rerankMs"] = round((time.perf_counter() - t0) * 1000, 1)

    selected_ids = {c.get("chunk_index") for c in top}
    trace = []
    for c in sorted(candidates, key=lambda x: x.get("rerank_score", 0), reverse=True):
        trace.append({
            "topicName": c.get("topic_name"),
            "chunkIndex": c.get("chunk_index"),
            "sourceFile": c.get("source_file"),
            "preview": (c.get("text") or "")[:220],
            "vectorScore": round(c.get("vector_score", c.get("score", 0)) or 0, 4),
            "keywordScore": c.get("keyword_score", 0),
            "rerankScore": c.get("rerank_score", 0),
            "selected": c.get("chunk_index") in selected_ids,
        })

    return top, query_embedding, timings, trace, vector_order


@demo_bp.route("/chat", methods=["POST"])
def demo_chat():
    """
    The instrumented chat call. Returns the grounded answer plus everything the
    UI needs to explain it: citations, the full candidate ranking, per-stage
    latency, and token/cost accounting.
    """
    from phase2_retrieval.rag_logic import (
        generate_rag_response, EmbeddingGenerationError, GenerationError,
    )

    data = request.json or {}
    query = (data.get("query") or "").strip()
    project_id = data.get("projectId")

    if not query:
        abort(400, description="query is required")
    if not project_id:
        abort(400, description="projectId is required")

    project = _resolve_project(project_id)

    overall = time.perf_counter()
    try:
        chunks, _emb, timings, trace, vector_order = _retrieve_with_trace(query, project_id)
    except EmbeddingGenerationError as e:
        abort(502, description=f"Embedding service unavailable: {e}")

    if not chunks:
        return jsonify({
            "answer": "I don't have any information about that in this knowledge base yet.",
            "citations": [], "candidates": [], "grounded": False,
            "timings": timings, "usage": None,
        })

    t0 = time.perf_counter()
    try:
        answer, _cfg, token_info = generate_rag_response(query, chunks, project)
    except GenerationError as e:
        abort(502, description=f"Generation failed: {e}")
    timings["generationMs"] = round((time.perf_counter() - t0) * 1000, 1)
    timings["totalMs"] = round((time.perf_counter() - overall) * 1000, 1)

    # The model emits image references as [IMAGE: <id>] tags copied from context;
    # strip them from the prose and surface them as structured media instead.
    import re
    image_ids = []
    match = re.search(r"\[IMAGE:\s*([^\]]+)\]", answer)
    if match:
        image_ids = [i.strip() for i in match.group(1).split(",") if i.strip()]
        answer = answer.replace(match.group(0), "").strip()

    citations = [
        {
            "topicName": c.get("topic_name"),
            "chunkIndex": c.get("chunk_index"),
            "sourceFile": c.get("source_file"),
            "text": c.get("text"),
            "pageStart": c.get("page_start"),
            "pageEnd": c.get("page_end"),
            "rerankScore": c.get("rerank_score"),
            "vectorScore": c.get("vector_score"),
            "keywordScore": c.get("keyword_score"),
        }
        for c in chunks
    ]

    refused = "don't know" in answer.lower() or "do not know" in answer.lower()

    return jsonify({
        "answer": answer,
        "images": [_signed_image_url(i) for i in image_ids],
        "citations": citations,
        "candidates": trace,
        "vectorOnlyOrder": vector_order,
        "hybridOrder": [c["topicName"] for c in trace if c["selected"]],
        "grounded": not refused,
        "refused": refused,
        "timings": timings,
        "usage": token_info,
    })


@demo_bp.route("/compare", methods=["POST"])
def demo_compare():
    """
    Runs the same question with and without retrieval.

    Without grounding the model answers from parametric memory — fluent, and
    for a private knowledge base, frequently wrong. Showing both side by side
    is the clearest demonstration of what RAG actually buys.
    """
    from llm_client import generate_text
    from phase2_retrieval.rag_logic import generate_rag_response, EmbeddingGenerationError

    data = request.json or {}
    query = (data.get("query") or "").strip()
    project_id = data.get("projectId")
    if not query or not project_id:
        abort(400, description="query and projectId are required")

    project = _resolve_project(project_id)

    # --- Ungrounded: no context, no guardrails ---
    t0 = time.perf_counter()
    try:
        resp, ungrounded_usage = generate_text(
            system_instruction=(
                "You are a helpful assistant. Answer the user's question directly "
                "and confidently from your own knowledge."
            ),
            contents=query,
            temperature=0.7,
            max_output_tokens=2000,
            operation="Demo Ungrounded",
        )
        ungrounded = (resp.text or "").strip()
    except Exception as e:
        ungrounded = f"(generation failed: {e})"
        ungrounded_usage = None
    ungrounded_ms = round((time.perf_counter() - t0) * 1000, 1)

    # --- Grounded: retrieval + strict prompt ---
    t0 = time.perf_counter()
    try:
        chunks, _emb, _timings, _trace, _vo = _retrieve_with_trace(query, project_id)
        if chunks:
            grounded, _cfg, grounded_usage = generate_rag_response(query, chunks, project)
            import re
            grounded = re.sub(r"\[IMAGE:\s*[^\]]+\]", "", grounded).strip()
            sources = [c.get("topic_name") for c in chunks]
        else:
            grounded, grounded_usage, sources = "I don't have information about that in this knowledge base.", None, []
    except EmbeddingGenerationError as e:
        abort(502, description=f"Embedding service unavailable: {e}")
    grounded_ms = round((time.perf_counter() - t0) * 1000, 1)

    return jsonify({
        "query": query,
        "ungrounded": {"answer": ungrounded, "latencyMs": ungrounded_ms,
                       "usage": ungrounded_usage, "sources": []},
        "grounded": {"answer": grounded, "latencyMs": grounded_ms,
                     "usage": grounded_usage, "sources": sources},
    })


@demo_bp.route("/projection/<project_id>", methods=["GET"])
def embedding_projection(project_id):
    """
    Projects the knowledge base's embeddings to 2D via PCA so the UI can render
    the vector space. PCA is used rather than t-SNE/UMAP because it is a fixed
    linear map: a query can be projected into the *same* space afterwards, which
    is what makes "where did my question land" meaningful.
    """
    import numpy as np

    _resolve_project(project_id)
    docs = list(db["chunks"].find(
        {"knowledge_base_id": project_id},
        {"embedding": 1, "topic_name": 1, "chunk_index": 1, "source_file": 1, "text": 1, "_id": 0},
    ).sort("chunk_index", 1))

    if len(docs) < 2:
        return jsonify({"points": [], "basis": None,
                        "message": "Need at least 2 chunks to project a vector space."})

    matrix = np.array([d["embedding"] for d in docs], dtype=float)
    mean = matrix.mean(axis=0)
    centered = matrix - mean

    # SVD is the numerically stable route to principal components.
    _u, _s, vt = np.linalg.svd(centered, full_matrices=False)
    basis = vt[:2]
    coords = centered @ basis.T

    # Normalize into a stable [-1, 1] box so the frontend doesn't rescale per query.
    scale = float(np.abs(coords).max()) or 1.0
    coords = coords / scale

    points = [
        {
            "x": round(float(coords[i][0]), 4),
            "y": round(float(coords[i][1]), 4),
            "topicName": d.get("topic_name"),
            "chunkIndex": d.get("chunk_index"),
            "sourceFile": d.get("source_file"),
            "preview": (d.get("text") or "")[:160],
        }
        for i, d in enumerate(docs)
    ]

    # Cache the transform so a query can be mapped into the identical space.
    _PROJECTION_CACHE[project_id] = {"mean": mean, "basis": basis, "scale": scale}

    return jsonify({"points": points, "dimensions": matrix.shape[1], "chunkCount": len(docs)})


_PROJECTION_CACHE = {}


@demo_bp.route("/projection/<project_id>/query", methods=["POST"])
def project_query(project_id):
    """Maps a live query into the cached 2D basis from /projection."""
    import numpy as np
    from phase2_retrieval.rag_logic import generate_query_embedding, EmbeddingGenerationError

    _resolve_project(project_id)
    data = request.json or {}
    query = (data.get("query") or "").strip()
    if not query:
        abort(400, description="query is required")

    cached = _PROJECTION_CACHE.get(project_id)
    if not cached:
        abort(409, description="Call /projection first to establish the basis")

    try:
        vector = np.array(generate_query_embedding(query), dtype=float)
    except EmbeddingGenerationError as e:
        abort(502, description=str(e))

    coords = (vector - cached["mean"]) @ cached["basis"].T / cached["scale"]
    return jsonify({"x": round(float(coords[0]), 4), "y": round(float(coords[1]), 4), "query": query})


@demo_bp.route("/eval/<project_id>", methods=["GET"])
def run_eval(project_id):
    """
    Runs the stored evaluation set and reports Hit@1 / Hit@3 / MRR for
    vector-only versus hybrid retrieval.
    """
    import json
    from phase2_retrieval.rag_logic import generate_query_embedding, rerank_chunks, EmbeddingGenerationError
    from database import perform_semantic_retrieval

    _resolve_project(project_id)

    eval_doc = db["demo_evalsets"].find_one({"projectId": project_id})
    if not eval_doc:
        return jsonify({"available": False,
                        "message": "No evaluation set is defined for this knowledge base."})

    cases = eval_doc.get("cases", [])
    results, vector_ranks, hybrid_ranks = [], [], []

    for case in cases:
        query, expected = case["query"], case["expectedTopic"]
        try:
            embedding = generate_query_embedding(query)
        except EmbeddingGenerationError as e:
            abort(502, description=str(e))

        candidates = perform_semantic_retrieval(embedding, project_id, n=TOP_N,
                                                candidate_pool=MAX_CANDIDATES_SHOWN)
        vector_only = sorted(candidates, key=lambda c: c.get("score", 0), reverse=True)[:TOP_N]
        hybrid = rerank_chunks(query, candidates, n=TOP_N)

        def rank_of(topic, items):
            for i, c in enumerate(items, 1):
                if c.get("topic_name") == topic:
                    return i
            return None

        v, h = rank_of(expected, vector_only), rank_of(expected, hybrid)
        vector_ranks.append(v)
        hybrid_ranks.append(h)
        results.append({"query": query, "expectedTopic": expected,
                        "vectorRank": v, "hybridRank": h, "hit": h == 1})

    def metrics(ranks):
        total = len(ranks) or 1
        return {
            "hit1": sum(1 for r in ranks if r == 1),
            "hit3": sum(1 for r in ranks if r is not None and r <= 3),
            "mrr": round(sum(1 / r for r in ranks if r) / total, 3),
            "total": len(ranks),
        }

    return jsonify({
        "available": True,
        "cases": results,
        "vectorOnly": metrics(vector_ranks),
        "hybrid": metrics(hybrid_ranks),
    })


@demo_bp.route("/upload", methods=["POST"])
def demo_upload():
    """
    Ingests a visitor's own document into a throwaway knowledge base so they can
    test the engine on material it has never seen.
    """
    from phase1_upload.ingest import analyze_document, get_extension, SUPPORTED_EXTENSIONS, UnsupportedFileTypeError
    from phase1_upload.chunker import group_content_by_topic, generate_semantic_chunks, map_images_to_chunks
    from phase2_retrieval.rag_logic import EmbeddingGenerationError
    import server as server_module

    if "file" not in request.files:
        abort(400, description="No file provided")
    file = request.files["file"]
    if not file.filename:
        abort(400, description="No file selected")

    ext = get_extension(file.filename)
    if ext not in SUPPORTED_EXTENSIONS:
        abort(415, description=f"Unsupported file type '.{ext}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")

    file_bytes = file.read()
    stages = []

    project_id = f"demo-{uuid.uuid4()}"
    admin_id = "demo-visitor"

    t0 = time.perf_counter()
    try:
        raw_file_id = server_module.fs.put(file_bytes, filename=file.filename)
    except Exception as exc:
        abort(500, description=f"Failed to store file: {exc}")

    try:
        layout = analyze_document(file_bytes, file.filename)
    except UnsupportedFileTypeError as e:
        abort(415, description=str(e))
    if layout is None:
        abort(422, description="Could not parse that file — it may be corrupt, empty, or an image with no readable text.")
    stages.append({"stage": "parse", "ms": round((time.perf_counter() - t0) * 1000, 1),
                   "detail": f"{len(layout['semantic_blocks'])} blocks, {len(layout['images'])} images"})

    t0 = time.perf_counter()
    chunks = generate_semantic_chunks(group_content_by_topic(layout["semantic_blocks"]))
    if not chunks:
        abort(422, description="No readable text was extracted from that file.")
    stages.append({"stage": "chunk", "ms": round((time.perf_counter() - t0) * 1000, 1),
                   "detail": f"{len(chunks)} chunks"})

    t0 = time.perf_counter()
    uploaded_images = server_module.upload_images_to_gridfs(layout["images"], file.filename, server_module.fs, project_id)
    chunks = map_images_to_chunks(chunks, uploaded_images)
    stages.append({"stage": "images", "ms": round((time.perf_counter() - t0) * 1000, 1),
                   "detail": f"{len(uploaded_images)} images anchored"})

    t0 = time.perf_counter()
    try:
        chunk_docs = server_module.format_mongodb_documents(
            chunks, file.filename, raw_file_id, project_id, admin_id, language="English"
        )
    except EmbeddingGenerationError as e:
        abort(502, description=f"Embedding service unavailable: {e}")
    stages.append({"stage": "embed", "ms": round((time.perf_counter() - t0) * 1000, 1),
                   "detail": f"{len(chunk_docs)} vectors"})

    t0 = time.perf_counter()
    inserted = server_module.bulk_insert_chunks(chunk_docs)
    stages.append({"stage": "index", "ms": round((time.perf_counter() - t0) * 1000, 1),
                   "detail": f"{inserted} chunks indexed"})

    expires_at = datetime.now(timezone.utc) + timedelta(hours=EPHEMERAL_TTL_HOURS)
    db["adminprojects"].insert_one({
        "adminId": admin_id,
        "projectId": project_id,
        "projectName": file.filename,
        "projectDescription": f"Your uploaded document ({ext.upper()})",
        "projectStatus": "active",
        "projectInstruction": "You are a helpful assistant answering questions about the user's uploaded document.",
        "projectGuardrails": "Answer only from the document. If the answer isn't there, say so plainly.",
        "buttons": [],
        "templates": [],
        "isDemo": True,
        "isEphemeral": True,
        "demoIcon": "file",
        "demoSampleQuestions": [],
        "expiresAt": expires_at,
        "createdAt": datetime.now(timezone.utc),
        "updatedAt": datetime.now(timezone.utc),
    })

    return jsonify({
        "projectId": project_id,
        "fileName": file.filename,
        "fileType": ext,
        "chunksCreated": inserted,
        "imagesExtracted": len(uploaded_images),
        "totalPages": layout["total_pages"],
        "stages": stages,
        "expiresAt": expires_at.isoformat(),
    }), 201


@demo_bp.route("/stats", methods=["GET"])
def demo_stats():
    """Headline numbers for the landing view."""
    from llm_client import CHAT_MODEL, EMBEDDING_MODEL, EMBEDDING_DIMENSIONS
    from phase1_upload.ingest import SUPPORTED_EXTENSIONS

    return jsonify({
        "chatModel": CHAT_MODEL,
        "embeddingModel": EMBEDDING_MODEL,
        "embeddingDimensions": EMBEDDING_DIMENSIONS,
        "supportedFormats": sorted(SUPPORTED_EXTENSIONS),
        "demoTenants": db["adminprojects"].count_documents({"isDemo": True, "isEphemeral": {"$ne": True}}),
        "totalChunks": db["chunks"].count_documents({}),
    })
