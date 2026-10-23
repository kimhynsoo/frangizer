"""임베딩 연결 지점.

문서와 질문은 같은 모델·설정을 써야 한다(B 검색과 공유). 모델을 바꾸면 전체 재색인.
초기 제안(B 정리, 2026-10-23): text-embedding-3-small, 1536차원 — A·B·E 최종 합의·품질 검증 전.

환경변수
- EMBEDDING_MODEL: 아래 MODELS 중 하나. 없거나 모르는 값이면 EMBEDDING_NOT_CONFIGURED로 실패(가짜 벡터로 성공시키지 않음)
- LLM_API_KEY: OpenAI 호환 API 키 (계약 11.3의 환경변수 목록을 따른다)
- EMBEDDING_BASE_URL: 기본 https://api.openai.com/v1 (테스트·프록시용)

B가 LangChain OpenAIEmbeddings(model=같은 이름)로 질문을 임베딩하면 같은 벡터 공간이다.
색인 쪽은 벡터를 L2 정규화해 내적으로 검색한다(= 코사인). 질문 벡터도 정규화해야 한다.
"""
import json
import os
import time
import urllib.error
import urllib.request
from typing import Protocol

import numpy as np

MODELS = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072}
BATCH = 64
RETRIES = 3


class EmbeddingError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Embedder(Protocol):
    model: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray:  # (n, dim) float32
        ...


class OpenAIEmbedder:
    def __init__(self, model, api_key, base_url="https://api.openai.com/v1", timeout=60, sleep=time.sleep):
        self.model, self.dim = model, MODELS[model]
        self._key, self._url = api_key, base_url.rstrip("/") + "/embeddings"
        self._timeout, self._sleep = timeout, sleep

    def _post(self, batch):
        body = json.dumps({"model": self.model, "input": batch, "encoding_format": "float"}).encode()
        req = urllib.request.Request(self._url, data=body, method="POST", headers={
            "Authorization": f"Bearer {self._key}", "Content-Type": "application/json"})
        for attempt in range(RETRIES + 1):
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                retryable = e.code == 429 or e.code >= 500
                reason = f"HTTP {e.code}"
            except (urllib.error.URLError, TimeoutError) as e:
                retryable, reason = True, type(e).__name__
            if not retryable or attempt == RETRIES:
                raise EmbeddingError("EMBEDDING_FAILED", f"임베딩 호출 실패: {reason}")  # 키·본문은 남기지 않는다
            self._sleep(2 ** attempt)

    def embed(self, texts):
        out = []
        for i in range(0, len(texts), BATCH):
            batch = texts[i:i + BATCH]
            data = self._post(batch).get("data", [])
            if len(data) != len(batch):
                raise EmbeddingError("EMBEDDING_FAILED", f"응답 개수 불일치: {len(data)}/{len(batch)}")
            vecs = [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]
            if any(len(v) != self.dim for v in vecs):
                raise EmbeddingError("EMBEDDING_FAILED", "응답 차원 불일치")
            out.extend(vecs)
        return np.asarray(out, dtype="float32").reshape(len(texts), self.dim)


def load_embedder() -> Embedder:
    model = os.environ.get("EMBEDDING_MODEL")
    if not model:
        raise EmbeddingError("EMBEDDING_NOT_CONFIGURED", "EMBEDDING_MODEL이 설정되지 않았다")
    if model not in MODELS:
        raise EmbeddingError("EMBEDDING_NOT_CONFIGURED", f"지원하지 않는 EMBEDDING_MODEL: {model}")
    key = os.environ.get("LLM_API_KEY")
    if not key:
        raise EmbeddingError("EMBEDDING_NOT_CONFIGURED", "LLM_API_KEY가 설정되지 않았다")
    return OpenAIEmbedder(model, key, os.environ.get("EMBEDDING_BASE_URL", "https://api.openai.com/v1"))
