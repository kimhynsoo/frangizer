import hashlib
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import jwt
from langchain_core.messages import AIMessage, ToolMessage

from app.auth import AuthSettings
from app.generation.model import Draft
from app.retrieval.base import Evidence, EvidenceBatch, is_allowed
from app.schemas import IndexedChunkMetadata, InternalQuery

AUTH = AuthSettings("offline-service-token", "offline-test-signing-key-at-least-32-bytes", "frangizer-test", "frangizer-rag")


def query_payload(question="테스트용 자료에서 확인 항목을 찾아줘"):
    return {
        "question_id": "q-test-001", "attempt_id": "attempt-test-001", "conversation_id": "conv-test-001",
        "question": question,
        "principal": {"user_id": "test-user", "role": "owner", "brand_id": "brand-test", "store_id": "store-test", "permissions": ["rag:ask"]},
        "as_of": "2026-10-23T10:00:00+09:00", "basis": "current", "scope_revision": "scope-test-001",
        "allowed_versions": [{"document_id": "fixture-document", "version_id": "fixture-v1", "index_revision": "fixture-index-v1"}],
        "history": [], "deadline_ms": 10000,
    }


def user_token(auth=AUTH, **changes):
    now = datetime.now(timezone.utc)
    claims = {"sub": "test-user", "role": "owner", "brand_id": "brand-test", "store_id": "store-test", "permissions": ["rag:ask"],
              "iss": auth.issuer, "aud": auth.audience, "iat": now, "exp": now + timedelta(hours=1)}
    claims.update(changes)
    return jwt.encode(claims, auth.jwt_secret, algorithm=auth.algorithm)


def headers(auth=AUTH, **changes):
    return {"X-Internal-Token": auth.internal_token, "Authorization": "Bearer " + user_token(auth, **changes)}


def fixture_evidence(chunk_id="fixture-chunk-1", **changes):
    text = "[테스트 전용 합성 데이터] 항목 A를 확인합니다. 실제 매장 정책이나 법령이 아닙니다."
    data = {
        "chunk_id": chunk_id, "brand_id": "brand-test", "document_id": "fixture-document", "version_id": "fixture-v1",
        "index_revision": "fixture-index-v1", "title": "테스트 전용 합성 검색 결과", "version": "1.0",
        "visibility": "owner", "material_type": "synthetic", "is_demo": True, "page": None,
        "article": None, "section_key": "fixture-section-1", "effective_from": "2026-10-01T00:00:00+09:00",
        "effective_until": None, "original_source": {"title": "테스트 fixture", "url": None, "edition": "1.0", "publisher": "frangizer tests", "acquired_on": "2026-10-23", "note": "검색 품질 평가에 사용하지 않음"},
        "embedding_model": "fixture-no-embeddings", "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }
    truncated = changes.pop("truncated", False)
    data.update(changes)
    return Evidence(text, IndexedChunkMetadata.model_validate(data), truncated)


class FixtureRetriever:
    ready = True

    def __init__(self, items=None):
        self.items = (fixture_evidence(),) if items is None else tuple(items)
        self.searches = []
        self.section_reads = []

    async def search(self, query, context):
        self.searches.append(query)
        return EvidenceBatch(tuple(i for i in self.items if is_allowed(i, context)), embedding_model="fixture-no-embeddings")

    async def read_sections(self, chunk_ids, context):
        self.section_reads.append(chunk_ids)
        sections = {i.metadata.section_key for i in self.items if i.metadata.chunk_id in chunk_ids}
        return EvidenceBatch(tuple(i for i in self.items if i.metadata.section_key in sections and is_allowed(i, context)))


def draft(**changes):
    data = {"answer": "테스트용 항목 A를 확인하세요. 실제 상담 답변이 아닙니다.", "steps": ["테스트 항목 A 확인"],
            "status": "answered", "used_chunk_ids": ["fixture-chunk-1"], "follow_up_questions": [], "handoff_reason": None, "category": "consumer"}
    data.update(changes)
    return Draft.model_validate(data)


def tool_call(name="search_documents", args=None, call_id="call-test-1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args or {"query": "테스트 확인 항목"}, "id": call_id, "type": "tool_call"}])


class ScriptedModel:
    """Deterministic model substitute; no question-understanding claim is made."""

    name = "scripted-test-model"

    def __init__(self, decisions=None, final=None):
        self.decisions = list(decisions if decisions is not None else [tool_call(), AIMessage(content="최종 작성")])
        self.final = final or draft()
        self.decide_calls = self.final_calls = 0
        self.seen_messages = []

    async def decide(self, messages):
        self.seen_messages = list(messages)
        self.decide_calls += 1
        return deepcopy(self.decisions[min(self.decide_calls - 1, len(self.decisions) - 1)])

    async def finalize(self, messages):
        self.seen_messages = list(messages)
        self.final_calls += 1
        return self.final, AIMessage(content="", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
