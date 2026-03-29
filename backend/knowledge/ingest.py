"""
Knowledge Base Ingestion — multi-format, incremental, with GC.

Supports:
  1. Seed JSON (existing 20 docs)
  2. PDF files in knowledge/docs/pdf/
  3. CSV/TSV files in knowledge/docs/csv/
  4. DOCX files in knowledge/docs/docx/

Usage:
    cd backend
    python -m knowledge.ingest              # Incremental (only new/changed)
    python -m knowledge.ingest --force      # Full rebuild
    python -m knowledge.ingest --gc-only    # Just clean up orphaned vectors
"""
import json
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.vector_store import (
    add_documents, get_doc_count, content_hash,
    get_existing_hashes, garbage_collect,
)
from knowledge.parsers import parse_file, scan_directory

DOCS_DIR = os.path.join(os.path.dirname(__file__), "docs")
SEED_PATH = os.path.join(DOCS_DIR, "seed_knowledge.json")


def load_seed_data() -> list[dict]:
    """Load the original 20 hand-written knowledge docs."""
    if not os.path.exists(SEED_PATH):
        return []
    with open(SEED_PATH, "r", encoding="utf-8") as f:
        docs = json.load(f)
    # Ensure doc_type is set
    for d in docs:
        d.setdefault("metadata", {})
        d["metadata"].setdefault("doc_type", "json")
    return docs


def load_file_documents() -> list[dict]:
    """Scan all subdirectories for PDF/CSV/DOCX files and parse them."""
    all_docs = []
    skipped_files = []
    subdirs = ["pdf", "csv", "docx", "json"]

    for subdir in subdirs:
        scan_path = os.path.join(DOCS_DIR, subdir)
        if not os.path.isdir(scan_path):
            continue

        files = scan_directory(scan_path)
        if not files:
            continue

        print(f"   Scanning {scan_path}: {len(files)} files")

        for filepath in files:
            try:
                parsed = parse_file(filepath)
                if not parsed:
                    # P1-4: Track files that produced no output (e.g. scanned PDFs, empty CSVs)
                    skipped_files.append(filepath)
                    continue
                for doc in parsed:
                    all_docs.append(doc.to_dict())
            except Exception as e:
                skipped_files.append(filepath)
                print(f"   ⚠ Failed to parse {filepath}: {e}")

    # P1-4: Report skipped files so user knows why doc count might be lower than file count
    if skipped_files:
        print(f"   ⚠ {len(skipped_files)} files produced no output (scanned PDF? empty file?):")
        for sf in skipped_files[:10]:
            print(f"     - {os.path.basename(sf)}")
        if len(skipped_files) > 10:
            print(f"     ... and {len(skipped_files) - 10} more")

    return all_docs


def validate_docs(docs: list[dict]) -> list[str]:
    """Validate doc structure. Returns list of error strings."""
    errors = []
    ids_seen = set()
    for i, doc in enumerate(docs):
        doc_id = doc.get("id", "")
        if not doc_id:
            errors.append(f"Doc [{i}]: missing 'id'")
        elif doc_id in ids_seen:
            errors.append(f"Doc [{i}]: duplicate id '{doc_id}'")
        else:
            ids_seen.add(doc_id)
        if not doc.get("content", "").strip():
            errors.append(f"Doc [{i}] ({doc_id}): empty content")
    return errors


def ingest(force: bool = False, gc_only: bool = False):
    start = time.time()
    print("=" * 60)
    print("📚 Financial Knowledge Base Ingestion")
    print("=" * 60)

    current_count = get_doc_count()
    print(f"   Current vectors in DB: {current_count}")
    print()

    # ---- Load all documents ----
    print("── Loading documents ──")

    seed_docs = load_seed_data()
    print(f"   Seed JSON: {len(seed_docs)} documents")

    file_docs = load_file_documents()
    print(f"   Parsed files: {len(file_docs)} documents")

    all_docs = seed_docs + file_docs
    print(f"   Total: {len(all_docs)} documents")

    if not all_docs and not gc_only:
        print("\n   No documents to ingest.")
        return

    # ---- Validate ----
    errors = validate_docs(all_docs)
    if errors:
        print(f"\n❌ Validation errors ({len(errors)}):")
        for e in errors[:10]:
            print(f"   {e}")
        if len(errors) > 10:
            print(f"   ... and {len(errors) - 10} more")
        sys.exit(1)

    # ---- Stats ----
    categories = Counter(d.get("metadata", {}).get("category", "?") for d in all_docs)
    doc_types = Counter(d.get("metadata", {}).get("doc_type", "?") for d in all_docs)
    topics = Counter(d.get("metadata", {}).get("topic", "?") for d in all_docs)

    print(f"\n   Categories: {dict(categories)}")
    print(f"   Doc types:  {dict(doc_types)}")
    print(f"   Topics:     {dict(topics)}")

    # ---- Collect valid parent IDs for GC ----
    valid_ids = {d["id"] for d in all_docs}

    # ---- GC: remove orphaned vectors ----
    print("\n── Garbage collection ──")
    gc_deleted = garbage_collect(valid_ids)
    if gc_deleted:
        print(f"   Removed {gc_deleted} orphaned vectors")
    else:
        print("   No orphaned vectors found")

    if gc_only:
        print(f"\n✅ GC complete. Total vectors: {get_doc_count()}")
        return

    # ---- Incremental or force ingest ----
    if force:
        print("\n── Full rebuild (--force) ──")
        to_upsert = all_docs
    else:
        print("\n── Incremental ingest ──")
        existing_hashes = get_existing_hashes()
        to_upsert = []

        for doc in all_docs:
            doc_id = doc["id"]
            doc_hash = content_hash(doc["content"])
            existing_hash = existing_hashes.get(doc_id, "")

            if existing_hash == doc_hash:
                continue  # Unchanged, skip

            # Add hash to metadata for next comparison
            doc.setdefault("metadata", {})["content_hash"] = doc_hash
            to_upsert.append(doc)

        if not to_upsert:
            print("   No changes detected. All documents up to date.")
            elapsed = time.time() - start
            print(f"\n✅ Complete in {elapsed:.1f}s. Total vectors: {get_doc_count()}")
            return

        print(f"   {len(to_upsert)} documents changed or new (skipping {len(all_docs) - len(to_upsert)} unchanged)")

    # ---- Upsert with progress ----
    print("\n── Embedding & upserting ──")
    batch_size = 50
    total_vectors = 0

    for i in range(0, len(to_upsert), batch_size):
        batch = to_upsert[i:i + batch_size]
        added = add_documents(batch)
        total_vectors += added
        done = min(i + batch_size, len(to_upsert))
        pct = done / len(to_upsert) * 100
        print(f"   [{done}/{len(to_upsert)}] {pct:.0f}% — {total_vectors} vectors so far")

    elapsed = time.time() - start
    final_count = get_doc_count()

    print()
    print("=" * 60)
    print(f"✅ Ingestion complete in {elapsed:.1f}s")
    print(f"   Documents processed: {len(to_upsert)}")
    print(f"   Vectors upserted: {total_vectors}")
    print(f"   Total vectors in DB: {final_count}")
    print(f"   GC removed: {gc_deleted}")
    print("=" * 60)


if __name__ == "__main__":
    force = "--force" in sys.argv
    gc_only = "--gc-only" in sys.argv
    ingest(force=force, gc_only=gc_only)
