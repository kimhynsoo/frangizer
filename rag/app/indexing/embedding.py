"""Shared document/query embedding adapter. No implicit model or credentials."""
import os
from typing import Protocol

import numpy as np
from openai import OpenAI


class EmbeddingError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Embedder(Protocol):
    model: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


class OpenAIEmbedder:
    def __init__(self, model: str, *, api_key: str, dimensions: int | None = None, client=None):
        supported = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072}
        if model not in supported or not api_key:
            raise EmbeddingError("EMBEDDING_NOT_CONFIGURED", "지원 모델과 임베딩 인증값을 설정해 주세요")
        self.model, self.dim = model, supported[model] if dimensions is None else dimensions
        if not 1 <= self.dim <= supported[model]:
            raise EmbeddingError("EMBEDDING_NOT_CONFIGURED", "임베딩 차원 설정을 확인해 주세요")
        self.client = client or OpenAI(api_key=api_key, timeout=20, max_retries=0)

    def embed_with_usage(self, texts):
        if not texts or any(not isinstance(t, str) or not t.strip() for t in texts):
            raise EmbeddingError("EMBEDDING_FAILED", "빈 임베딩 입력입니다")
        vectors, tokens = [], 0
        try:
            for start in range(0, len(texts), 64):
                part = texts[start:start + 64]
                response = self.client.embeddings.create(
                    model=self.model, input=part, dimensions=self.dim, encoding_format="float",
                )
                ordered = sorted(response.data, key=lambda item: item.index)
                if [item.index for item in ordered] != list(range(len(part))):
                    raise ValueError("embedding index mismatch")
                vectors.extend(item.embedding for item in ordered)
                tokens += response.usage.total_tokens
            array = np.asarray(vectors, dtype="float32")
            if (array.shape != (len(texts), self.dim) or not np.isfinite(array).all()
                    or np.any(np.linalg.norm(array, axis=1) == 0)):
                raise ValueError("invalid embedding vectors")
            return array, tokens
        except Exception:
            raise EmbeddingError("EMBEDDING_FAILED", "임베딩 제공자 응답을 처리하지 못했습니다") from None

    def embed(self, texts):
        return self.embed_with_usage(texts)[0]


def load_embedder() -> Embedder:
    try:
        raw_dim = os.environ.get("EMBEDDING_DIMENSIONS", "")
        return OpenAIEmbedder(
            os.environ.get("EMBEDDING_MODEL", ""),
            api_key=os.environ.get("EMBEDDING_API_KEY") or os.environ.get("LLM_API_KEY", ""),
            dimensions=int(raw_dim) if raw_dim else None,
        )
    except (TypeError, ValueError):
        raise EmbeddingError("EMBEDDING_NOT_CONFIGURED", "임베딩 설정을 확인해 주세요") from None
