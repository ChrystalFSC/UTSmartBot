"""Download the UTS web pages listed in knowledge/sources.txt into knowledge/uts_website/.

Usage:
    python -m rag.fetch      # then: python -m campus.extract && python -m rag.ingest

HTML pages are saved as-is with their URL recorded, so citations link back to
them; PDFs (e.g. academic calendars) are saved as text. Saved files are a
snapshot: re-run this to refresh them. www.uts.edu.my's robots.txt allows
crawling; requests are made one at a time with a pause in between.
"""
import logging
import re
import time
from io import BytesIO
from urllib.parse import urlparse

import httpx
from pypdf import PdfReader

from . import KNOWLEDGE_DIR
from .documents import SOURCES_FILE, clean_text

log = logging.getLogger("rag.fetch")

WEBSITE_DIR = KNOWLEDGE_DIR / "uts_website"
PAUSE_SECONDS = 2


def read_sources():
    if not SOURCES_FILE.exists():
        return []
    return [line.strip() for line in SOURCES_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def file_name(url):
    parsed = urlparse(url)
    path = parsed.path.strip("/") or "home"
    name = re.sub(r"[^a-zA-Z0-9]+", "_", path.rsplit("/", 1)[-1]).strip("_")
    # Pages on the school sites (scm.uts.edu.my/team, sfs.uts.edu.my/staff, ...) get the site as a prefix.
    site = parsed.hostname.split(".")[0] if parsed.hostname else ""
    if site not in ("www", "uts", ""):
        name = f"{site}_{name}"
    name = name.removesuffix("_pdf")
    # "undergraduate_session_1_20260702084935_jw0k6a8mln" -> "academic_calendar_undergraduate_session_1"
    calendar = re.match(r"^((?:foundation|undergraduate)_session_\d+)_\d{14}_\w+$", name)
    return f"academic_calendar_{calendar.group(1)}" if calendar else name or "home"


def save(url, response):
    if "pdf" in response.headers.get("content-type", "") or url.lower().endswith(".pdf"):
        reader = PdfReader(BytesIO(response.content))
        text = clean_text("\n\n".join(page.extract_text() or "" for page in reader.pages))
        path = WEBSITE_DIR / f"{file_name(url)}.md"
        path.write_text(f"Source: {url}\n{text}\n", encoding="utf-8")
    else:
        html = response.text
        if 'rel="canonical"' not in html:
            # Record where the page came from so search results can link to it.
            html = html.replace("<head>", f'<head>\n<link rel="canonical" href="{url}">', 1)
        path = WEBSITE_DIR / f"{file_name(url)}.html"
        path.write_text(html, encoding="utf-8")
    return path


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    WEBSITE_DIR.mkdir(exist_ok=True)
    urls = read_sources()
    saved = failed = 0
    with httpx.Client(follow_redirects=True, timeout=30) as client:
        for i, url in enumerate(urls):
            if i:
                time.sleep(PAUSE_SECONDS)
            try:
                response = client.get(url)
                response.raise_for_status()
                path = save(url, response)
            except Exception as error:
                failed += 1
                log.warning("Skipped %s: %s", url, str(error)[:120])
                continue
            saved += 1
            log.info("Saved %s -> %s", url, path.relative_to(KNOWLEDGE_DIR))
    log.info("%d saved, %d failed. Next: python -m campus.extract && python -m rag.ingest", saved, failed)


if __name__ == "__main__":
    main()
