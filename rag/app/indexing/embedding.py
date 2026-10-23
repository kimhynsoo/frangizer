"""임베딩 연결 지점.

문서와 질문은 같은 모델·설정을 써야 한다(B 검색과 공유). 모델은 B·E와 합의 전이라
실제 제공자 호출은 아직 구현하지 않았다. EMBEDDING_MODEL이 없거나 알 수 없는 값이면
색인 작업은 EMBEDDING_NOT_CONFIGURED로 실패한다(가짜 벡터로 성공시키지 않음).
"""
import os
from typing import Protocol

import numpy as np


class EmbeddingError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Embedder(Protocol):
    model: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray:  # (n, dim) float32
        ...


def load_embedder() -> Embedder:
    model = os.environ.get("EMBEDDING_MODEL")
    if not model:
        raise EmbeddingError("EMBEDDING_NOT_CONFIGURED", "EMBEDDING_MODEL이 설정되지 않았다")
    # TODO(A·B): 합의한 제공자 연결. 질문 임베딩과 같은 클라이언트를 공유한다.
    raise EmbeddingError("EMBEDDING_NOT_CONFIGURED", f"지원하지 않는 EMBEDDING_MODEL: {model}")
