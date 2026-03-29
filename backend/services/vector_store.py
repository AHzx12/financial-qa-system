"""
Vector store service — ChromaDB for RAG at scale (1000+ docs).

Upgrades:
- Hybrid search: optional keyword filter via where_document (substring match)
- Reranker: optional cross-encoder reranking of top candidates
- Confidence score: returns max_relevance for upstream confidence-aware decisions
- All v4 features preserved: type-aware chunking, metadata filter, parent dedup, GC
"""
import os
import re
import hashlib
import logging


import chromadb
from chromadb.utils import embedding_functions

logger = logging.getLogger("vector_store")

PERSIST_DIR = os.path.join(os.path.dirname(__file__), "..", "knowledge", "chroma_db")
COLLECTION_NAME = "financial_knowledge"
RELEVANCE_THRESHOLD = 0.9

CHUNK_CONFIGS = {
    "default": {"size": 300, "overlap": 50, "min_length": 50},
    "pdf":     {"size": 500, "overlap": 80, "min_length": 60},
    "docx":    {"size": 500, "overlap": 80, "min_length": 60},
    "csv":     {"size": 600, "overlap": 0,  "min_length": 30},
    "json":    {"size": 300, "overlap": 50, "min_length": 50},
}

# Reranker: lazy-loaded cross-encoder (None = not loaded yet, False = unavailable)
_reranker = None


def _get_embedding_fn():
    try:
        return embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="paraphrase-multilingual-MiniLM-L12-v2"
        )
    except Exception:
        return embedding_functions.DefaultEmbeddingFunction()


def _get_collection():
    client = chromadb.PersistentClient(path=PERSIST_DIR)
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=_get_embedding_fn(),
        metadata={"hnsw:space": "cosine"},
    )


# ================================================================
#  RERANKER — cross-encoder (lazy loaded)
# ================================================================

def _get_reranker():
    """Lazy-load cross-encoder reranker. Returns None if unavailable."""
    global _reranker
    if _reranker is False:
        return None
    if _reranker is not None:
        return _reranker
    try:
        from sentence_transformers import CrossEncoder
        # P0-1 fix: multilingual reranker for Chinese+English knowledge base.
        # Replaces English-only ms-marco-MiniLM-L-6-v2 which could mis-rank Chinese docs.
        _reranker = CrossEncoder(
            "cross-encoder/ms-marco-multilingual-MiniLM-L12-v2",
            max_length=512,
        )
        logger.info("Reranker loaded: ms-marco-multilingual-MiniLM-L12-v2 (device: %s)",
                     _reranker.model.device)
        return _reranker
    except Exception as e:
        logger.warning("Reranker unavailable (non-fatal): %s", e)
        _reranker = False
        return None


def _rerank(query: str, docs: list[dict], top_k: int = 5) -> list[dict]:
    """
    Rerank documents using cross-encoder. Replaces vector similarity scores
    with cross-encoder scores. Falls back to original order if reranker unavailable.
    """
    reranker = _get_reranker()
    if reranker is None or len(docs) <= 1:
        return docs[:top_k]

    try:
        pairs = [[query, doc["content"][:512]] for doc in docs]  # P1-3: lists not tuples
        scores = reranker.predict(pairs)

        for doc, score in zip(docs, scores):
            doc["rerank_score"] = float(score)

        reranked = sorted(docs, key=lambda d: d.get("rerank_score", 0), reverse=True)
        logger.debug("Reranked %d docs, top score=%.3f", len(docs), reranked[0]["rerank_score"])
        return reranked[:top_k]
    except Exception as e:
        logger.warning("Reranking failed (using original order): %s", e)
        return docs[:top_k]


# ================================================================
#  CHUNKING — type-aware
# ================================================================

def chunk_document(content: str, doc_type: str = "default") -> list[str]:
    config = CHUNK_CONFIGS.get(doc_type, CHUNK_CONFIGS["default"])
    return _chunk_text(content, config["size"], config["overlap"], config["min_length"])


def _chunk_text(text: str, chunk_size: int, overlap: int, min_length: int) -> list[str]:
    if len(text) <= chunk_size * 1.5:
        return [text] if len(text) >= min_length else []

    sentence_ends = [m.end() for m in re.finditer(r"[。.！？\n]", text)]
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            remainder = text[start:].strip()
            if len(remainder) >= min_length:
                chunks.append(remainder)
            break
        best_break = end
        for se in sentence_ends:
            if start < se <= end + 30:
                best_break = se
        chunk = text[start:best_break].strip()
        if len(chunk) >= min_length:
            chunks.append(chunk)
        next_start = best_break - overlap if overlap > 0 and best_break > start + overlap else best_break
        if next_start <= start:
            next_start = start + 1
        start = next_start
    return chunks


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ================================================================
#  INGEST
# ================================================================

def add_documents(documents: list[dict]) -> int:
    collection = _get_collection()
    all_ids, all_contents, all_metadatas = [], [], []

    for doc in documents:
        doc_id = doc["id"]
        raw_content = doc["content"]
        metadata = doc.get("metadata", {})
        doc_type = metadata.get("doc_type", "json")
        doc_hash = content_hash(raw_content)

        chunks = chunk_document(raw_content, doc_type)
        for i, chunk in enumerate(chunks):
            chunk_id = doc_id if len(chunks) == 1 else f"{doc_id}_chunk_{i}"
            all_ids.append(chunk_id)
            all_contents.append(chunk)
            all_metadatas.append({
                **metadata,
                "parent_id": doc_id, "chunk_index": i,
                "total_chunks": len(chunks), "content_hash": doc_hash,
            })

    if all_ids:
        collection.upsert(ids=all_ids, documents=all_contents, metadatas=all_metadatas)
    return len(all_ids)


# ================================================================
#  SEARCH — hybrid + rerank + parent dedup
# ================================================================

def search(
    query: str,
    n_results: int = 5,
    where: dict | None = None,
    keywords: list[str] | None = None,
    use_reranker: bool = False,
) -> dict:
    """
    Search with optional hybrid filtering + reranking.

    Returns dict:
        {
            "docs": [...],          # list of result dicts
            "max_relevance": float, # highest relevance score (for confidence-aware decisions)
        }

    Args:
        where: ChromaDB metadata filter
        keywords: If provided, adds where_document $contains filter for each keyword.
                  Multiple keywords use $or (any keyword match).
        use_reranker: If True, reranks candidates with cross-encoder.
    """
    collection = _get_collection()
    count = collection.count()
    if count == 0:
        return {"docs": [], "max_relevance": 0.0}

    # Over-fetch for dedup + reranking headroom
    fetch_multiplier = 4 if use_reranker else 3
    raw_n = min(n_results * fetch_multiplier, count)

    kwargs = {
        "query_texts": [query],
        "n_results": raw_n,
        "include": ["documents", "metadatas", "distances"],
    }

    # Metadata filter
    if where:
        kwargs["where"] = where

    # Hybrid: keyword substring filter
    # P1-2 fix: ChromaDB 0.5's where_document $or support is unstable.
    # Use only the first (most important) keyword for reliable filtering.
    # _extract_keywords already returns keywords sorted by importance.
    if keywords:
        kwargs["where_document"] = {"$contains": keywords[0]}

    try:
        results = collection.query(**kwargs)
    except Exception as e:
        # Filter might reference non-existent keys or fail — retry without filters
        logger.warning("Search with filters failed: %s. Retrying unfiltered.", e)
        results = collection.query(
            query_texts=[query], n_results=raw_n,
            include=["documents", "metadatas", "distances"],
        )

    # Threshold + parent dedup
    seen_parents = set()
    candidates = []

    for i in range(len(results["ids"][0])):
        distance = results["distances"][0][i] if results.get("distances") else 0
        if distance > RELEVANCE_THRESHOLD:
            continue
        meta = results["metadatas"][0][i] if results.get("metadatas") else {}
        parent = meta.get("parent_id", results["ids"][0][i])

        if parent in seen_parents:
            continue
        seen_parents.add(parent)

        candidates.append({
            "id": results["ids"][0][i],
            "content": results["documents"][0][i],
            "source": meta.get("source", "knowledge_base"),
            "topic": meta.get("topic", "general"),
            "category": meta.get("category", "concept"),
            "parent_id": parent,
            "entity": meta.get("entity", ""),
            "relevance_score": round(1.0 - distance, 3),
        })

    # Rerank if requested and we have enough candidates
    if use_reranker and len(candidates) > 1:
        candidates = _rerank(query, candidates, top_k=n_results)
    else:
        candidates = candidates[:n_results]

    max_rel = max((d["relevance_score"] for d in candidates), default=0.0)

    return {"docs": candidates, "max_relevance": max_rel}


# ================================================================
#  GC + UTILS
# ================================================================

def garbage_collect(valid_parent_ids: set[str]) -> int:
    collection = _get_collection()
    if collection.count() == 0:
        return 0
    all_data = collection.get(include=["metadatas"])
    to_delete = [
        doc_id for doc_id, meta in zip(all_data["ids"], all_data["metadatas"])
        if meta.get("parent_id", doc_id) not in valid_parent_ids
    ]
    for i in range(0, len(to_delete), 500):
        collection.delete(ids=to_delete[i:i+500])
    if to_delete:
        logger.info("GC: deleted %d orphaned vectors", len(to_delete))
    return len(to_delete)


def get_existing_hashes() -> dict[str, str]:
    collection = _get_collection()
    if collection.count() == 0:
        return {}
    all_data = collection.get(include=["metadatas"])
    hashes = {}
    for meta in all_data["metadatas"]:
        parent = meta.get("parent_id", "")
        h = meta.get("content_hash", "")
        if parent and h:
            hashes[parent] = h
    return hashes


def get_doc_count() -> int:
    return _get_collection().count()
