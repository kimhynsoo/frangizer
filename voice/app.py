"""내부 STT 서비스(:8008). Java 게이트웨이만 X-Internal-Token으로 호출한다.

POST /internal/v1/stt  multipart `audio`(WebM·WAV) → {"data": {"text", "duration_ms"}}
GET  /health           모델 로드 전 503

음성은 임시 폴더에만 쓰고 응답 전에 지운다(원본 저장 금지). 파일명은 사용자 값을 쓰지 않는다.
"""
import hmac
import os
import subprocess
import tempfile
import threading
import traceback
import wave
from pathlib import Path

from fastapi import FastAPI, File, Header, UploadFile
from fastapi.responses import JSONResponse

MAX_BYTES = 10 * 1024 * 1024
MAX_MS = 60_000
INTERNAL_TOKEN = os.environ["INTERNAL_TOKEN"]
MODEL_NAME = os.environ.get("WHISPER_MODEL", "small")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
model = None
# ponytail: CPU 하나에서 한 번에 한 건씩 인식한다. 동시 사용이 늘면 워커·GPU를 늘린다.
lock = threading.Lock()


def _load():
    global model
    try:
        from faster_whisper import WhisperModel  # 무거운 import는 서버가 뜬 뒤에

        model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
    except Exception:
        # 503에 조용히 머무르지 않고 종료해서 compose 재시작과 로그로 드러나게 한다
        traceback.print_exc()
        os._exit(1)


@app.on_event("startup")
def startup():
    threading.Thread(target=_load, daemon=True).start()  # 첫 실행은 모델 내려받기로 수 분 걸린다


def error(status, code, message):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@app.get("/health")
def health():
    return {"status": "ready"} if model else JSONResponse(status_code=503, content={"status": "not_ready"})


@app.post("/internal/v1/stt")
def stt(audio: UploadFile = File(...), x_internal_token: str | None = Header(default=None)):
    if x_internal_token is None or not hmac.compare_digest(x_internal_token.encode(), INTERNAL_TOKEN.encode()):
        return error(401, "AUTH_REQUIRED", "서비스 인증이 필요합니다.")
    if model is None:
        return error(503, "SERVICE_UNAVAILABLE", "음성 모델을 준비하고 있습니다.")
    data = audio.file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        return error(413, "PAYLOAD_TOO_LARGE", "음성은 10MB 이하로 보내 주세요.")

    with tempfile.TemporaryDirectory() as tmp:
        src, wav = Path(tmp, "in"), Path(tmp, "in.wav")
        src.write_bytes(data)
        # 형식과 상관없이 16kHz 모노 WAV로 바꾼다. 변환이 안 되면 지원하지 않는 음성으로 본다.
        proc = subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src), "-ac", "1", "-ar", "16000", "-vn", str(wav)],
            capture_output=True, timeout=30,
        )
        if proc.returncode != 0:
            return error(415, "UNSUPPORTED_MEDIA_TYPE", "WebM 또는 WAV 음성만 인식할 수 있습니다.")
        with wave.open(str(wav)) as w:
            duration_ms = round(w.getnframes() * 1000 / w.getframerate())
        if duration_ms > MAX_MS:
            return error(422, "VALIDATION_ERROR", "음성은 60초 이하로 녹음해 주세요.")
        with lock:
            segments, _ = model.transcribe(str(wav), language="ko", beam_size=5, vad_filter=True)
            text = "".join(s.text for s in segments).strip()
    return {"data": {"text": text, "duration_ms": duration_ms}}
