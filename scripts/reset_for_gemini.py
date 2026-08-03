"""
One-time data reset for the OpenAI -> Gemini migration.

Why this is necessary rather than optional: every stored chunk was embedded
with OpenAI's text-embedding-3-small. Gemini embeddings occupy a completely
different vector space, so an old chunk and a new query vector have no
meaningful geometric relationship — retrieval would return confident nonsense
rather than failing loudly. Those vectors must be regenerated, which means the
chunks must be re-ingested from their source documents.

Also clears accumulated development/test residue (throwaway tenants, chat
sessions, request logs) so the platform starts from a clean state.

A full JSON backup of every collection was taken before this script was
written; see D:\\Infoware\\rag-engine_dbbackup_<timestamp>\\.

Usage:
    python scripts/reset_for_gemini.py --dry-run   # report only, no writes
    python scripts/reset_for_gemini.py --confirm   # perform the reset
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gridfs
from database import db

# Collections wiped entirely: chunks are unusable post-migration, the rest is
# development residue. integration_registry is preserved — it's the service
# catalog (Calendar/Shopify/Slack/Calendly definitions), not user data.
COLLECTIONS_TO_CLEAR = [
    "chunks",
    "chathistories",
    "api_logs",
    "admindetails",
    "adminprojects",
    "project_credentials",
    "projects",
]

PRESERVED = ["integration_registry"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", action="store_true", help="actually perform the reset")
    parser.add_argument("--dry-run", action="store_true", help="report counts without deleting")
    args = parser.parse_args()

    if not args.confirm and not args.dry_run:
        parser.error("pass --dry-run to preview or --confirm to execute")

    fs = gridfs.GridFS(db)

    print("Current state:")
    for name in COLLECTIONS_TO_CLEAR:
        print(f"  {name:<22} {db[name].count_documents({}):>6}")
    print(f"  {'gridfs files':<22} {db['fs.files'].count_documents({}):>6}")
    for name in PRESERVED:
        print(f"  {name:<22} {db[name].count_documents({}):>6}  (preserved)")

    if args.dry_run:
        print("\nDry run — nothing was deleted.")
        return 0

    print("\nResetting...")
    for name in COLLECTIONS_TO_CLEAR:
        result = db[name].delete_many({})
        print(f"  cleared {name}: {result.deleted_count}")

    deleted_files = 0
    for stored in fs.find():
        fs.delete(stored._id)
        deleted_files += 1
    print(f"  cleared gridfs files: {deleted_files}")

    print("\nRemaining:")
    for name in sorted(db.list_collection_names()):
        print(f"  {name:<22} {db[name].count_documents({}):>6}")

    print("\nDone. Re-ingest source documents to rebuild knowledge bases with Gemini embeddings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
