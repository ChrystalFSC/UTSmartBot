"""Load UTS documents from ``knowledge/`` and split them into chunks.

Web pages are downloaded into ``knowledge/uts_website/`` by ``python -m rag.fetch``.
"""
import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup
from pypdf import PdfReader

from . import KNOWLEDGE_DIR

log = logging.getLogger(__name__)

SOURCES_FILE = KNOWLEDGE_DIR / "sources.txt"
CHUNK_CHARS = 1200
OVERLAP_CHARS = 200


@dataclass
class Document:
    title: str
    url: str | None
    text: str


@dataclass
class Chunk:
    id: str
    title: str
    url: str | None
    text: str
    doc: int = 0  # index of the source document in docs.json
    start: int = 0  # first line of the chunk in that document
    end: int = 0  # line after the last one


def clean_text(text):
    text = text.replace("\r\n", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def html_to_document(html, fallback_title, fallback_url=None):
    soup = BeautifulSoup(html, "html.parser")
    canonical = soup.find("link", rel="canonical") or soup.find("meta", property="og:url")
    url = (canonical.get("href") or canonical.get("content")) if canonical else fallback_url
    title = soup.title.get_text(strip=True) if soup.title else fallback_title
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "form", "svg", "iframe"]):
        tag.decompose()
    # Keep each table row on one line ("Programme | 126 | 3.5 | 52,440 | ...") so a row's
    # values stay together in one chunk instead of one cell per line.
    for row in soup.find_all("tr"):
        values = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
        row.replace_with(soup.new_string(" | ".join(v for v in values if v) + "\n"))
    # <article> is the page only when there is exactly one; staff pages use one per person.
    articles = soup.find_all("article")
    main = soup.find("main") or (articles[0] if len(articles) == 1 else None) or soup.body or soup
    return Document(title=title or fallback_title, url=url, text=clean_text(main.get_text("\n")))


def load_file(path: Path):
    suffix = path.suffix.lower()
    title = path.stem.replace("_", " ").replace("-", " ").strip()
    if suffix in (".md", ".txt"):
        text = path.read_text(encoding="utf-8")
        # Optional first line "Source: https://..." links chunks back to the original page.
        url = None
        first, _, rest = text.partition("\n")
        if first.lower().startswith("source:"):
            url, text = first.split(":", 1)[1].strip(), rest
        return Document(title=title, url=url, text=clean_text(text))
    if suffix in (".html", ".htm"):
        return html_to_document(path.read_text(encoding="utf-8", errors="ignore"), title)
    if suffix == ".pdf":
        reader = PdfReader(str(path))
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        return Document(title=title, url=None, text=clean_text(text))
    return None


def load_local_documents():
    docs = []
    for path in sorted(KNOWLEDGE_DIR.rglob("*")):
        if path.is_file() and path.name != SOURCES_FILE.name:
            doc = load_file(path)
            if doc and doc.text:
                docs.append(doc)
                log.info("Loaded %s (%d chars)", path.relative_to(KNOWLEDGE_DIR), len(doc.text))
    return docs



def split_lines(text):
    lines = []
    for line in text.split("\n"):
        # Very long lines (e.g. PDF paragraphs) are broken at sentence ends.
        while len(line) > CHUNK_CHARS:
            cut = line.rfind(". ", 0, CHUNK_CHARS)
            cut = cut + 1 if cut > CHUNK_CHARS // 2 else CHUNK_CHARS
            lines.append(line[:cut])
            line = line[cut:].lstrip()
        lines.append(line)
    return lines


def chunk_document(doc: Document, doc_index=0):
    """Split on line boundaries into ~CHUNK_CHARS pieces with a small overlap.

    Returns the document's lines and its chunks. Chunks remember their line
    range so the retriever can widen a hit to its neighbours, keeping things
    like a programme name and its fee table together.
    """
    lines = split_lines(doc.text)
    ranges, start, size = [], 0, 0
    for i, line in enumerate(lines):
        if size + len(line) > CHUNK_CHARS and i > start:
            ranges.append((start, i))
            # Carry the last lines over so facts split across a boundary stay findable.
            new_start, overlap = i, 0
            while new_start - 1 > start and overlap + len(lines[new_start - 1]) <= OVERLAP_CHARS:
                new_start -= 1
                overlap += len(lines[new_start]) + 1
            start, size = new_start, overlap
        size += len(line) + 1
    ranges.append((start, len(lines)))

    chunks = []
    for start, end in ranges:
        text = "\n".join(lines[start:end]).strip()
        if not text:
            continue
        title = " › ".join([doc.title] + section_path(lines, start))
        # The title is part of the id because it is embedded with the text.
        chunk_id = hashlib.sha1(f"{doc.url}|{title}|{text}".encode("utf-8")).hexdigest()[:16]
        chunks.append(Chunk(id=chunk_id, title=title, url=doc.url, text=text,
                            doc=doc_index, start=start, end=end))
    return lines, chunks


HEADING_RE = re.compile(r"^[A-Z][A-Za-z&'()/,\- ]{2,60}$")
PERSON_RE = re.compile(r"^(Mr|Ms|Mdm|Dr|Prof|Assoc|Associate|Ts|Sr|Ir)\b")
ITEM_RE = re.compile(r"^\d{1,2}\.\s+[A-Z].{3,80}$")


def is_heading(line):
    """Plain-text documents have no markup, so guess headings: short Title Case lines."""
    line = line.strip()
    return (
        bool(HEADING_RE.match(line))
        and len(line.split()) <= 7
        and not line.endswith((".", ":"))
        and not PERSON_RE.match(line)
    )


def section_path(lines, start):
    """Find the section heading (and numbered item, e.g. a programme) a chunk belongs to.

    Gives citations like "UTS knowledge base › Undergraduate Programmes ›
    14. Bachelor of Computer Science (Hons)" instead of a random first line.
    """
    first = next((i for i in range(start, min(start + 5, len(lines))) if lines[i].strip()), start)
    heading_at = next((j for j in range(first, max(-1, first - 400), -1) if is_heading(lines[j])), None)
    path = [lines[heading_at].strip()] if heading_at is not None else []
    floor = heading_at if heading_at is not None else max(-1, first - 40)
    item = next((lines[j].strip() for j in range(first, floor, -1) if ITEM_RE.match(lines[j].strip())), None)
    if item and item not in path:
        path.append(item)
    return path
