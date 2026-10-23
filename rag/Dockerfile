# Build context: ./rag  (FastAPI internal RAG, :8001). See rag/README.md "실제 서비스 실행".
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

# app/generation/agent.py reads /app/prompts/counsel.md
COPY app ./app
COPY prompts ./prompts

# Shared volume with the Java backend: documents, FAISS revisions, query-result SQLite
RUN mkdir -p /var/lib/frangizer/faiss /var/lib/frangizer/rag

EXPOSE 8001

# Single worker: index jobs run in one background worker (rag/README.md)
CMD ["uvicorn", "app.bootstrap:create_service", "--factory", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
