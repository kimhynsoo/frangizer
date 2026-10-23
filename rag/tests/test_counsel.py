import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from langchain_core.messages import AIMessage

from app.errors import ServiceError
from app.auth import AuthSettings
from app.generation.agent import CounselAgent
from app.main import create_app
from app.retrieval.base import EvidenceBatch, UnavailableRetriever
from app.schemas import InternalQuery
from .support import AUTH, FixtureRetriever, ScriptedModel, draft, fixture_evidence, headers, query_payload, tool_call, user_token

SPEC = yaml.safe_load((Path(__file__).resolve().parents[2] / "docs/api/internal-rag.openapi.yaml").read_text())


def validate_contract(payload, schema):
    document = {"$ref": f"#/components/schemas/{schema}", "components": SPEC["components"]}
    Draft202012Validator(document, format_checker=FormatChecker()).validate(payload)


def client_for(tmp_path, model=None, retriever=None, demo=False):
    model = model or ScriptedModel()
    retriever = retriever or FixtureRetriever()
    app = create_app(auth=AUTH, agent=CounselAgent(model, retriever), state_db=str(tmp_path / "results.sqlite3"), demo=demo)
    return TestClient(app), model, retriever


def test_answer_sources_usage_and_contract(tmp_path):
    client, model, retriever = client_for(tmp_path)
    with client:
        response = client.post("/rag-query", headers=headers(), json=query_payload())
        assert response.status_code == 200, response.text
        payload = response.json()
        validate_contract(payload["data"], "InternalAnswer")
        result = payload["data"]["result"]
        assert result["answer"].startswith("합성 시연 자료 기준")
        assert result["sources"][0]["page"] is None
        assert result["sources"][0]["file_url"].endswith("/file")
        assert payload["data"]["retrieval"]["used_chunk_ids"] == ["fixture-chunk-1"]
        assert payload["data"]["usage"]["input_tokens"] == 10
        assert result["classification_status"] == "pending"
        assert len(retriever.searches) == 1


@pytest.mark.parametrize("changes,auth_changes,status", [
    ({}, {"role": "hq"}, 403),
    ({}, {"brand_id": "another-brand"}, 403),
    ({}, {"store_id": "another-store"}, 403),
    ({}, {"permissions": []}, 403),
    ({"source": "pdf"}, {}, 422),
    ({"question": " "}, {}, 422),
    ({"question": "x" * 2001}, {}, 422),
    ({"as_of": "2026-10-23T00:00:00"}, {}, 422),
    ({"deadline_ms": True}, {}, 422),
])
def test_reject_invalid_request(tmp_path, changes, auth_changes, status):
    client, model, _ = client_for(tmp_path)
    body = query_payload(); body.update(changes)
    with client:
        response = client.post("/rag-query", headers=headers(**auth_changes), json=body)
        assert response.status_code == status, response.text
        validate_contract(response.json(), "Error")
        assert model.decide_calls == 0


@pytest.mark.parametrize("claim", [{"exp": datetime.now(timezone.utc) - timedelta(seconds=1)}, {"iss": "wrong"}, {"aud": "wrong"}])
def test_invalid_jwt(tmp_path, claim):
    client, _, _ = client_for(tmp_path)
    with client:
        response = client.post("/rag-query", headers=headers(**claim), json=query_payload())
        assert response.status_code == 401
        validate_contract(response.json(), "Error")


def test_bad_signature_service_token_and_missing_token(tmp_path):
    client, _, _ = client_for(tmp_path)
    with client:
        for h in [{}, {"X-Internal-Token": "wrong"}, {**headers(), "Authorization": "Bearer invalid"}]:
            assert client.post("/rag-query", headers=h, json=query_payload()).status_code == 401


def test_no_evidence_and_clarification(tmp_path):
    final = draft(status="needs_review", used_chunk_ids=[], steps=[], follow_up_questions=["어떤 상황인지 알려주세요."], answer="확인할 정보가 필요합니다.")
    client, _, _ = client_for(tmp_path, ScriptedModel([AIMessage(content="되묻기")], final), FixtureRetriever([]))
    with client:
        response = client.post("/rag-query", headers=headers(), json=query_payload())
        assert response.status_code == 200
        result = response.json()["data"]["result"]
        assert result["sources"] == [] and result["needs_review"] and not result["handoff"]
        validate_contract(response.json()["data"], "InternalAnswer")


def test_handoff_is_guidance(tmp_path):
    final = draft(status="handoff", handoff_reason="최종 판단은 담당자 확인이 필요합니다.")
    client, _, _ = client_for(tmp_path, ScriptedModel(final=final))
    with client:
        response = client.post("/rag-query", headers=headers(), json=query_payload())
        assert response.status_code == 200
        assert response.json()["data"]["result"]["handoff"]
        validate_contract(response.json()["data"], "InternalAnswer")


def test_repeat_search_and_section_expansion(tmp_path):
    decisions = [tool_call(), tool_call(call_id="call-2"), tool_call("read_sections", {"chunk_ids": ["fixture-chunk-1"]}, "call-3")]
    client, model, retriever = client_for(tmp_path, ScriptedModel(decisions))
    with client:
        response = client.post("/rag-query", headers=headers(), json=query_payload())
        assert response.status_code == 200, response.text
        assert len(retriever.searches) == 2 and len(retriever.section_reads) == 1
        assert model.decide_calls + model.final_calls == 4


def test_infinite_search_is_bounded(tmp_path):
    client, model, retriever = client_for(tmp_path, ScriptedModel([tool_call()]))
    with client:
        assert client.post("/rag-query", headers=headers(), json=query_payload()).status_code == 200
        assert len(retriever.searches) == 2 and model.decide_calls + model.final_calls <= 4


@pytest.mark.parametrize("final,items", [
    (draft(used_chunk_ids=["invented-chunk"]), None),
    (draft(used_chunk_ids=[]), []),
    (draft(), [fixture_evidence(truncated=True)]),
])
def test_hallucinated_or_missing_or_truncated_sources_rejected(tmp_path, final, items):
    client, _, _ = client_for(tmp_path, ScriptedModel(final=final), FixtureRetriever(items))
    with client:
        response = client.post("/rag-query", headers=headers(), json=query_payload())
        assert response.status_code == 502
        validate_contract(response.json(), "Error")


def test_scope_filter_before_return_and_guard_untrusted_adapter(tmp_path):
    items = [fixture_evidence(), fixture_evidence("foreign", brand_id="brand-other"), fixture_evidence("future", effective_from="2027-01-01T00:00:00+09:00")]
    client, _, _ = client_for(tmp_path, retriever=FixtureRetriever(items))
    with client:
        response = client.post("/rag-query", headers=headers(), json=query_payload())
        assert response.status_code == 200
        assert response.json()["data"]["retrieval"]["candidate_count"] == 1
    class LeakyRetriever(FixtureRetriever):
        async def search(self, query, context):
            return EvidenceBatch(tuple(items))
    client, _, _ = client_for(tmp_path, retriever=LeakyRetriever())
    with client:
        body = query_payload(); body["attempt_id"] = "leaky-attempt"
        assert client.post("/rag-query", headers=headers(), json=body).status_code == 502


def test_unknown_tool_and_unseen_section_are_not_executed(tmp_path):
    for call in [tool_call("delete_documents"), tool_call("read_sections", {"chunk_ids": ["unseen"]})]:
        client, _, retriever = client_for(tmp_path, ScriptedModel([call]))
        with client:
            assert client.post("/rag-query", headers=headers(), json=query_payload()).status_code == 502
            assert retriever.section_reads == []


def test_replay_conflict_and_restart(tmp_path):
    client, model, _ = client_for(tmp_path)
    with client:
        first = client.post("/rag-query", headers=headers(), json=query_payload())
        second = client.post("/rag-query", headers=headers(), json=query_payload())
        assert first.json()["data"] == second.json()["data"] and model.final_calls == 1
        conflict = query_payload("다른 질문")
        assert client.post("/rag-query", headers=headers(), json=conflict).status_code == 409
    client, model, _ = client_for(tmp_path)
    with client:
        replay = client.post("/rag-query", headers=headers(), json=query_payload())
        assert replay.json()["data"] == first.json()["data"] and model.decide_calls == 0


def test_model_failure_is_not_review_and_does_not_echo_secret(tmp_path):
    class FailedModel(ScriptedModel):
        async def decide(self, messages):
            raise RuntimeError("SECRET_PROVIDER_ERROR customer@example.com")
    client, _, _ = client_for(tmp_path, FailedModel())
    with client:
        response = client.post("/rag-query", headers=headers(), json=query_payload())
        assert response.status_code == 502
        assert "SECRET_PROVIDER_ERROR" not in response.text


def test_deadline(tmp_path):
    class SlowModel(ScriptedModel):
        async def decide(self, messages):
            await asyncio.sleep(2)
            return await super().decide(messages)
    client, _, _ = client_for(tmp_path, SlowModel())
    body = query_payload(); body["deadline_ms"] = 1000
    with client:
        assert client.post("/rag-query", headers=headers(), json=body).status_code == 504


def test_pii_masking_and_history(tmp_path):
    client, model, _ = client_for(tmp_path)
    body = query_payload("고객명: 홍길동 연락처 010-1234-5678 customer@example.com")
    body["history"] = [{"question": "주문번호: ABC123", "answer": "010-1234-5678"}]
    with client:
        assert client.post("/rag-query", headers=headers(), json=body).status_code == 200
        text = " ".join(str(m.content) for m in model.seen_messages)
        for secret in ["홍길동", "010-1234-5678", "customer@example.com", "ABC123"]:
            assert secret not in text


def test_readiness_unavailable_and_explicit_demo(tmp_path):
    with TestClient(create_app(auth=AUTH, state_db=":memory:")) as client:
        assert client.get("/health").status_code == 401
        assert client.get("/health", headers=headers()).json() == {"status": "not_ready"}
        assert client.post("/rag-query", headers=headers(), json=query_payload()).status_code == 503
    client, _, _ = client_for(tmp_path, demo=True)
    with client:
        response = client.get("/health", headers=headers())
        assert response.status_code == 503 and response.headers["X-Frangizer-Test-Mode"] == "synthetic-fixture"


def test_same_request_concurrently_executes_once(tmp_path):
    class SlowOnce(ScriptedModel):
        async def decide(self, messages):
            await asyncio.sleep(0.02)
            return await super().decide(messages)

    async def run():
        model = SlowOnce()
        app = create_app(auth=AUTH, agent=CounselAgent(model, FixtureRetriever()), state_db=str(tmp_path / "concurrent.sqlite3"))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                a, b = await asyncio.gather(*[
                    client.post("/rag-query", headers=headers(), json=query_payload()) for _ in range(2)
                ])
                assert a.status_code == b.status_code == 200
                assert a.json()["data"] == b.json()["data"] and model.final_calls == 1
    asyncio.run(run())


def test_failed_attempt_replays_same_error(tmp_path):
    class Failure(ScriptedModel):
        async def decide(self, messages):
            self.decide_calls += 1
            raise ServiceError(502, "UPSTREAM_ERROR", "모델을 호출하지 못했습니다.", retryable=True)
    client, model, _ = client_for(tmp_path, Failure())
    with client:
        for _ in range(2):
            response = client.post("/rag-query", headers=headers(), json=query_payload())
            assert response.status_code == 502
        assert model.decide_calls == 1


def test_wrong_signature_and_unsigned_jwt(tmp_path):
    valid = jwt.decode(user_token(), AUTH.jwt_secret, algorithms=["HS256"], audience=AUTH.audience)
    client, _, _ = client_for(tmp_path)
    with client:
        for token in [jwt.encode(valid, "wrong-signing-key-of-at-least-32-bytes", algorithm="HS256"),
                      jwt.encode(valid, "", algorithm="none")]:
            h = headers(); h["Authorization"] = "Bearer " + token
            assert client.post("/rag-query", headers=h, json=query_payload()).status_code == 401


@pytest.mark.parametrize("changes", [
    {"visibility": "hq"}, {"version_id": "other-version"}, {"index_revision": "other-index"},
    {"effective_until": "2026-10-23T10:00:00+09:00"},
])
def test_disallowed_metadata_is_filtered(changes):
    context = InternalQuery.model_validate(query_payload())
    retriever = FixtureRetriever([fixture_evidence(**changes)])
    assert asyncio.run(retriever.search("test", context)).items == ()


def test_bad_json_duplicate_scope_and_history_limit(tmp_path):
    client, _, _ = client_for(tmp_path)
    with client:
        response = client.post("/rag-query", headers={**headers(), "Content-Type": "application/json"}, content="{")
        assert response.status_code == 400
        validate_contract(response.json(), "Error")
        body = query_payload(); body["allowed_versions"] *= 2
        assert client.post("/rag-query", headers=headers(), json=body).status_code == 422
        body = query_payload(); body["history"] = [{"question": "a", "answer": "b"}] * 7
        assert client.post("/rag-query", headers=headers(), json=body).status_code == 422


def test_openapi_exposes_existing_query_schema_and_both_authentication_methods(tmp_path):
    client, _, _ = client_for(tmp_path)
    with client:
        schema = client.get("/openapi.json").json()
        operation = schema["paths"]["/rag-query"]["post"]
        assert operation["security"] == [{"ServiceAuth": [], "BearerAuth": []}]
        assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("InternalQuery")
        assert set(schema["components"]["schemas"]["InternalQuery"]["required"]) == set(SPEC["components"]["schemas"]["InternalQuery"]["required"])


@pytest.mark.parametrize("role,store_id", [("owner", "store-test"), ("hq", None), ("prospect", None)])
def test_reported_backend_jwt_settings_and_null_store_claim(tmp_path, role, store_id):
    """C's reported claim shape, locally signed; not a real backend integration test."""
    auth = AuthSettings(AUTH.internal_token, AUTH.jwt_secret, "frangizer", "frangizer-api")
    body = query_payload()
    body["principal"].update(role=role, store_id=store_id)
    app = create_app(
        auth=auth, agent=CounselAgent(ScriptedModel(), FixtureRetriever([fixture_evidence(visibility="prospect")])),
        state_db=str(tmp_path / "backend-claims.sqlite3"),
    )
    with TestClient(app) as client:
        response = client.post("/rag-query", headers=headers(auth, role=role, store_id=store_id), json=body)
        assert response.status_code == 200, response.text
        validate_contract(response.json()["data"], "InternalAnswer")
