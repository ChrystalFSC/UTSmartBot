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

from . import CHUNKS_PATH, DATA_DIR, DOCS_PATH, index_path
from .documents import chunk_document, load_local_documents
from .embeddings import get_embedder

log = logging.getLogger("rag.ingest")


def load_cache(model):
    """Previous vectors by chunk id from this model's index, if there is one."""
    path = index_path(model)
    if not path.exists():
        return {}
    index = np.load(path)
    if "ids" in index:
        ids = list(index["ids"])
    elif CHUNKS_PATH.exists():  # older index files did not store their chunk ids
        ids = [c["id"] for c in json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))]
    else:
        return {}
    return dict(zip(ids, index["vectors"])) if len(ids) == len(index["vectors"]) else {}


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
    path = index_path(embedder.name)
    np.savez_compressed(
        path,
        vectors=np.stack([cache[c.id] for c in chunks]).astype(np.float32),
        ids=np.array([c.id for c in chunks]),  # lets the retriever detect an index older than chunks.json
        model=np.array(embedder.name),  # the retriever must embed queries with the same model
    )
    CHUNKS_PATH.write_text(json.dumps([asdict(c) for c in chunks], ensure_ascii=False, indent=1), encoding="utf-8")
    DOCS_PATH.write_text(json.dumps(doc_records, ensure_ascii=False), encoding="utf-8")
    log.info("Wrote %s, %s and %s", path.name, CHUNKS_PATH.name, DOCS_PATH.name)
    others = [p.name for p in DATA_DIR.glob("index-*.npz") if p != path]
    if others:
        log.info("Other indexes (%s) must be rebuilt too if the knowledge files changed, "
                 "e.g. EMBEDDING_MODEL=<model> python -m rag.ingest", ", ".join(others))


if __name__ == "__main__":
    main()
