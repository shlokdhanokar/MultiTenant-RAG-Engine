"""
Seeds the demo knowledge bases that power the showcase UI.

Creates two tenants from the sample documents so the multi-tenant switcher has
something real to demonstrate: the same engine serving isolated knowledge bases
with different personas and guardrails. Also stores an evaluation set per
tenant for the eval dashboard.

Idempotent — re-running replaces the demo tenants and their chunks, and leaves
everything else (including visitor uploads) alone.

Usage:
    python scripts/seed_demo.py
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gridfs
from database import db
from phase1_upload.ingest import analyze_document
from phase1_upload.chunker import group_content_by_topic, generate_semantic_chunks, map_images_to_chunks

import server as server_module

KB_DIR = os.path.join(os.path.dirname(__file__), "..", "knowledge_base")

DEMOS = [
    {
        "projectId": "demo-tourism",
        "projectName": "Sapphire Cove Resort",
        "projectDescription": "Luxury island resort — rooms, dining, excursions, travel",
        "demoIcon": "palm",
        "file": "tourism.pdf",
        "projectInstruction": (
            "You are the digital concierge for Sapphire Cove Resort. Answer guest "
            "questions warmly and concisely, as a knowledgeable member of staff would."
        ),
        "projectGuardrails": (
            "Never invent prices, dates, or availability. If a guest asks about "
            "something not covered in the resort information, say so and offer to "
            "connect them with the front desk."
        ),
        "sampleQuestions": [
            "Can I go scuba diving at the resort?",
            "What kind of food is served at the restaurants?",
            "Tell me about the beachfront villas",
            "How do I get to the island from the airport?",
        ],
        "eval": [
            ("Can I go scuba diving at the resort?", "Adventure, Wellness & Exploration"),
            ("Do you offer spa treatments and massages?", "Adventure, Wellness & Exploration"),
            ("What kind of food is served at the restaurants?", "A Culinary Journey by the Sea"),
            ("Is fresh seafood available for dinner?", "A Culinary Journey by the Sea"),
            ("Tell me about the beachfront villas", "Luxury Stays Designed for Every Traveler"),
            ("What room options do you have for families?", "Luxury Stays Designed for Every Traveler"),
            ("How do I get to the island from the airport?", "Plan Your Perfect Getaway"),
            ("What should I pack and what is the weather like?", "Plan Your Perfect Getaway"),
            ("How far in advance should I book excursions?", "Frequently Asked Questions"),
            ("What makes this island special?", "A Tropical Escape Like No Other"),
            ("Are there coral reefs and turquoise lagoons nearby?", "A Tropical Escape Like No Other"),
            ("Can I arrange a private dining experience?", "A Culinary Journey by the Sea"),
        ],
    },
    {
        "projectId": "demo-medical",
        "projectName": "Apex Medical Center",
        "projectDescription": "Hospital services — emergency, clinics, admissions, billing",
        "demoIcon": "cross",
        "file": "medical.pdf",
        "projectInstruction": (
            "You are the patient information assistant for Apex Medical Center. "
            "Answer clearly and calmly, in plain language a worried patient can follow."
        ),
        "projectGuardrails": (
            "Never give medical advice, diagnoses, or treatment recommendations. "
            "Never speculate about a patient's condition. For anything clinical, "
            "direct the person to speak with a qualified member of staff. "
            "Do not invent costs, wait times, or insurance coverage."
        ),
        "sampleQuestions": [
            "What emergency services are available?",
            "How do I book an appointment at a clinic?",
            "What happens during the admission process?",
            "Which insurance plans do you accept?",
        ],
        # Expected topics are substrings of the chunker's actual headers,
        # resolved at seed time by _match_topic().
        "eval": [
            ("What emergency services do you offer?", "Critical Care"),
            ("Is there 24/7 critical care?", "Critical Care"),
            ("How do I book an outpatient appointment?", "Specialized Consultations"),
            ("Do you have a cardiology clinic?", "Specialized Consultations"),
            ("What is the admission process like?", "Comfortable Stay"),
            ("What room types are available for inpatients?", "Comfortable Stay"),
            ("How does billing work?", "Transparent Billing"),
            ("Which insurance plans are accepted?", "Transparent Billing"),
            ("What are the hospital's facilities and mission?", "Introduction, Mission"),
        ],
    },
]

DEMO_ADMIN_ID = "demo-admin"


def _match_topic(expected_fragment, available_topics):
    """
    Eval sets are written against human-readable section names, but the chunker
    produces hierarchical headers ("Parent - Child"). Resolve by substring so the
    eval set doesn't have to encode chunker internals.
    """
    for topic in available_topics:
        if expected_fragment.lower() in topic.lower():
            return topic
    return expected_fragment


def seed_one(spec):
    project_id = spec["projectId"]
    path = os.path.join(KB_DIR, spec["file"])
    if not os.path.exists(path):
        print(f"  SKIP {project_id}: {path} not found")
        return False

    print(f"\n=== {spec['projectName']} ({project_id}) ===")

    # Clear any previous run of this demo tenant.
    fs = gridfs.GridFS(db)
    removed = db["chunks"].delete_many({"knowledge_base_id": project_id}).deleted_count
    for stored in fs.find({"knowledge_base_id": project_id}):
        fs.delete(stored._id)
    db["adminprojects"].delete_many({"projectId": project_id})
    db["demo_evalsets"].delete_many({"projectId": project_id})
    if removed:
        print(f"  cleared {removed} existing chunks")

    with open(path, "rb") as f:
        file_bytes = f.read()

    raw_file_id = fs.put(file_bytes, filename=spec["file"], knowledge_base_id=project_id)

    layout = analyze_document(file_bytes, spec["file"])
    if layout is None:
        print(f"  FAILED to parse {spec['file']}")
        return False

    chunks = generate_semantic_chunks(group_content_by_topic(layout["semantic_blocks"]))
    images = server_module.upload_images_to_gridfs(layout["images"], spec["file"], fs, project_id)
    chunks = map_images_to_chunks(chunks, images)
    print(f"  parsed: {len(chunks)} chunks, {len(images)} images, {layout['total_pages']} pages")

    print(f"  embedding {len(chunks)} chunks...")
    chunk_docs = server_module.format_mongodb_documents(
        chunks, spec["file"], raw_file_id, project_id, DEMO_ADMIN_ID, language="English"
    )
    inserted = server_module.bulk_insert_chunks(chunk_docs)
    print(f"  indexed: {inserted} chunks")

    db["adminprojects"].insert_one({
        "adminId": DEMO_ADMIN_ID,
        "projectId": project_id,
        "projectName": spec["projectName"],
        "projectDescription": spec["projectDescription"],
        "projectStatus": "active",
        "projectInstruction": spec["projectInstruction"],
        "projectGuardrails": spec["projectGuardrails"],
        "buttons": [],
        "templates": [],
        "isDemo": True,
        "isEphemeral": False,
        "demoIcon": spec["demoIcon"],
        "demoSampleQuestions": spec["sampleQuestions"],
        "createdAt": datetime.now(timezone.utc),
        "updatedAt": datetime.now(timezone.utc),
    })

    topics = db["chunks"].distinct("topic_name", {"knowledge_base_id": project_id})
    cases = [{"query": q, "expectedTopic": _match_topic(t, topics)} for q, t in spec["eval"]]
    db["demo_evalsets"].insert_one({
        "projectId": project_id,
        "cases": cases,
        "createdAt": datetime.now(timezone.utc),
    })
    print(f"  eval set: {len(cases)} cases")
    print(f"  topics: {topics}")
    return True


def main():
    if not db["admindetails"].find_one({"adminId": DEMO_ADMIN_ID}):
        db["admindetails"].insert_one({
            "adminId": DEMO_ADMIN_ID,
            "masterKeyHash": None,          # demo tenants are API-key-less by design
            "projectKeys": [],
            "companyName": "RAG Engine Demo",
            "companyPersona": "Public demonstration tenant",
            "planType": "demo",
            "maxApiKeys": 0,
            "fileUploadCount": 0,
            "createdAt": datetime.now(timezone.utc),
            "updatedAt": datetime.now(timezone.utc),
        })
        print("created demo admin")

    ok = sum(1 for spec in DEMOS if seed_one(spec))
    print(f"\nSeeded {ok}/{len(DEMOS)} demo knowledge bases.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
