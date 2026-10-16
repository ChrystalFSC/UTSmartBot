FROM python:3.12-slim

# PORT 7860 is what Hugging Face Spaces expects; other hosts (e.g. Cloud Run) set PORT themselves.
# BEHIND_PROXY=1: these hosts put a proxy in front of the app, so the rate limiter reads the
# visitor's address from X-Forwarded-For. Set it to 0 if nothing sits in front of the container.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=7860 \
    BEHIND_PROXY=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download the local embedding model at build time (~2.2 GB) so containers start
# without fetching it. Must match the model that built data/index.npz.
ARG EMBEDDING_MODEL=intfloat/multilingual-e5-large
ENV EMBEDDING_MODEL=${EMBEDDING_MODEL}
COPY rag/__init__.py rag/embeddings.py rag/
RUN python -c "from rag.embeddings import get_embedder; get_embedder()"

COPY . .

# Hugging Face Spaces runs containers as user 1000; the first user created here gets that id.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser /app/.models
USER appuser

EXPOSE 7860

# One worker process, because each process loads its own copy of the embedding
# model (~2 GB RAM); threads keep many streaming responses open at once.
CMD exec gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 120 UTSmartBot:app
