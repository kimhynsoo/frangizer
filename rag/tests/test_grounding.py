"""Source and scope boundaries after drafting; never a pre-writing approval gate."""
from dataclasses import replace

import pytest

from app.generation.review import Quantity
from tests.support import (
    FixtureRetriever, source, block, ref, Driver,
    proposal, run_draft_flow as run,
)


def test_missing_quantity_binding_is_filled_only_from_a_unique_cited_literal_pair():
    from app.generation.evidence_policy import bind_literal_quantities, quantity_failures
    from app.generation.review import evidence_units
    e = source('납부기한은 계약체결일부터 3일 이내입니다.')
    b = block(text='교육비는 계약체결일부터 3일 이내 납부합니다.')
    result = bind_literal_quantities([b], {e.metadata.chunk_id:e})[0]
    assert result.text == b.text and result.evidence_refs == b.evidence_refs
    assert [(q.value,q.unit,q.evidence_ref) for q in result.quantities] == [('3','일',ref())]
    assert not quantity_failures(result, evidence_units({e.metadata.chunk_id:e}))
    assert not b.quantities


@pytest.mark.parametrize('case', ['wrong_value', 'wrong_unit', 'ambiguous', 'not_cited'])
def test_quantity_binding_never_guesses_a_value_unit_or_source(case):
    from app.generation.evidence_policy import bind_literal_quantities
    e = source('납부기한 3일 이내', 'first')
    other = source('다른 조건의 납부기한 3일 이내', 'second')
    text = '납부기한은 4일입니다.' if case=='wrong_value' else '납부기한은 3개월입니다.' if case=='wrong_unit' else '납부기한은 3일입니다.'
    refs = [ref('first'),ref('second')] if case=='ambiguous' else [ref('second')] if case=='not_cited' else [ref('first')]
    if case=='not_cited':other=source('다른 기준입니다.', 'second')
    b = block(text=text, refs=refs)
    result = bind_literal_quantities([b], {'first':e,'second':other})[0]
    assert not result.quantities


def test_wrong_quantity_reference_is_rebound_without_changing_the_fact_or_citations():
    from app.generation.evidence_policy import bind_literal_quantities
    e, other = source('부담 비율 100분의 40', 'first'), source('적용 대상에 관한 설명', 'second')
    b = block(text='부담 비율은 40%입니다.', refs=[ref('first'),ref('second')],
        quantities=[Quantity(label='부담 비율',value='40',unit='%',evidence_ref=ref('second'))])
    result = bind_literal_quantities([b], {'first':e,'second':other})[0]
    assert result.text == b.text and result.evidence_refs == b.evidence_refs
    assert result.quantities[0].evidence_ref == ref('first')
    assert result.quantities[0].value == '40' and result.quantities[0].unit == '%'


def test_quantity_binding_does_not_hide_a_wrong_explicit_row_assignment():
    from app.generation.evidence_policy import bind_literal_quantities, quantity_failures
    from app.generation.review import evidence_units
    e = source('교육비: 3,300천원\n보증금: 1,500천원')
    b = block(text='교육비는 1,500천원입니다.', quantities=[
        Quantity(label='교육비',value='1,500',unit='천원',evidence_ref=ref())])
    result = bind_literal_quantities([b], {e.metadata.chunk_id:e})[0]
    assert 'QUANTITY_LABEL_VALUE_MISMATCH' in quantity_failures(result,evidence_units({e.metadata.chunk_id:e}))


def test_missing_quantity_metadata_cannot_borrow_another_rows_amount():
    from app.generation.evidence_policy import bind_literal_quantities
    e = source('교육비: 3,300천원\n보증금: 1,500천원')
    result = bind_literal_quantities([block(text='교육비는 1,500천원입니다.')],{e.metadata.chunk_id:e})[0]
    assert not result.quantities


@pytest.mark.parametrize('damage', ['unknown_chunk', 'unknown_unit', 'blocked', 'truncated'])
def test_unusable_source_cannot_become_a_public_fact(damage):
    record = source('문서에 명시된 기준입니다.')
    selected = ref('invented') if damage == 'unknown_chunk' else ref(unit='unit-999') if damage == 'unknown_unit' else ref()
    if damage == 'blocked':
        record = replace(record, quality_flags=('table_needs_review',))
    elif damage == 'truncated':
        record = replace(record, truncated=True)
    p = proposal().model_copy(update={'blocks': [block(item_id='rule', refs=[selected])]})
    driver = Driver([p])
    result = run(driver, FixtureRetriever([record]))
    assert result.result.status == 'needs_review' and not result.result.sources
    assert len(driver.writes) <= 2
    if damage in ('blocked', 'truncated'):
        assert driver.writes[0]['evidence'] == []


def test_semantically_wrong_source_discards_whole_item_but_preserves_independent_fact():
    extra = source('두 번째 항목의 확인된 기준입니다.', 'second')
    p = proposal()
    second = p.items[0].model_copy(update={'item_id': 'second'})
    p = p.model_copy(update={'items': [*p.items, second], 'blocks': [
        block(item_id='rule', text='잘못 연결한 첫 항목입니다.', refs=[ref('second')]),
        block(1, item_id='rule', text='함께 삭제해야 하는 설명입니다.'),
        block(2, item_id='second', text=extra.text, refs=[ref('second')])]})
    driver = Driver([p])
    # Citation membership cannot prove meaning. The semantic reviewer rejects
    # the first item's unrelated source, while the independent item is retained.
    driver.review = lambda v, payload, n: v.model_copy(update={
        'supported': False, 'unsupported_claims': ['첫 항목은 인용 원문으로 뒷받침되지 않음'],
        'claims': [c.model_copy(update={'supported': c.claim_id in {b['id'] for b in payload['claims_to_check'] if b['item_id'] == 'second'}}) for c in v.claims],
        'item_checks': [i.model_copy(update={'complete': i.item_id == 'second'}) for i in v.item_checks],
        'recovery': 'none'})
    result = run(driver, FixtureRetriever([source('첫 항목 원문'), extra]))
    assert result.result.status == 'needs_review'
    assert '잘못 연결' not in result.result.answer and '함께 삭제' not in result.result.answer
    assert extra.text in result.result.answer
    assert [s.chunk_id for s in result.result.sources] == ['second']


@pytest.mark.parametrize('wrong_value', ['1,500', '2026'])
def test_other_rows_or_metadata_numbers_do_not_prove_the_requested_amount(wrong_value):
    record = source('단위: 천원\n- 교육비: 금액 3,300\n- 보증금: 금액 1,500')
    claim = block(item_id='rule', text=f'교육비는 {wrong_value}천원입니다.', quantities=[
        Quantity(label='교육비', value=wrong_value, unit='천원', evidence_ref=ref())])
    p = proposal().model_copy(update={'blocks': [claim]})
    result = run(Driver([p]), FixtureRetriever([record]))
    assert result.result.status == 'needs_review' and not result.result.sources
    assert wrong_value not in result.result.answer


def test_missing_document_is_not_transformed_into_a_user_context_question():
    p = proposal(disposition='missing_evidence').model_copy(update={'follow_up_questions': ['어떤 매장인가요?']})
    result = run(Driver([p]))
    assert result.result.status == 'needs_review' and not result.result.follow_up_questions
    assert not result.result.sources


def test_verified_user_condition_question_survives_without_a_factual_answer():
    p = proposal(disposition='missing_user_context').model_copy(update={'follow_up_questions': ['실제 계약 유형은 무엇인가요?']})
    result = run(Driver([p]))
    assert result.result.follow_up_questions == ('실제 계약 유형은 무엇인가요?',)
    assert not result.result.sources


def test_declaring_document_scope_does_not_bypass_semantic_condition_review():
    driver = Driver()
    def review(v, payload, n):
        return v.model_copy(update={'supported': False, 'unsupported_claims': ['대상 확대'],
            'claims': [c.model_copy(update={'conditions_preserved': False, 'scope_relation': 'broader'}) for c in v.claims],
            'recovery': 'rewrite'})
    driver.review = review
    result = run(driver)
    assert result.result.status == 'needs_review' and not result.result.sources
    assert '비용 일부를 분담' not in result.result.answer


def test_reviewer_sees_uncited_context_but_only_actual_claim_sources_are_published():
    other = source('추가 대상과 예외를 설명하는 원문입니다.', 'context')
    driver = Driver()
    def review(v, payload, n):
        assert {e['chunk_id'] for e in payload['evidence']} == {'fixture-chunk-1', 'context'}
        return v
    driver.review = review
    result = run(driver, FixtureRetriever([source('직접 인용 원문'), other]))
    assert result.result.status == 'answered'
    assert [s.chunk_id for s in result.result.sources] == ['fixture-chunk-1']


@pytest.mark.parametrize('source_text,unit', [('비용 부담 비율: 100분의 20', '%'),
                                           ('비용 부담 비율: 100분의 20', '100분의'),
                                           ('비용 부담 비율: 20%', '%')])
def test_equivalent_percentage_spelling_preserves_literal_value_and_source(source_text, unit):
    from app.generation.evidence_policy import quantity_failures
    from app.generation.review import evidence_units
    record = source(source_text)
    claim = block(text='비용 부담 비율은 20%입니다.', quantities=[
        Quantity(label='비용 부담 비율', value='20', unit=unit, evidence_ref=ref())])
    assert quantity_failures(claim, evidence_units({record.metadata.chunk_id: record})) == []
    assert record.text == source_text


@pytest.mark.parametrize('source_text,value,unit', [('비용 항목: 간판교체비용', '20', '%'),
    ('비용 부담 비율: 100분의 40', '20', '%'),
    ('비용 부담 비율: 100분의 20', '100', '원'),
    ('비용 부담 비율: 100분의 20', '20', '천원')])
def test_percentage_spelling_cannot_rebind_source_change_amount_or_supply_currency(source_text,value,unit):
    from app.generation.evidence_policy import quantity_failures
    from app.generation.review import evidence_units
    record = source(source_text)
    claim = block(text=f'비용은 {value}{unit}입니다.', quantities=[
        Quantity(label='비용', value=value, unit=unit, evidence_ref=ref())])
    assert quantity_failures(claim, evidence_units({record.metadata.chunk_id: record}))
