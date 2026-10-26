import asyncio
import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from app.generation.recipe import RecipeShortcut, RagRuntime, parse_request
from app.indexing.job import run_index_job
from app.retrieval.faiss_store import FaissRetriever
from app.schemas import HistoryTurn, InternalQuery
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


def test_temperature_reply_reuses_only_latest_request_and_current_sources(recipes):
    shortcut, jobs = recipes
    for previous_question in ('바닐라라떼 레시피 알려줘', '아메리카노 레시피', '바닐라라떼 우유 얼마?'):
        original = context(jobs, previous_question)
        pending = asyncio.run(shortcut.try_answer(original)).result
        assert pending.follow_up_questions == ('HOT과 ICED 중 어느 레시피인가요?',)
        for reply, label in [('핫', 'HOT'), ('아이스', 'ICED'), ('핫이요', 'HOT'),
                             ('아이스로 주세요', 'ICED'), ('HOT', 'HOT'), ('iced', 'ICED')]:
            followup = original.model_copy(update={'question': reply, 'history': (
                HistoryTurn(question=previous_question, answer=pending.answer),)})
            result = asyncio.run(shortcut.try_answer(followup))
            assert result.result.status == 'answered'
            assert label in result.result.answer and result.result.sources
            assert result.retrieval.llm_called is False
            if '우유' in previous_question:
                assert ('210 mL' if label == 'HOT' else '170 mL') in result.result.answer
            else:
                assert result.result.steps
    # Do not skip an unrelated latest turn or swallow a new substitution request.
    for previous, reply in [('내일 직원 몇 명?', '아이스'),
                            ('바닐라라떼랑 아메리카노 레시피', '핫'),
                            ('바닐라라떼 레시피', '아이스 우유 대신 두유')]:
        followup = context(jobs, reply).model_copy(update={'history': (
            HistoryTurn(question='바닐라라떼 레시피', answer='HOT과 ICED 중 어느 레시피인가요?'),
            HistoryTurn(question=previous, answer='확인 필요'),)})
        assert asyncio.run(shortcut.try_answer(followup)) is None
    assert asyncio.run(shortcut.try_answer(context(jobs, '핫'))) is None


def test_recipe_temperature_and_completed_step_need_clarification(recipes):
    shortcut, jobs = recipes
    response = asyncio.run(shortcut.try_answer(context(jobs, '카페라떼 우유 얼마 넣어?')))
    assert response.result.status == 'needs_review'
    assert response.result.follow_up_questions == ('HOT과 ICED 중 어느 레시피인가요?',)
    assert response.result.answer == 'HOT과 ICED 중 어느 레시피인가요?'
    response = asyncio.run(shortcut.try_answer(context(jobs, '아이스 딸기라떼 다음 단계는?')))
    assert response.result.status == 'needs_review'
    response = asyncio.run(shortcut.try_answer(context(jobs, '아이스 딸기라떼 1단계 완료, 다음 단계는?')))
    assert response.result.status == 'answered' and '다음은 2단계' in response.result.answer
    assert not response.result.steps[0].startswith('2.')


def test_same_quantities_do_not_need_temperature_question(recipes):
    shortcut, jobs = recipes
    response = asyncio.run(shortcut.try_answer(context(jobs, '바닐라라테 시럽 몇 mL예요?')))
    assert response.result.status == 'answered' and '20 mL' in response.result.answer
    assert 'HOT·ICED 공통' in response.result.answer
    assert not response.result.follow_up_questions and response.result.sources[0].page == 5
    assert not response.retrieval.llm_called


@pytest.mark.parametrize('version,date,count,amount', [
    ('1.0', '2026-10-25T12:00:00+09:00', '4회', '20 mL'),
    ('1.1', '2026-10-26T00:00:00+09:00', '3회', '15 mL'),
])
def test_pumps_use_published_source_mapping_without_llm(recipes, version, date, count, amount):
    shortcut, jobs = recipes
    response = asyncio.run(shortcut.try_answer(context(jobs, '바닐라 라떼 몇 펌프 넣어요?', version, date)))
    assert response.result.status == 'answered'
    assert count in response.result.answer and amount in response.result.answer
    assert 'HOT·ICED 공통' in response.result.answer
    assert not response.retrieval.llm_called and response.usage.embedding_tokens == 0


def test_full_recipe_contains_ingredients_and_unnumbered_steps(recipes):
    shortcut, jobs = recipes
    response = asyncio.run(shortcut.try_answer(context(jobs, '아이스 바닐라 라떼 레시피 알려줘')))
    assert response.result.status == 'answered'
    assert '재료:' in response.result.answer and '우유 170 mL' in response.result.answer
    assert len(response.result.steps) == 3
    assert response.result.steps[0].startswith('바닐라 시럽')
    assert not response.retrieval.llm_called


def test_missing_or_tampered_pump_mapping_is_not_inferred(recipes):
    shortcut, jobs = recipes
    for record in shortcut.data['recipe_versions']:
        if record['menu_id'] == 'vanilla_latte':
            for rule in record['order_rules']['pumps']:
                rule['pumps'] = '99'
    response = asyncio.run(shortcut.try_answer(context(jobs, '아이스 바닐라 라떼 몇 펌프?')))
    assert response.result.status == 'needs_review' and '99' not in response.result.answer
    assert '20 mL' in response.result.answer and not response.retrieval.llm_called


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


@pytest.mark.parametrize('question', ['아이스 바닐라라떼 시럽 적게 양',
                                    '바닐라라떼랑 카페모카 레시피 비교'])
def test_unsupported_recipe_questions_are_not_forced_into_shortcut(recipes, question):
    shortcut, jobs = recipes
    assert asyncio.run(shortcut.try_answer(context(jobs, question))) is None


@pytest.mark.parametrize('question', [
    '아이스 바닐라 라떼 시럽 양, 두유로 바꿔도 돼?',
    '아이스 바닐라 라떼 우유 대신 두유 넣는 레시피 알려줘',
    '아이스 바닐라 라떼 시럽 10 mL 넣어도 돼?',
    '아이스 바닐라 라떼 지난주랑 시럽 양 달라?',
    '아이스 바닐라 라떼 우유 몇 밀리 넣고 알레르기 괜찮아?',
    '그건 몇 펌프 넣어?',
])
def test_unconsumed_request_never_qualifies_for_fast_answer(question):
    assert parse_request(question, json.loads(STRUCTURED.read_text())) is None


def test_recipe_request_coverage_and_clarification(recipes):
    shortcut, jobs = recipes
    cases = [
        ('아이스 바닐라 라떼 우유 몇 밀리 넣어?', 'answered', ('170 mL',)),
        ('아이스 바닐라 라떼 세 잔 시럽 얼마?', 'needs_review', ()),
        ('아이스 바닐라 라떼 12잔 시럽 얼마?', 'needs_review', ()),
        ('아이스 바닐라 라떼 달지 않게 시럽 얼마?', 'answered', ('덜 달게', '15 mL')),
        ('바닐라 라떼 덜 달게 몇 펌프?', 'answered', ('3회', '15 mL')),
        ('아이스 바닐라 라떼 레시피랑 펌프 횟수 알려줘', 'answered', ('재료:', '4회')),
        ('아이스 바닐라 라떼 재료 알려줘', 'answered', ('60 mL', '20 mL', '170 mL', '180 g')),
        ('아이스 바닐라 라떼 우유와 시럽 양', 'answered', ('170 mL', '20 mL')),
        ('아이스 바닐라 라떼 시럽 빼기 양', 'answered', ('0 mL',)),
        ('아이스 바닐라 라떼 1단계 완료, 다음 단계랑 우유 양', 'answered', ('2단계', '170 mL')),
    ]
    for question, status, expected in cases:
        response = asyncio.run(shortcut.try_answer(context(jobs, question)))
        assert response is not None, question
        assert response.result.status == status, (question, response.result)
        assert all(t in response.result.answer for t in expected), (question, response.result)
        assert not response.retrieval.llm_called and response.usage.embedding_tokens == 0
        if status == 'needs_review':
            assert response.result.follow_up_questions and '20 mL' not in response.result.answer


def test_unsupported_qualifier_reaches_counselor_without_losing_question(recipes):
    shortcut, jobs = recipes

    class Counselor:
        retriever = shortcut.retriever
        seen = None

        async def run(self, ctx):
            self.seen = ctx
            return 'counselor-result'

    counselor = Counselor()
    runtime = RagRuntime(counselor, shortcut)
    ctx = context(jobs, '아이스 바닐라 라떼 시럽 양, 두유로 바꿔도 돼?')
    assert asyncio.run(runtime.run(ctx)) == 'counselor-result'
    assert counselor.seen is ctx


def test_recipe_item_lookup_binds_only_assessed_source_units(recipes, monkeypatch):
    from app.generation.review import AnswerItem, Grounding, Quote, RecipeLookup, block_failures
    shortcut, jobs = recipes
    ctx = context(jobs, '아이스 바닐라 라떼 시럽 양, 두유로 바꿔도 돼?')
    candidates = asyncio.run(shortcut.retriever.recipe_candidates(ctx, shortcut.data['doc_id']))
    evidence = {e.metadata.chunk_id: e for _, items in candidates for e in items if e.metadata.page == 5}
    refs = [Quote(chunk_id=cid, unit_id='unit-0') for cid in evidence]
    item = AnswerItem(item_id='quantity', requirement='아이스 바닐라 라떼 시럽 양',
        grounding=Grounding(evidence_status='complete', applicability='established',
            source_scope='레시피', question_scope='아이스 바닐라 라떼 시럽 양'),
        evidence_refs=refs, conditions=[], missing_information=None,
        lookup=RecipeLookup(operation='quantity', menu_id='vanilla_latte', temperature='ICED',
                            ingredients=['바닐라 시럽'], option=None, servings=1))
    found = asyncio.run(shortcut.resolve_item(ctx, item, evidence))
    assert found is not None and '20 mL' in found.blocks[0].text
    from tests.support import assessment
    assert not block_failures(found.blocks, assessment(items=[item]), evidence)
    assert asyncio.run(shortcut.resolve_item(ctx, item.model_copy(update={'lookup': None}), evidence)) is None
    typed = item.model_copy(update={'requirement': '서술 형식과 조회 인터페이스는 독립적입니다.',
        'lookup': RecipeLookup(operation='quantity', menu_id='vanilla_latte', temperature='ICED',
                               ingredients=['바닐라 시럽'], option=None, servings=1)})
    assert asyncio.run(shortcut.resolve_item(ctx, typed, evidence)) is not None
    assert asyncio.run(shortcut.resolve_item(ctx, typed.model_copy(update={
        'lookup': typed.lookup.model_copy(update={'servings': 3})}), evidence)) is None
    assert asyncio.run(shortcut.resolve_item(ctx, item.model_copy(update={'evidence_refs': []}), evidence)) is None
    def no_text_round_trip(*args, **kwargs):
        raise AssertionError('Typed lookup must not parse a question or build a public answer')
    monkeypatch.setattr('app.generation.recipe.parse_request', no_text_round_trip)
    monkeypatch.setattr(shortcut, 'try_answer', no_text_round_trip)
    assert asyncio.run(shortcut.resolve_item(ctx, typed, evidence)) is not None


def test_other_temperature_or_version_values_cannot_validate_a_recipe(recipes):
    shortcut, jobs = recipes
    original = deepcopy(shortcut.data)
    for ingredient, wrong_amount in [('우유', '170'), ('바닐라 시럽', '15')]:
        shortcut.data = deepcopy(original)
        record = next(r for r in shortcut.data['recipe_versions']
                      if r['menu_id'] == 'vanilla_latte' and r['temperature'] == 'HOT'
                      and r['document_versions'] == ['1.0'])
        next(i for i in record['ingredients'] if i['name'] == ingredient)['amount'] = wrong_amount
        response = asyncio.run(shortcut.try_answer(context(jobs, f'핫 바닐라 라떼 {ingredient} 몇 mL 넣어?')))
        assert response is None
