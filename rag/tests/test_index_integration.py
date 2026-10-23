import asyncio
import json
from copy import deepcopy

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from openai import OpenAI

from app.errors import ServiceError
from app.generation.agent import CounselAgent
from app.indexing.embedding import EmbeddingError, OpenAIEmbedder
from app.indexing.job import run_index_job
from app.indexing.service import IndexService
from app.main import create_app
from app.retrieval.faiss_store import FaissRetriever
from app.schemas import InternalQuery
from tests.indexing.conftest import make_request, roots, embedder, contract  # noqa: F401
from tests.support import AUTH, ScriptedModel, draft, headers, query_payload, tool_call


def context_for(req, job):
    payload = query_payload()
    payload['principal']['brand_id'] = req['brand_id']
    payload['allowed_versions'] = [{k: job[k] for k in ('document_id', 'version_id', 'index_revision')}]
    return InternalQuery.model_validate(payload)


def create_index(req, roots, embedder):
    return run_index_job(req, document_root=roots[0], index_root=roots[1], embedder=embedder)


def test_index_post_poll_get_and_contract(make_request, roots, embedder, contract):
    req = make_request()
    manager = IndexService(*roots, embedder=embedder)
    app = create_app(auth=AUTH, index_service=manager, state_db=str(roots[1] / 'replay.sqlite3'))
    with TestClient(app) as client:
        assert client.post('/internal/v1/index-jobs', content='bad').status_code == 401
        auth = {'X-Internal-Token': AUTH.internal_token}
        first = client.post('/internal/v1/index-jobs', json=req, headers=auth)
        assert first.status_code == 202
        contract('IndexJob', first.json()['data'])
        assert client.get('/internal/v1/index-jobs/missing', headers=auth).status_code == 404
        assert client.get('/internal/v1/index-jobs/bad%20id', headers=auth).status_code == 422
        changed = dict(req, title='conflicting title')
        assert client.post('/internal/v1/index-jobs', json=changed, headers=auth).status_code == 409
        # Closing waits for actual indexing threads; no timing-dependent sleeps in this test.
        schema = client.get('/openapi.json').json()
        assert schema['paths']['/internal/v1/index-jobs']['post']['security'] == [{'ServiceAuth': []}]
    done = manager.get(req['job_id'])
    assert done['status'] == 'succeeded', done
    contract('IndexJob', done)
    with TestClient(create_app(auth=AUTH, index_service=IndexService(*roots, embedder=embedder),
                               state_db=str(roots[1] / 'replay.sqlite3'))) as client:
        assert client.get('/internal/v1/index-jobs/' + req['job_id'], headers=auth).json()['data'] == done
        assert client.post('/internal/v1/index-jobs', json=req, headers=auth).json()['data'] == done


def test_index_queue_is_bounded(make_request, roots, embedder):
    manager = IndexService(*roots, embedder=embedder, max_pending=0)
    with TestClient(create_app(auth=AUTH, index_service=manager, state_db=str(roots[1] / 'r.sqlite3'))) as client:
        assert client.post('/internal/v1/index-jobs', json=make_request(),
                           headers={'X-Internal-Token': AUTH.internal_token}).status_code == 503


def test_real_artifact_retrieval_and_answer_warning(make_request, roots, embedder, contract):
    req = make_request()
    job = create_index(req, roots, embedder)
    context = context_for(req, job)
    retriever = FaissRetriever(roots[1], embedder)
    batch = asyncio.run(retriever.search('청약철회', context))
    assert batch.items and batch.items[0].legal_temporal_unreviewed
    cid = batch.items[0].metadata.chunk_id
    model = ScriptedModel(final=draft(used_chunk_ids=[cid]))
    response = asyncio.run(CounselAgent(model, retriever).run(context))
    contract('InternalAnswer', response.model_dump(mode='json'))
    assert '[시행일 미검수]' in response.result.answer
    assert response.result.sources[0].version_id == job['version_id']
    read = asyncio.run(retriever.read_sections((cid,), context))
    assert [item.metadata.chunk_id for item in read.items] == [cid]


def test_retrieval_filters_role_brand_and_period_before_embedding(make_request, roots, embedder):
    req = make_request(visibility='hq')
    job = create_index(req, roots, embedder)
    payload = context_for(req, job).model_dump(mode='json')
    class NoQueryCalls:
        model, dim = embedder.model, embedder.dim
        def embed(self, texts):
            pytest.fail('No permitted rows: query embedding must not be called')
    retriever = FaissRetriever(roots[1], NoQueryCalls())
    assert not asyncio.run(retriever.search('x', InternalQuery.model_validate(payload))).items
    payload['principal']['role'] = 'hq'
    payload['principal']['brand_id'] = 'other-brand'
    assert not asyncio.run(retriever.search('x', InternalQuery.model_validate(payload))).items
    payload['principal']['brand_id'] = req['brand_id']
    payload['as_of'] = '2026-01-01T00:00:00+09:00'
    assert not asyncio.run(retriever.search('x', InternalQuery.model_validate(payload))).items


def test_missing_revision_and_dimension_mismatch_fail_closed(make_request, roots, embedder):
    req = make_request()
    job = create_index(req, roots, embedder)
    context = context_for(req, job)
    embedder.dim += 1
    with pytest.raises(ServiceError) as err:
        asyncio.run(FaissRetriever(roots[1], embedder).search('x', context))
    assert err.value.status == 503
    payload = context.model_dump(mode='json')
    payload['allowed_versions'][0]['index_revision'] = 'idx-missing'
    with pytest.raises(ServiceError) as err:
        asyncio.run(FaissRetriever(roots[1], embedder).search('x', InternalQuery.model_validate(payload)))
    assert err.value.status == 503


def test_tampered_artifact_hash_is_rejected(make_request, roots, embedder):
    req = make_request()
    job = create_index(req, roots, embedder)
    path = roots[1] / 'revisions' / job['index_revision'] / 'chunks.jsonl'
    lines = path.read_text().splitlines()
    rec = json.loads(lines[0]); rec['text'] += ' altered'
    lines[0] = json.dumps(rec)
    path.write_text('\n'.join(lines) + '\n')
    with pytest.raises(ServiceError) as err:
        asyncio.run(FaissRetriever(roots[1], embedder).search('x', context_for(req, job)))
    assert err.value.status == 502


def test_embedding_http_format_order_usage_and_safe_error():
    def respond(request):
        payload = json.loads(request.content)
        assert payload['dimensions'] == 2 and payload['encoding_format'] == 'float'
        return httpx.Response(200, json={
            'object': 'list', 'model': 'text-embedding-3-small',
            'data': [{'object': 'embedding', 'index': 1, 'embedding': [0.0, 1.0]},
                     {'object': 'embedding', 'index': 0, 'embedding': [1.0, 0.0]}],
            'usage': {'prompt_tokens': 7, 'total_tokens': 7},
        })
    client = OpenAI(api_key='offline-not-real', http_client=httpx.Client(transport=httpx.MockTransport(respond)))
    adapter = OpenAIEmbedder('text-embedding-3-small', api_key='offline-not-real', dimensions=2, client=client)
    array, usage = adapter.embed_with_usage(['a', 'b'])
    assert np.array_equal(array, np.eye(2)) and usage == 7
    with pytest.raises(EmbeddingError):
        adapter.embed([''])
    client.close()


def test_forbidden_best_match_cannot_displace_permitted_row(make_request, roots, embedder):
    import faiss
    req = make_request()
    job = create_index(req, roots, embedder)
    directory = roots[1] / 'revisions' / job['index_revision']
    path = directory / 'chunks.jsonl'
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(records) > 1
    for n, rec in enumerate(records):
        rec['metadata']['visibility'] = 'owner' if n == 0 else 'hq'
    path.write_text('\n'.join(json.dumps(r) for r in records) + '\n')
    matrix = faiss.read_index(str(directory / 'index.faiss')).reconstruct_n(0, len(records))
    class ForbiddenBestQuery:
        model, dim = embedder.model, embedder.dim
        def embed(self, texts):
            return matrix[-1:].copy()  # Best possible match is HQ-only.
    response = asyncio.run(FaissRetriever(roots[1], ForbiddenBestQuery(), top_k=1).search('x', context_for(req, job)))
    assert [e.metadata.chunk_id for e in response.items] == [records[0]['metadata']['chunk_id']]


def test_dimension_changes_revision_and_zero_vectors_fail(make_request, roots, embedder):
    from app.indexing.job import revision_id
    from app.indexing.models import IndexJobRequest
    req = make_request()
    parsed = IndexJobRequest.model_validate(req)
    assert revision_id(parsed, embedder.model, 8) != revision_id(parsed, embedder.model, 16)
    class ZeroVectors:
        model, dim = embedder.model, embedder.dim
        def embed(self, texts):
            return np.zeros((len(texts), self.dim), dtype='float32')
    job = create_index(req, roots, ZeroVectors())
    assert (job['status'], job['error_code']) == ('failed', 'INDEX_VALIDATION_FAILED')


def test_unreviewed_table_cannot_be_returned_as_confirmed_numeric_answer():
    from dataclasses import replace
    from app.generation.answer import assemble_answer
    from app.schemas import Usage
    from tests.support import fixture_evidence
    item = replace(fixture_evidence(), quality_flags=('table_needs_review',))
    response = assemble_answer(InternalQuery.model_validate(query_payload()),
        draft(answer='확정 금액은 123456원입니다.'), {item.metadata.chunk_id: item},
        latency_ms=0, candidate_count=1,
        usage=Usage(input_tokens=0, output_tokens=0, embedding_tokens=0,
                    model='offline', embedding_model='not-used', estimated_cost_usd=None))
    assert response.result.status == 'needs_review'
    assert '123456' not in response.result.answer and not response.result.steps
    assert response.result.handoff_reason == '표 추출 품질 검수 필요'
