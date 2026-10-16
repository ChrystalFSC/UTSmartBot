"""Embedding backends: a local open-source model (default) or the Gemini API.

Set EMBEDDING_MODEL in .env to choose:
    EMBEDDING_MODEL=intfloat/multilingual-e5-large   # any fastembed model, runs on this machine
    EMBEDDING_MODEL=gemini-embedding-001             # Gemini API (free tier: ~1,000 requests/day)

Local models are downloaded once into .models/ and then work offline, with
no quota and no API key. The index remembers which model built it, because
vectors from different models cannot be compared.
"""
import logging
import os
import time

import numpy as np

from . import ROOT

log = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "intfloat/multilingual-e5-large"
MODEL_CACHE_DIR = ROOT / ".models"
GEMINI_DIM = 768
GEMINI_BATCH = 50


def normalize(matrix):
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    return matrix / np.clip(norms, 1e-12, None)


class LocalEmbedder:
    """Open-source model run with fastembed (ONNX, CPU only, no PyTorch needed)."""

    def __init__(self, name):
        from fastembed import TextEmbedding  # imported lazily so Gemini-only setups skip it

        self.name = name
        self.model = TextEmbedding(name, cache_dir=str(MODEL_CACHE_DIR))
        # E5 models are trained with these prefixes and work noticeably worse without them.
        self.e5 = "e5" in name.lower()

    def embed_documents(self, texts):
        if self.e5:
            texts = [f"passage: {t}" for t in texts]
        vectors = []
        for i, vector in enumerate(self.model.embed(texts, batch_size=16), start=1):
            vectors.append(vector)
            if i % 50 == 0 or i == len(texts):
                log.info("Embedded %d/%d chunks", i, len(texts))
        return normalize(np.array(vectors, dtype=np.float32))

    def embed_query(self, text):
        query = f"query: {text}" if self.e5 else text
        return normalize(np.array(next(iter(self.model.query_embed([query]))), dtype=np.float32))


class GeminiEmbedder:
    """Gemini embedding API. Fast and strong, but limited on the free tier."""

    def __init__(self, name, client):
        from google.genai import types

        self.name = name
        self.client = client
        self.types = types

    def _embed(self, texts, task_type):
        config = self.types.EmbedContentConfig(task_type=task_type, output_dimensionality=GEMINI_DIM)
        return self.client.models.embed_content(model=self.name, contents=texts, config=config)

    def embed_documents(self, texts):
        from google.genai import errors

        vectors = []
        for start in range(0, len(texts), GEMINI_BATCH):
            batch = texts[start:start + GEMINI_BATCH]
            for attempt in range(8):
                try:
                    result = self._embed(batch, "RETRIEVAL_DOCUMENT")
                    break
                except errors.APIError as error:
                    if error.code not in (429, 500, 503) or attempt == 7:
                        raise
                    wait = min(60, 5 * 2 ** attempt)
                    log.warning("Embedding API returned %s, retrying in %ss", error.code, wait)
                    time.sleep(wait)
            vectors.extend(e.values for e in result.embeddings)
            log.info("Embedded %d/%d chunks", min(start + GEMINI_BATCH, len(texts)), len(texts))
        # Truncated (768-dim) Gemini embeddings are not unit length.
        return normalize(np.array(vectors, dtype=np.float32))

    def embed_query(self, text):
        result = self._embed(text, "RETRIEVAL_QUERY")
        return normalize(np.array(result.embeddings[0].values, dtype=np.float32))


def get_embedder(name=None, client=None):
    name = name or os.environ.get("EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL
    if name.startswith("gemini"):
        if client is None:
            from google import genai

            client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        return GeminiEmbedder(name, client)
    return LocalEmbedder(name)
