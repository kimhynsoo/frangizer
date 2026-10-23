import hashlib
import json
import shutil
from pathlib import Path

import jsonschema
import numpy as np
import pytest
import yaml
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO = Path(__file__).resolve().parents[3]
SOURCE = REPO / "data" / "source"


class FakeEmbedder:
    """테스트 전용. 실제 모델이 아니며 색인 품질과 무관하다."""
    model = "test-fake-embedding"
    dim = 8

    def __init__(self, fail=False):
        self.fail = fail

    def embed(self, texts):
        if self.fail:
            raise RuntimeError("boom")
        out = np.zeros((len(texts), self.dim), dtype="float32")
        for i, t in enumerate(texts):
            h = hashlib.sha256(t.encode()).digest()
            out[i] = np.frombuffer(h[: self.dim * 4], dtype="uint32") / 2**32 + 0.01
        return out


@pytest.fixture
def embedder():
    return FakeEmbedder()


@pytest.fixture
def roots(tmp_path):
    doc_root = tmp_path / "store"
    (doc_root / "documents").mkdir(parents=True)
    return doc_root, tmp_path / "faiss"


def place(doc_root, src_name, key):
    dst = doc_root / key
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(SOURCE / src_name, dst)
    return hashlib.sha256(dst.read_bytes()).hexdigest()


@pytest.fixture
def make_request(roots):
    doc_root, _ = roots

    def _make(src_name="전자상거래법_청약철회_발췌_v2026-07-21.pdf", key="documents/ecommerce/v1.pdf", **over):
        sha = place(doc_root, src_name, key)
        req = {
            "job_id": "job-test-001", "brand_id": "demo-brand", "document_id": "doc-ecommerce",
            "version_id": "ver-ecommerce-1", "title": "전자상거래법(발췌)", "version": "2026-07-21",
            "category": "consumer", "material_type": "official", "is_demo": False, "visibility": "prospect",
            "effective_from": "2026-07-21T00:00:00+09:00",
            "original_source": {"title": "전자상거래법", "url": None, "edition": "2026-07-21",
                                "publisher": "법제처 국가법령정보센터", "acquired_on": "2026-10-23", "note": ""},
            "indexing_policy": {"exclude_pages": [1, 2], "legal_temporal_reviewed": False},
            "source": {"storage_key": key, "sha256": sha, "media_type": "application/pdf"},
        }
        for k, v in over.items():
            req[k] = v
        return req

    return _make


@pytest.fixture(scope="session")
def contract():
    """docs/api/internal-rag.openapi.yaml 스키마로 검증하는 함수."""
    spec = yaml.safe_load((REPO / "docs" / "api" / "internal-rag.openapi.yaml").read_text(encoding="utf-8"))
    reg = Registry().with_resource("urn:internal", Resource.from_contents(spec, default_specification=DRAFT202012))

    def validate(name, obj):
        v = jsonschema.Draft202012Validator({"$ref": f"urn:internal#/components/schemas/{name}"}, registry=reg,
                                            format_checker=jsonschema.FormatChecker())
        errors = [f"{list(e.path)}: {e.message}" for e in v.iter_errors(json.loads(json.dumps(obj)))]
        assert not errors, errors

    return validate
