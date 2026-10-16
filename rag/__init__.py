"""Retrieval-augmented generation (RAG) for UTSmartBot.

- ``rag.documents``: load knowledge files / URLs and split them into chunks
- ``rag.embeddings``: local open-source or Gemini embedding models
- ``rag.ingest``:    build the search index (``python -m rag.ingest``)
- ``rag.retriever``: hybrid (embedding + keyword) search used by the app
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = ROOT / "knowledge"
DATA_DIR = ROOT / "data"
CHUNKS_PATH = DATA_DIR / "chunks.json"
DOCS_PATH = DATA_DIR / "docs.json"


def index_path(model):
    """One search index per embedding model, e.g. data/index-gemini-embedding-001.npz,
    so a deployment can switch models (local or Gemini) without rebuilding."""
    return DATA_DIR / f"index-{re.sub(r'[^a-z0-9]+', '-', model.lower()).strip('-')}.npz"
