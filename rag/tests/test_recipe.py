import asyncio
import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from app.generation.recipe import RecipeShortcut, RagRuntime
from app.indexing.job import run_index_job
from app.retrieval.faiss_store import FaissRetriever
from app.schemas import InternalQuery
from tests.indexing.conftest import FakeEmbedder
from tests.support import query_payload

REPO = Path(__file__).resolve().parents[2]
STRUCTURED = REPO / 'data/structured/demo_cafe_recipes.json'


@pytest.fixture
def recipes(tmp_path):
    store, index = tmp_path / 'store', tmp_path / 'faiss'
    manifest = json.loads((REPO / 'data/manifest.json').read_text())
    jobs = {}
    for doc in manifest['documents']:
        if doc['local_key'] != 'demo-cafe-recipes':
            continue
        meta, server = doc['upload_metadata'], doc['server']
        target = store / server['storage_key']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / 'data' / doc['path'], target)
        req = {k: v for k, v in meta.items() if k != 'change_summary'}
        req.update(job_id='test-recipe-' + meta['version'], brand_id='brand-test',
                   document_id=server['document_id'], version_id=server['version_id'],
                   source=dict(storage_key=server['storage_key'], sha256=doc['file']['sha256'], media_type='application/pdf'))
        job = run_index_job(req, document_root=store, index_root=index, embedder=FakeEmbedder())
        assert job['status'] == 'succeeded', job
        jobs[meta['version']] = job
    return RecipeShortcut(STRUCTURED, FaissRetriever(index, FakeEmbedder())), jobs


def context(jobs, question, version='1.0', date='2026-10-23T12:00:00+09:00'):
    p = query_payload(question)
    p['as_of'] = date
    p['allowed_versions'] = [{k: jobs[version][k] for k in ('document_id', 'version_id', 'index_revision')}]
    return InternalQuery.model_validate(p)


@pytest.mark.parametrize('version,date,expected', [
    ('1.0', '2026-10-25T23:59:59+09:00', '20 mL'),
    ('1.1', '2026-10-26T00:00:00+09:00', '15 mL'),
    ('1.0', '2026-10-28T00:00:00+09:00', '20 mL'),  # v1.1 not published; Java still permits v1.0.
])
def test_approved_recipe_quantity_without_llm(recipes, version, date, expected):
    shortcut, jobs = recipes
    response = asyncio.run(shortcut.try_answer(context(jobs, '아이스 바닐라라떼 시럽 얼마 넣어?', version, date)))
    assert response is not None
    assert expected in response.result.answer
    assert response.result.sources[0].page == 5
    assert response.result.sources[0].version == version
    assert response.retrieval.llm_called is False
    assert response.usage.input_tokens == response.usage.output_tokens == response.usage.embedding_tokens == 0


def test_recipe_temperature_and_completed_step_need_clarification(recipes):
    shortcut, jobs = recipes
    response = asyncio.run(shortcut.try_answer(context(jobs, '카페라떼 우유 얼마 넣어?')))
    assert response.result.status == 'needs_review'
    assert response.result.follow_up_questions == ('HOT과 ICED 중 어느 레시피인가요?',)
    response = asyncio.run(shortcut.try_answer(context(jobs, '아이스 딸기라떼 다음 단계는?')))
    assert response.result.status == 'needs_review'
    response = asyncio.run(shortcut.try_answer(context(jobs, '아이스 딸기라떼 1단계 완료, 다음 단계는?')))
    assert response.result.status == 'answered' and response.result.steps[0].startswith('2.')


def test_same_quantities_do_not_need_temperature_question(recipes):
    shortcut, jobs = recipes
    response = asyncio.run(shortcut.try_answer(context(jobs, '바닐라라테 시럽 몇 mL예요?')))
    assert response.result.status == 'answered' and '20 mL' in response.result.answer
    assert 'HOT·ICED 공통' in response.result.answer
    assert not response.result.follow_up_questions and response.result.sources[0].page == 5
    assert not response.retrieval.llm_called


def test_staged_or_wrong_sha_never_supplies_quantity(recipes):
    shortcut, jobs = recipes
    # Future artifact exists on disk but is not present in allowed_versions.
    response = asyncio.run(shortcut.try_answer(context(jobs, '아이스 바닐라라떼 시럽 얼마?', '1.0')))
    assert '20 mL' in response.result.answer and '15 mL' not in response.result.answer
    response = asyncio.run(shortcut.try_answer(context(jobs, '아이스 바닐라라떼 시럽 얼마?', '1.1')))
    assert response.result.status == 'needs_review'
    shortcut.data = deepcopy(shortcut.data)
    shortcut.data['source_documents'][0]['sha256'] = '0' * 64
    response = asyncio.run(shortcut.try_answer(context(jobs, '아이스 바닐라라떼 시럽 얼마?')))
    assert response.result.status == 'needs_review'


def test_json_altered_value_falls_back_instead_of_returning_it(recipes):
    shortcut, jobs = recipes
    for record in shortcut.data['recipe_versions']:
        if record['menu_id'] == 'vanilla_latte':
            record['ingredients'][0]['amount'] = '999999'
    response = asyncio.run(shortcut.try_answer(context(jobs, '아이스 바닐라라떼 시럽 얼마?')))
    assert response is None


@pytest.mark.parametrize('question', ['아이스 바닐라라떼 두 잔 시럽 양', '아이스 바닐라라떼 시럽 적게 양',
                                    '바닐라라떼랑 카페모카 레시피 비교'])
def test_unsupported_recipe_questions_are_not_forced_into_shortcut(recipes, question):
    shortcut, jobs = recipes
    assert asyncio.run(shortcut.try_answer(context(jobs, question))) is None
