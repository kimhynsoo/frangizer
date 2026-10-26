"""Focused failures: coverage, independent facts, quantities and visible source spans."""
import asyncio
import json
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from app.generation.agent import CounselAgent
from app.generation.answer import deduplicate_verified
from app.generation.model import CounselDraft
from app.generation.review import Condition, Quantity, ItemCheck
from app.retrieval.base import EvidenceBatch
from app.schemas import InternalQuery
from tests.support import FixtureRetriever, assessment, fixture_evidence, query_payload, verified_fixture, source, ref, item, lookup_item, block, Blocks, run_coverage as run


def test_partial_coverage_retains_supported_fact_and_does_not_ask_for_missing_document():
    a = assessment(items=[item(), item('item-2', state='missing_evidence')])
    model = Blocks([block(text='확인된 첫 번째 기준입니다.')], assessments=[a])
    result = run(model)
    assert result.status == 'needs_review' and len(result.sources) == 1
    assert '확인된 첫 번째 기준' in result.answer and 'item-2' in result.answer
    assert result.follow_up_questions == ()


@pytest.mark.parametrize('reject_lookup', [False, True])
def test_item_lookup_skips_rewriting_but_never_skips_final_review(reject_lookup):
    class Review(Blocks):
        async def verify(self, messages):
            self.verify_calls += 1
            v = verified_fixture(messages)
            if reject_lookup:
                v = v.model_copy(update={'supported': False, 'unsupported_claims': ['적용 조건 불일치'],
                    'claims': [c.model_copy(update={'supported': False}) for c in v.claims],
                    'item_checks': [i.model_copy(update={'complete': False}) for i in v.item_checks]})
            return v, AIMessage(content='')

    async def lookup(ctx, assessed_item, evidence):
        assert assessed_item.item_id == 'item-1'  # Missing items never reach the resolver.
        return CounselDraft(blocks=[block(text='원문으로 확정한 조회값입니다.')], category='franchise')

    model = Review([], assessments=[assessment(items=[lookup_item(), item('item-2', state='missing_evidence')])])
    ctx = InternalQuery.model_validate(query_payload())
    result = asyncio.run(CounselAgent(model, FixtureRetriever(), max_model_calls=3, item_lookup=lookup).run(ctx))
    assert model.verify_calls >= 1
    assert ('원문으로 확정한 조회값' in result.result.answer) is not reject_lookup
    assert result.result.status == 'needs_review'


def test_item_lookup_replaces_only_its_own_proposed_item():
    class Writer(Blocks):
        async def finalize(self, messages):
            payload = json.loads(messages[-1].content)
            assert {i['item_id'] for i in payload['assessment']['items']} == {'item-1', 'item-2'}
            return await super().finalize(messages)

    async def lookup(ctx, assessed_item, evidence):
        return CounselDraft(blocks=[block(text='저장값으로 확인한 첫 항목입니다.')], category='franchise') if assessed_item.item_id == 'item-1' else None

    model = Writer([block(item_id='item-2', text='근거로 검증할 두 번째 항목입니다.')],
                   assessments=[assessment(items=[lookup_item(), item('item-2')])])
    ctx = InternalQuery.model_validate(query_payload())
    result = asyncio.run(CounselAgent(model, FixtureRetriever(), item_lookup=lookup).run(ctx))
    assert model.final_calls == model.verify_calls == 1
    assert '저장값으로 확인한 첫 항목' in result.result.answer
    assert '검증할 두 번째 항목' in result.result.answer
    assert result.result.status == 'answered'


def test_user_context_gap_keeps_grounded_fact_and_short_question():
    a = assessment(items=[item(), item('item-2', state='missing_user_context')], questions=['어느 업종으로 신고한 매장인가요?'])
    result = run(Blocks([block()], assessments=[a]))
    assert result.sources and result.follow_up_questions == ('어느 업종으로 신고한 매장인가요?',)
    assert '사용자 조건' in result.answer


def test_document_rule_can_be_answered_without_claiming_case_application():
    rule = item().model_copy(update={'answer_scope': 'document_rule'})
    case = item('case', state='missing_user_context')
    a = assessment(items=[rule, case], questions=['실제 해당 조건에 해당하나요?'])
    result = run(Blocks([block(text='문서에 명시된 조건에서 비용 일부를 분담합니다.')], assessments=[a]))
    assert result.status == 'needs_review' and result.sources
    assert '문서 기준:' in result.answer and '비용 일부' in result.answer
    assert 'case' in result.answer and result.follow_up_questions
    # Merely calling an unresolved claim a document rule never grants permission to answer.
    assert case.model_copy(update={'answer_scope': 'document_rule'}).state == 'missing_user_context'


def test_duplicate_verified_statement_merges_citations_but_not_scope_or_units():
    from datetime import timedelta
    first, second = source('이행보증금 1,500천원', 'first'), source('이행보증금 1,500천원', 'second')
    statements = [block(text='이행보증금: 1,500 (단위: 천원)', refs=[ref('first')]),
                  block(1, text='이행보증금: 1,500 (단위:천원)', refs=[ref('second')])]
    evidence = {'first': first, 'second': second}
    combined = deduplicate_verified(statements, evidence)
    assert len(combined) == 1 and len(combined[0].evidence_refs) == 2
    different_period = replace(second, metadata=second.metadata.model_copy(update={
        'effective_from': second.metadata.effective_from + timedelta(days=1)}))
    assert len(deduplicate_verified(statements, {**evidence, 'second': different_period})) == 2
    assert len(deduplicate_verified([statements[0], statements[1].model_copy(update={'item_id': 'another'})], evidence)) == 2
    changed_unit = statements[1].model_copy(update={'text': '이행보증금: 1,500 (단위: 원)'})
    assert len(deduplicate_verified([statements[0], changed_unit], evidence)) == 2


def test_failed_item_loses_all_its_explanation_but_preserves_other_item():
    class Review(Blocks):
        async def verify(self, messages):
            v = verified_fixture(messages)
            claims = [c.model_copy(update={'supported': c.claim_id != 1}) for c in v.claims]
            return v.model_copy(update={'supported': False, 'unsupported_claims': ['두 번째 항목 조건 누락'],
                'claims': claims, 'recovery': 'rewrite'}), AIMessage(content='')
    a = assessment(items=[item(), item('item-2')])
    blocks = [block(text='독립적으로 확인된 기준입니다.'),
              block(1, item_id='item-2', text='잘못 확대된 조건입니다.'),
              block(2, item_id='item-2', text='따라서 두 기준은 같습니다.')]
    result = run(Review(blocks, assessments=[a]))
    assert result.status == 'needs_review' and result.sources
    assert '독립적으로 확인된' in result.answer
    assert '잘못 확대' not in result.answer and '두 기준은 같습니다' not in result.answer
    assert 'item-2' in result.answer


@pytest.mark.parametrize('second_passes', [True, False])
def test_rewrite_preserves_omitted_verified_item_and_merges_only_newly_verified_items(second_passes):
    class Rewrite(Blocks):
        async def finalize(self, messages):
            if self.final_calls:
                payload = json.loads(messages[-1].content)
                assert [i['item_id'] for i in payload['assessment']['items']] == ['item-2']
                # Reusing ID zero must not overwrite the first generation's item.
                self.blocks = [block(0, item_id='item-2', text='새로 작성한 두 번째 기준입니다.')]
            return await super().finalize(messages)

        async def verify(self, messages):
            self.verify_calls += 1
            v = verified_fixture(messages)
            # Reused facts remain visible to final review. Reject the second item,
            # not whichever draft-local IDs happened to be present before reuse.
            bad_ids = {c['id'] for c in json.loads(messages[-1].content)['claims_to_check'] if c['item_id'] == 'item-2'}
            if self.verify_calls == 2 and second_passes:
                return v, AIMessage(content='')
            return v.model_copy(update={'supported': False, 'unsupported_claims': ['두 번째 항목 오류'],
                'claims': [c.model_copy(update={'supported': c.claim_id not in bad_ids}) for c in v.claims],
                'item_checks': [i.model_copy(update={'complete': i.item_id == 'item-1'}) for i in v.item_checks],
                'recovery': 'rewrite'}), AIMessage(content='')

    model = Rewrite([block(text='검수 통과한 첫 번째 기준입니다.'), block(1, item_id='item-2')],
                    assessments=[assessment(items=[item(), item('item-2')])])
    result = run(model, max_calls=5)
    assert model.final_calls == model.verify_calls == 2
    assert '검수 통과한 첫 번째 기준' in result.answer
    assert ('새로 작성한 두 번째 기준' in result.answer) == second_passes
    assert result.status == ('answered' if second_passes else 'needs_review')


def test_new_review_rejection_of_same_item_cannot_resurrect_old_version():
    class Rewrite(Blocks):
        async def verify(self, messages):
            self.verify_calls += 1
            v = verified_fixture(messages)
            return v.model_copy(update={'supported': False, 'unsupported_claims': ['근거 불일치'],
                'claims': [c.model_copy(update={'supported': self.verify_calls == 1 and c.claim_id == 0}) for c in v.claims],
                'recovery': 'rewrite'}), AIMessage(content='')

    model = Rewrite([block(text='이전 첫 번째 답입니다.'), block(1, item_id='item-2')],
                    assessments=[assessment(items=[item(), item('item-2')])])
    result = run(model, max_calls=5)
    assert model.verify_calls == 2 and not result.sources
    assert '이전 첫 번째 답' not in result.answer


def test_research_invalidates_old_verified_snapshot_before_reassessment():
    class Research(Blocks):
        async def verify(self, messages):
            self.verify_calls += 1
            v = verified_fixture(messages)
            return v.model_copy(update={'supported': False, 'unsupported_claims': ['두 번째 근거 부족'],
                'claims': [c.model_copy(update={'supported': c.claim_id == 0}) for c in v.claims],
                'recovery': 'research', 'missing_item_ids': ['item-2'], 'query': '추가 적용 조건'}), AIMessage(content='')

    first = assessment(items=[item(), item('item-2')])
    revised = assessment('insufficient', items=[item(state='missing_evidence'), item('item-2', state='conflict')])
    model = Research([block(text='재평가 전 첫 번째 기준입니다.'), block(1, item_id='item-2')],
                     assessments=[first, revised])
    result = run(model, max_calls=6)
    assert model.assess_calls == 2 and model.verify_calls == 2
    assert not result.sources and '재평가 전 첫 번째 기준' not in result.answer


def test_numeric_row_swap_is_rejected_even_if_judge_marks_everything_supported():
    text = '단위: 천원\n- 교육비: 금액 3,300\n- 보증금: 금액 1,500'
    b = block(text='교육비는 1,500천원이고 보증금은 3,300천원입니다.', quantities=[
        Quantity(label='교육비', value='1,500', unit='천원', evidence_ref=ref()),
        Quantity(label='보증금', value='3,300', unit='천원', evidence_ref=ref())])
    result = run(Blocks([b], assessments=[assessment()]), FixtureRetriever([source(text)]))
    assert result.status == 'needs_review' and not result.sources
    assert '1,500' not in result.answer and '3,300' not in result.answer


@pytest.mark.parametrize('row', [
    '- 매월: 로열티 275 · 비고 익월 5일 내 납부',
    '[행 1] 매월: 로열티: 275 | 비고: 익월 5일 내 납부',
    '로열티 금액: 275 · 지급기한 익월 5일 내 납부',
])
def test_table_title_is_not_a_numeric_assignment_and_column_value_is_grounded(row):
    text = '<별첨 1-3> 로열티 금액 관련 기준\n(단위: 천원, 부가세 포함)\n' + row
    b = block(text='로열티는 매월 275천원입니다.', quantities=[
        Quantity(label='로열티', value='275', unit='천원', evidence_ref=ref())])
    result = run(Blocks([b], assessments=[assessment()]), FixtureRetriever([source(text)]))
    assert result.status == 'answered' and '275천원' in result.answer


@pytest.mark.parametrize(('text', 'label', 'value'), [
    ('단위: 천원\n- 매월: 로열티 275 · 비고 익월 5일 내 납부', '로열티', '5'),
    ('단위: 천원\n- 교육비: 금액 3,300 · 지급기한 계약체결일로부터 3일 이내', '교육비', '3'),
])
def test_other_cell_number_cannot_be_assigned_to_an_amount_label(text, label, value):
    b = block(text=f'{label}는 {value}일입니다.', quantities=[
        Quantity(label=label, value=value, unit='일', evidence_ref=ref())])
    model = Blocks([b], assessments=[assessment()])
    result = run(model, FixtureRetriever([source(text)]))
    assert result.status == 'needs_review' and not result.sources
    assert model.verify_calls == 1


def test_page_and_version_numbers_are_not_factual_quantity_evidence():
    b = block(text='교육비는 2026천원입니다.', quantities=[Quantity(label='교육비', value='2026', unit='천원', evidence_ref=ref())])
    result = run(Blocks([b], assessments=[assessment()]), FixtureRetriever([source('교육비: 3,300천원')]))
    assert result.status == 'needs_review' and '2026' not in result.answer


def test_valid_paraphrase_uses_its_selected_paragraph_and_preserves_other_condition_source():
    text = '제9조(점검) ① 본사가 정한 담당자는 당일 점검표를 확인하여야 한다.\n② 휴점일은 제외한다.'
    a = assessment(items=[item(refs=[ref(unit='unit-1')], conditions=[
        Condition(text='휴점일은 제외한다.', evidence_refs=[ref(unit='unit-2')])])])
    b = block(text='본사가 정한 담당자는 휴점일을 제외하고 당일 점검표를 확인해야 합니다.', refs=[ref(unit='unit-1'), ref(unit='unit-2')])
    result = run(Blocks([b], assessments=[a]), FixtureRetriever([source(text)]))
    assert result.status == 'answered'
    assert '① 본사가 정한' in result.sources[0].excerpt and '② 휴점일' in result.sources[0].excerpt
    assert '제9조(점검)' not in result.sources[0].excerpt


def test_incomplete_item_cannot_be_made_answered_by_global_positive_flag():
    class Review(Blocks):
        async def verify(self, messages):
            v = verified_fixture(messages)
            return v.model_copy(update={'item_checks': [ItemCheck(item_id='item-1', complete=False)]}), AIMessage(content='')
    result = run(Review([block()], assessments=[assessment()]))
    assert result.status == 'needs_review' and not result.sources


def test_invalid_sentence_reference_is_repaired_or_safely_discarded():
    model = Blocks([block(refs=[ref(unit='unit-999')])], assessments=[assessment()])
    result = run(model)
    assert result.status == 'needs_review' and not result.sources
    assert model.final_calls <= 2


def test_verifier_can_trigger_one_targeted_retrieval_instead_of_rewriting_same_evidence():
    extra = source('추가 확인된 두 번째 기준입니다.', 'source-2')
    first = assessment(items=[item(), item('item-2')])
    second = assessment(items=[item(), item('item-2', refs=[ref('source-2')])])

    class Retrieval(FixtureRetriever):
        async def search(self, query, context):
            self.searches.append(query)
            return EvidenceBatch((fixture_evidence(),) if len(self.searches)==1 else (extra,))

    class Review(Blocks):
        async def finalize(self, messages):
            if self.final_calls:
                self.blocks = [block(), block(1, item_id='item-2', refs=[ref('source-2')], text='추가 확인된 두 번째 기준입니다.')]
            return await super().finalize(messages)

        async def verify(self, messages):
            self.verify_calls += 1
            v = verified_fixture(messages)
            if self.verify_calls == 1:
                v = v.model_copy(update={'supported': False, 'unsupported_claims': ['두 번째 항목 근거 부족'],
                    'claims': [c.model_copy(update={'supported': c.claim_id != 1}) for c in v.claims],
                    'recovery': 'research', 'missing_item_ids': ['item-2'], 'query': '두 번째 항목 실질 기준'})
            return v, AIMessage(content='')

    retriever = Retrieval()
    model = Review([block(), block(1, item_id='item-2')], assessments=[first, second])
    result = run(model, retriever, max_calls=6)
    assert result.status == 'answered' and len(result.sources)==2
    assert retriever.searches[-1] == '두 번째 항목 실질 기준' and len(retriever.searches)==2
    assert model.assess_calls == model.final_calls == model.verify_calls == 2


def test_partial_numeric_fact_also_runs_row_binding_guard():
    a = assessment(items=[item(), item('item-2', state='missing_evidence')])
    b = block(text='교육비는 1,500천원입니다.', quantities=[Quantity(label='교육비', value='1,500', unit='천원', evidence_ref=ref())])
    result = run(Blocks([b], assessments=[a]), FixtureRetriever([source('단위: 천원\n교육비: 3,300\n보증금: 1,500')]))
    assert '1,500' not in result.answer and not result.sources


def test_time_label_paraphrase_is_not_rejected_as_a_literal_copy_requirement():
    b = block(text='계약체결일로부터 3일 이내 납부해야 합니다.', quantities=[
        Quantity(label='납부기한', value='3', unit='일', evidence_ref=ref())])
    result = run(Blocks([b], assessments=[assessment()]), FixtureRetriever([source('지급기한: 계약체결일로부터 3일 이내')]))
    assert result.status == 'answered' and '3일' in result.answer


def test_numeric_unit_must_not_treat_won_as_thousand_won():
    b = block(text='교육비는 3,300원입니다.', quantities=[Quantity(label='교육비', value='3,300', unit='원', evidence_ref=ref())])
    result = run(Blocks([b], assessments=[assessment()]), FixtureRetriever([source('단위: 천원\n교육비: 3,300')]))
    assert result.status == 'needs_review' and '3,300원' not in result.answer


def test_article_reference_is_not_confused_with_a_monetary_amount():
    s = source('① 특정 담당자는 점검표를 확인하여야 한다.')
    s = replace(s, metadata=s.metadata.model_copy(update={'article': '제41조'}))
    b = block(text='제41조는 특정 담당자의 점검표 확인 의무를 규정합니다.')
    assert run(Blocks([b], assessments=[assessment()]), FixtureRetriever([s])).status == 'answered'
    bad = block(text='제41조에 따라 점검 비용은 41원입니다.', quantities=[Quantity(label='점검 비용', value='41', unit='원', evidence_ref=ref())])
    assert not run(Blocks([bad], assessments=[assessment()]), FixtureRetriever([s])).sources


def test_excerpt_length_limit_includes_explicit_elision_notice():
    text = '머리말 ' + '가' * 2000 + '\n① 실제 사용한 조건 ' + '나' * 2400
    b = block(text='실제 사용한 조건을 확인합니다.', refs=[ref(unit='unit-1')])
    a = assessment(items=[item(refs=[ref(unit='unit-1')])])
    result = run(Blocks([b], assessments=[a]), FixtureRetriever([source(text)]))
    excerpt = result.sources[0].excerpt
    assert len(excerpt) <= 1500 and '일부' in excerpt and '실제 사용한 조건' in excerpt
    assert '머리말' not in excerpt


def test_expanded_scope_is_rejected_by_independent_semantic_review():
    class ScopeReview(Blocks):
        async def verify(self, messages):
            v = verified_fixture(messages)
            return v.model_copy(update={'supported': False, 'unsupported_claims': ['원문보다 대상 확대'],
                'claims': [c.model_copy(update={'applicable': False, 'scope_relation': 'broader'}) for c in v.claims]}), AIMessage(content='')
    original = source('① 특정 자격을 갖춘 담당자는 점검을 받아야 한다.')
    bad = block(text='모든 담당자는 점검을 받아야 합니다.')
    assert not run(ScopeReview([bad], assessments=[assessment()]), FixtureRetriever([original])).sources
    good = block(text='특정 자격을 갖춘 담당자는 점검을 받아야 합니다.')
    assert run(Blocks([good], assessments=[assessment()]), FixtureRetriever([original])).status == 'answered'


def test_grounded_paraphrase_does_not_require_literal_restriction_words():
    original = source('① 본사가 정한 담당자는 당일 점검표를 확인하여야 한다.')
    paraphrase = block(text='당일 점검표는 가맹본부가 지정한 담당자가 확인해야 합니다.')
    assert run(Blocks([paraphrase], assessments=[assessment()]), FixtureRetriever([original])).status == 'answered'


def test_explicit_article_paragraph_citation_is_not_a_quantity():
    s = source('② 영업을 하려는 자는 미리 교육을 받아야 한다.')
    s = replace(s, metadata=s.metadata.model_copy(update={'article': '제41조'}))
    b = block(text='제41조제2항은 영업을 하려는 자의 사전 교육을 규정합니다.')
    assert run(Blocks([b], assessments=[assessment()]), FixtureRetriever([s])).status == 'answered'


@pytest.mark.parametrize('text', [
    '제71조제1항과 제2항은 서로 다른 대상을 정합니다.',
    '제71조 제1항 및 제2항을 확인합니다.',
    '제71조에 따라 제2항의 대상을 설명합니다.',
])
def test_short_paragraph_reference_requires_an_explicit_article_and_its_cited_unit(text):
    from app.generation.evidence_policy import quantity_failures
    from app.generation.review import evidence_units
    s = source('제71조(합성 기준) ① 대상 A. ② 대상 B.')
    s = replace(s, metadata=s.metadata.model_copy(update={'article': '제71조'}))
    b = block(text=text, refs=[ref(unit='unit-1'), ref(unit='unit-2')])
    assert quantity_failures(b, evidence_units({s.metadata.chunk_id: s})) == []


@pytest.mark.parametrize('text', [
    '제71조제1항과 제3항을 확인합니다.',
    '제72조에 따라 제2항을 확인합니다.',
    '제2항을 확인합니다.',
    '제71조제1항과 제2항에 따라 비용은 2원입니다.',
])
def test_short_reference_does_not_authorize_unknown_paragraphs_or_factual_amounts(text):
    from app.generation.evidence_policy import quantity_failures
    from app.generation.review import evidence_units
    s = source('제71조(합성 기준) ① 대상 A. ② 대상 B.')
    s = replace(s, metadata=s.metadata.model_copy(update={'article': '제71조'}))
    b = block(text=text, refs=[ref(unit='unit-1'), ref(unit='unit-2')])
    assert 'MISSING_QUANTITY_BINDING' in quantity_failures(b, evidence_units({s.metadata.chunk_id: s}))


def test_step_without_its_own_item_answer_is_not_promoted_to_main_answer():
    class Review(Blocks):
        async def verify(self, messages):
            v = verified_fixture(messages)
            return v.model_copy(update={'supported': False, 'unsupported_claims': ['첫 번째 항목 오류'],
                'claims': [c.model_copy(update={'supported': c.claim_id != 0}) for c in v.claims]}), AIMessage(content='')
    good_step = block(1, item_id='item-2', text='독립적으로 확인된 두 번째 기준입니다.').model_copy(update={'placement': 'step'})
    result = run(Review([block(), good_step], assessments=[assessment(items=[item(), item('item-2')])]))
    assert result.status == 'needs_review' and not result.sources
    assert '독립적으로 확인된 두 번째 기준' not in result.answer


def test_failed_step_removes_its_item_answer_and_sources_as_a_group():
    class Review(Blocks):
        async def verify(self, messages):
            v = verified_fixture(messages)
            return v.model_copy(update={
                'supported': False, 'unsupported_claims': ['항목의 처리 순서가 근거와 다름'],
                'claims': [c.model_copy(update={'supported': c.claim_id != 1}) for c in v.claims],
            }), AIMessage(content='')

    extra = source('독립적으로 확인된 다른 기준입니다.', 'source-2')
    a = assessment(items=[item(), item('item-2', refs=[ref('source-2')])])
    blocks = [block(text='잘못된 단계와 연결된 첫 번째 기준입니다.'),
              block(1, text='잘못된 처리 순서입니다.').model_copy(update={'placement': 'step'}),
              block(2, item_id='item-2', refs=[ref('source-2')], text=extra.text)]
    result = run(Review(blocks, assessments=[a]), FixtureRetriever([fixture_evidence(), extra]))
    assert result.status == 'needs_review' and result.steps == ()
    assert '잘못된 단계와 연결된' not in result.answer
    assert extra.text in result.answer
    assert [s.chunk_id for s in result.sources] == ['source-2']


def test_missing_document_is_not_reclassified_as_a_user_context_question():
    class Review(Blocks):
        async def verify(self, messages):
            self.verify_calls += 1
            v = verified_fixture(messages)
            return v.model_copy(update={
                'supported': False, 'unsupported_claims': ['첫 항목의 설명 오류'],
                'claims': [c.model_copy(update={'supported': False}) for c in v.claims],
                'recovery': 'clarify', 'missing_item_ids': ['item-2'],
            }), AIMessage(content='')

    a = assessment(items=[item(), item('item-2', state='missing_evidence')],
                   questions=['어떤 상황인지 알려 주세요.'])
    model = Review([block()], assessments=[a])
    result = run(model, max_calls=6)
    assert model.final_calls == model.verify_calls == 1
    assert result.follow_up_questions == () and not result.sources


def test_source_condition_in_item_answer_need_not_be_repeated_in_each_step():
    original = source('① 본사가 정한 담당자는 당일 점검표를 확인하여야 한다.')
    blocks = [block(text='본사가 정한 담당자는 당일 점검표를 확인해야 합니다.'),
              block(1, text='당일 점검표를 확인합니다.').model_copy(update={'placement': 'step'})]
    result = run(Blocks(blocks, assessments=[assessment()]), FixtureRetriever([original]))
    assert result.status == 'answered' and result.steps == ('당일 점검표를 확인합니다.',)


@pytest.mark.parametrize('same_table', [True, False])
def test_quantity_can_link_a_separate_unit_header_only_in_the_same_table(same_table):
    from app.generation.evidence_policy import quantity_failures
    from app.generation.review import evidence_units
    row = replace(source('교육비: 금액 3,300'), parent_section_key='p9.table1')
    header = replace(source('단위: 천원, 부가세 포함', 'header'),
                     parent_section_key='p9.table1' if same_table else 'p9.table2')
    quantity = Quantity(label='교육비', value='3,300', unit='천원', evidence_ref=ref(), context_refs=[ref('header')])
    claim = block(text='교육비는 3,300천원입니다.', refs=[ref(), ref('header')], quantities=[quantity])
    failures = quantity_failures(claim, evidence_units({'fixture-chunk-1': row, 'header': header}))
    assert bool(failures) != same_table
    if not same_table:
        assert 'QUANTITY_SOURCE_MISMATCH' in failures


@pytest.mark.parametrize('repaired', [True, False])
def test_cross_table_quantity_is_rewritten_before_any_semantic_review(repaired):
    primary = replace(source('단위: 천원\n교육비: 3,300'), parent_section_key='table-1')
    other = replace(source('단위: 천원\n교육비: 3,300', 'other'), parent_section_key='table-2')
    q = Quantity(label='교육비', value='3,300', unit='천원', evidence_ref=ref(), context_refs=[ref('other')])
    bad = block(text='교육비는 3,300천원입니다.', refs=[ref(), ref('other')], quantities=[q])

    class Writer(Blocks):
        async def finalize(self, messages):
            if self.final_calls:
                assert self.verify_calls == 0
                correction = json.loads(json.loads(messages[-1].content)['correction'])
                assert any(f['code'] == 'QUANTITY_SOURCE_MISMATCH' for f in correction['failures'])
                if repaired:
                    self.blocks = [bad.model_copy(update={'evidence_refs': [ref()],
                        'quantities': [q.model_copy(update={'context_refs': []})]})]
            return await super().finalize(messages)

    model = Writer([bad], assessments=[assessment(items=[item(refs=[ref(), ref('other')])])])
    result = run(model, FixtureRetriever([primary, other]), max_calls=4)
    assert model.final_calls == 2
    assert model.verify_calls == 1
    assert (result.status == 'answered') == repaired
    assert bool(result.sources) == repaired


def test_numeric_error_discards_its_whole_item_before_review_when_no_rewrite_budget():
    primary = source('단위: 천원\n교육비: 3,300')
    other = source('독립적으로 확인된 기준입니다.', 'other')
    bad = block(text='교육비는 3,300원입니다.', quantities=[
        Quantity(label='교육비', value='3,300', unit='원', evidence_ref=ref())])
    step = block(1, text='첫 번째 항목의 처리 순서입니다.').model_copy(update={'placement': 'step'})
    good = block(2, item_id='item-2', text='독립적으로 확인된 기준입니다.', refs=[ref('other')])

    class Review(Blocks):
        async def verify(self, messages):
            claims = json.loads(messages[-1].content)['claims_to_check']
            assert [c['item_id'] for c in claims] == ['item-2']
            return await super().verify(messages)

    model = Review([bad, step, good], assessments=[assessment(items=[item(), item('item-2', refs=[ref('other')])])])
    result = run(model, FixtureRetriever([primary, other]), max_calls=2)
    assert model.final_calls == model.verify_calls == 1
    assert '3,300원' not in result.answer and '첫 번째 항목의 처리 순서' not in result.answer
    assert '독립적으로 확인된 기준' in result.answer
