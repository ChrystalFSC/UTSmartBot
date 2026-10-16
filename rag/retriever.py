"""Hybrid search over the UTS index: embeddings for meaning, BM25 for exact terms.

Embeddings handle paraphrases ("how much does BCS cost" ~ "tuition fees"),
while keyword search catches names, programme codes and extension numbers
that embeddings tend to blur. The two rankings are merged with reciprocal
rank fusion.
"""
import functools
import json
import logging
import math
import re
import time
from collections import Counter

import numpy as np

from . import CHUNKS_PATH, DOCS_PATH, INDEX_PATH
from .embeddings import get_embedder

log = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"[a-z0-9]+")
QUERY_CACHE_SIZE = 1000
EMBEDDING_PAUSE_SECONDS = 600
RRF_K = 60
CANDIDATES = 50  # results taken from each ranking before fusion (tuned with evals/run.py)
# "Bachelor of Computer Science (Hons) (BCS)" -> BCS = Bachelor of Computer Science (Hons)
ABBREVIATION_RE = re.compile(
    r"((?:[A-Z][\w&\-]*|of|in|and|with|&)(?:[ \t]+(?:[A-Z][\w&\-]*|of|in|and|with|&|\([A-Za-z ]+\)))*)"
    r"[ \t]*\(([A-Z][A-Za-z]{1,7})\)"
)


def tokenize(text):
    return TOKEN_RE.findall(text.lower())


def common_title(titles):
    """"A › B › C" and "A › B › D" -> "A › B"."""
    parts = [t.split(" › ") for t in titles]
    shared = []
    for segments in zip(*parts):
        if len(set(segments)) > 1:
            break
        shared.append(segments[0])
    return " › ".join(shared) or titles[0]


def build_glossary(texts):
    """Collect abbreviations defined in the documents themselves.

    Students ask about "BCS" but fee tables only say "Bachelor of Computer
    Science", so queries are expanded with the long form before searching.
    """
    glossary = {}
    for text in texts:
        for match in ABBREVIATION_RE.finditer(text):
            phrase, abbreviation = match.group(1).strip(), match.group(2)
            if len(phrase.split()) >= 2 and phrase[0].upper() == abbreviation[0].upper():
                glossary.setdefault(abbreviation.upper(), phrase)
    return glossary


class BM25:
    def __init__(self, documents, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.docs = [Counter(tokenize(d)) for d in documents]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg_length = sum(self.lengths) / max(len(self.docs), 1)
        df = Counter(term for d in self.docs for term in d)
        n = len(self.docs)
        self.idf = {term: math.log(1 + (n - f + 0.5) / (f + 0.5)) for term, f in df.items()}

    def scores(self, query):
        terms = set(tokenize(query))
        result = np.zeros(len(self.docs), dtype=np.float32)
        for i, (doc, length) in enumerate(zip(self.docs, self.lengths)):
            score = 0.0
            for term in terms:
                tf = doc.get(term)
                if tf:
                    norm = self.k1 * (1 - self.b + self.b * length / self.avg_length)
                    score += self.idf[term] * tf * (self.k1 + 1) / (tf + norm)
            result[i] = score
        return result


class Retriever:
    def __init__(self, client=None, embedder=None):
        if not all(p.exists() for p in (INDEX_PATH, CHUNKS_PATH, DOCS_PATH)):
            raise SystemExit("Search index not found. Build it with: python -m rag.ingest")
        index = np.load(INDEX_PATH)
        index_model = str(index["model"]) if "model" in index else "gemini-embedding-001"
        self.embedder = embedder or get_embedder(index_model, client)
        if self.embedder.name != index_model:
            raise SystemExit(f"The search index was built with {index_model} but EMBEDDING_MODEL is "
                             f"{self.embedder.name}. Rebuild it with: python -m rag.ingest")
        self.vectors = index["vectors"]
        # Repeated questions (suggested questions, evals) reuse the vector instead of
        # spending another request from the embedding quota. Failures are not cached.
        self.embed_query = functools.lru_cache(maxsize=QUERY_CACHE_SIZE)(self._embed_query)
        self.keyword_only = False  # True when the last search could not use embeddings
        self.embeddings_paused_until = 0.0
        self.chunks = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
        self.docs = json.loads(DOCS_PATH.read_text(encoding="utf-8"))
        # Chunks in document order, so a hit can be widened to its neighbours.
        self.neighbours = {}
        by_doc = {}
        for c in self.chunks:
            by_doc.setdefault(c["doc"], []).append(c)
        for doc_chunks in by_doc.values():
            doc_chunks.sort(key=lambda c: c["start"])
            for i, c in enumerate(doc_chunks):
                self.neighbours[c["id"]] = (doc_chunks[max(i - 1, 0)], doc_chunks[min(i + 1, len(doc_chunks) - 1)])
        self.bm25 = BM25([f"{c['title']}\n{c['text']}" for c in self.chunks])
        self.glossary = build_glossary("\n".join(d["lines"]) for d in self.docs)

    def expand_query(self, query):
        expansions = [
            self.glossary[word.upper()]
            for word in re.findall(r"[A-Za-z]{2,8}", query)
            if word.upper() in self.glossary and (len(word) >= 3 or word.isupper())
        ]
        return f"{query} ({'; '.join(dict.fromkeys(expansions))})" if expansions else query

    def _embed_query(self, query):
        return self.embedder.embed_query(query)

    def search(self, query, k=6, expand=True):
        """Return up to k passages for the query.

        With ``expand``, each hit also includes the chunk before and after it,
        and hits that touch are merged into one passage. This keeps facts that
        straddle a chunk boundary together (e.g. a programme name and its fees).
        """
        hits = self.rank(query, k)
        if not expand:
            return hits

        spans = []  # [doc, start, end, best_hit, titles of the chunks covered]
        for hit in hits:
            before, after = self.neighbours[hit["id"]]
            start, end = before["start"], after["end"]
            titles = [before["title"], hit["title"], after["title"]]
            for span in spans:
                if span[0] == hit["doc"] and start <= span[2] and end >= span[1]:
                    span[1], span[2] = min(span[1], start), max(span[2], end)
                    span[4].extend(titles)
                    break
            else:
                spans.append([hit["doc"], start, end, hit, titles])

        passages = []
        for doc_index, start, end, hit, titles in spans:
            doc = self.docs[doc_index]
            passages.append({
                "id": hit["id"],
                # A passage can span several items (e.g. three programmes' fees), so
                # cite only the path they share, but never less than the hit's section.
                "title": max(common_title(titles), " › ".join(hit["title"].split(" › ")[:2]), key=len),
                "url": doc["url"],
                "text": "\n".join(doc["lines"][start:end]).strip(),
                "snippet": hit["text"],  # the matched chunk, for the source preview in the UI
            })
        return passages

    def rank(self, query, k):
        query = self.expand_query(query)
        rankings = [np.argsort(-self.bm25.scores(query))[:CANDIDATES]]
        self.keyword_only = True
        if time.time() >= self.embeddings_paused_until:
            try:
                rankings.append(np.argsort(-(self.vectors @ self.embed_query(query)))[:CANDIDATES])
                self.keyword_only = False
            except Exception as error:
                # Keyword search alone still gives useful results if the embedding call
                # fails (e.g. the free-tier daily quota), but quality drops, so say so.
                log.warning("Embedding search unavailable, using keyword search only: %s", str(error)[:200])
                if getattr(error, "code", None) == 429:
                    # Quota errors last a while; skip the call instead of retrying on every question.
                    self.embeddings_paused_until = time.time() + EMBEDDING_PAUSE_SECONDS

        fused = Counter()
        for ranking in rankings:
            for rank, index in enumerate(ranking):
                fused[int(index)] += 1 / (RRF_K + rank + 1)
        return [self.chunks[i] for i, _ in fused.most_common(k)]
