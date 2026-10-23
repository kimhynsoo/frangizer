# Build context: ./voice  (internal STT, :8008). Whisper model is downloaded on first start into the cache volume.
FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY app.py ./

EXPOSE 8008
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8008", "--workers", "1"]
