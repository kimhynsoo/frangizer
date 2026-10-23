"""Shared embedding adapter: bounded query calls, retryable indexing calls, no implicit model."""
import os
import time
from typing import Protocol

import numpy as np
from openai import APIConnectionError, APIStatusError, OpenAI


class EmbeddingError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Embedder(Protocol):
    model: str
    dim: int
    def embed(self, texts: list[str]) -> np.ndarray: ...


class OpenAIEmbedder:
    def __init__(self, model, api_key, base_url='https://api.openai.com/v1', timeout=20, sleep=time.sleep,
                 *, dimensions=None, client=None):
        supported = {'text-embedding-3-small': 1536, 'text-embedding-3-large': 3072}
        if model not in supported or not api_key:
            raise EmbeddingError('EMBEDDING_NOT_CONFIGURED', '지원 모델과 임베딩 인증값을 설정해 주세요')
        self.model, self.dim = model, supported[model] if dimensions is None else dimensions
        if not 1 <= self.dim <= supported[model]:
            raise EmbeddingError('EMBEDDING_NOT_CONFIGURED', '임베딩 차원 설정을 확인해 주세요')
        self.client = client or OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)
        self.sleep = sleep

    def _request(self, part, retries):
        for attempt in range(retries + 1):
            try:
                return self.client.embeddings.create(
                    model=self.model, input=part, dimensions=self.dim, encoding_format='float',
                )
            except (APIConnectionError, APIStatusError) as exc:
                status = getattr(exc, 'status_code', None)
                retryable = status is None or status == 429 or status >= 500
                reason = f'HTTP {status}' if status else '연결 오류'
                if not retryable or attempt == retries:
                    raise EmbeddingError('EMBEDDING_FAILED', f'임베딩 호출 실패: {reason}') from None
                self.sleep(2 ** attempt)

    def embed_with_usage(self, texts, *, retries=0):
        # Query embedding never retries; indexing may retry transient failures.
        if not texts or any(not isinstance(t, str) or not t.strip() for t in texts):
            raise EmbeddingError('EMBEDDING_FAILED', '빈 임베딩 입력입니다')
        vectors, tokens = [], 0
        try:
            for start in range(0, len(texts), 64):
                part = texts[start:start + 64]
                response = self._request(part, retries)
                ordered = sorted(response.data, key=lambda item: item.index)
                if [item.index for item in ordered] != list(range(len(part))):
                    raise ValueError('embedding index mismatch')
                vectors.extend(item.embedding for item in ordered)
                tokens += response.usage.total_tokens if response.usage else 0
            array = np.asarray(vectors, dtype='float32')
            if (array.shape != (len(texts), self.dim) or not np.isfinite(array).all()
                    or np.any(np.linalg.norm(array, axis=1) == 0)):
                raise ValueError('invalid embedding vectors')
            return array, tokens
        except EmbeddingError:
            raise
        except Exception:
            raise EmbeddingError('EMBEDDING_FAILED', '임베딩 응답 개수·차원·벡터 형식을 확인해 주세요') from None

    def embed(self, texts):
        return self.embed_with_usage(texts, retries=3)[0]


def load_embedder() -> Embedder:
    try:
        raw_dim = os.environ.get('EMBEDDING_DIMENSIONS', '')
        return OpenAIEmbedder(
            os.environ.get('EMBEDDING_MODEL', ''),
            api_key=os.environ.get('EMBEDDING_API_KEY') or os.environ.get('LLM_API_KEY', ''),
            base_url=os.environ.get('EMBEDDING_BASE_URL') or 'https://api.openai.com/v1',
            dimensions=int(raw_dim) if raw_dim else None,
        )
    except (TypeError, ValueError):
        raise EmbeddingError('EMBEDDING_NOT_CONFIGURED', '임베딩 설정을 확인해 주세요') from None
