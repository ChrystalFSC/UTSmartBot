"""Build the UTSmartBot search index.

Usage:
    python -m rag.fetch    # optional: download the pages in knowledge/sources.txt into knowledge/uts_website/
    python -m rag.ingest   # index everything in knowledge/

Embeddings are cached by chunk id, so re-running after a small change only
embeds the chunks that are new or changed. The embedding model comes from
EMBEDDING_MODEL in .env (see rag/embeddings.py); changing it re-embeds everything.
"""
import argparse
import json
import logging
import time
from dataclasses import asdict

import numpy as np
from dotenv import load_dotenv

from . import CHUNKS_PATH, DATA_DIR, DOCS_PATH, INDEX_PATH
from .documents import chunk_document, load_local_documents
from .embeddings import get_embedder

log = logging.getLogger("rag.ingest")


def load_cache(model):
    """Previous vectors by chunk id, if they were made with the same model."""
    if not (INDEX_PATH.exists() and CHUNKS_PATH.exists()):
        return {}
    index = np.load(INDEX_PATH)
    index_model = str(index["model"]) if "model" in index else "gemini-embedding-001"
    ids = [c["id"] for c in json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))]
    if index_model != model or len(ids) != len(index["vectors"]):
        return {}
    return dict(zip(ids, index["vectors"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args()  # only for --help

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv()
    embedder = get_embedder()
    log.info("Embedding model: %s", embedder.name)

    docs = load_local_documents()
    chunks, doc_records = {}, []
    for doc_index, doc in enumerate(docs):
        lines, doc_chunks = chunk_document(doc, doc_index)
        doc_records.append({"title": doc.title, "url": doc.url, "lines": lines})
        for chunk in doc_chunks:
            chunks.setdefault(chunk.id, chunk)  # drop exact duplicates
    chunks = list(chunks.values())
    log.info("%d documents -> %d chunks", len(docs), len(chunks))

    cache = load_cache(embedder.name)
    missing = [c for c in chunks if c.id not in cache]
    log.info("%d chunks cached, %d to embed", len(chunks) - len(missing), len(missing))
    if missing:
        started = time.time()
        # The title is included so a chunk about "fees" also knows which programme page it came from.
        new_vectors = embedder.embed_documents([f"{c.title}\n\n{c.text}" for c in missing])
        cache.update(zip((c.id for c in missing), new_vectors))
        log.info("Embedding took %.0fs", time.time() - started)

    DATA_DIR.mkdir(exist_ok=True)
    np.savez_compressed(
        INDEX_PATH,
        vectors=np.stack([cache[c.id] for c in chunks]).astype(np.float32),
        model=np.array(embedder.name),  # the retriever must embed queries with the same model
    )
    CHUNKS_PATH.write_text(json.dumps([asdict(c) for c in chunks], ensure_ascii=False, indent=1), encoding="utf-8")
    DOCS_PATH.write_text(json.dumps(doc_records, ensure_ascii=False), encoding="utf-8")
    log.info("Wrote %s, %s and %s", INDEX_PATH.name, CHUNKS_PATH.name, DOCS_PATH.name)


if __name__ == "__main__":
    main()
