"""Draft → independent review → bounded correction; no approval model in between."""
import json

import pytest

from app.generation.model import ProposedAnswer
from app.generation.review import ItemCheck
from tests.support import FixtureRetriever, source, block, ref, proposal, Driver, run_draft_flow as run


def test_basic_counsel_has_only_draft_and_review():
    driver = Driver()
    result = run(driver)
    assert driver.calls == ['draft', 'verify']
    assert result.result.status == 'answered' and result.result.sources
    assert '문서 기준:' in result.result.answer
    assert 'assessment' not in driver.writes[0]


def test_item_sources_are_derived_from_its_sentences_without_a_second_model_selection():
    p = proposal()
    other = p.items[0].model_copy(update={'item_id': 'other'})
    p = p.model_copy(update={'items': [*p.items, other], 'blocks': [
        *p.blocks, block(1, item_id='rule', refs=[ref()]),
        block(2, item_id='other', refs=[ref('other-source')])]})
    assert p.item_sources(p.items[0]) == [ref()]
    assert p.item_sources(other) == [ref('other-source')]
    assert 'evidence_refs' not in p.items[0].model_dump()
    assert 'evidence_refs' not in ProposedAnswer.model_json_schema()['$defs']['DraftItem']['properties']


def test_citations_do_not_promote_a_missing_evidence_item_into_an_answer():
    p = proposal(disposition='missing_evidence').model_copy(update={
        'blocks': [block(item_id='rule', text='근거 없이 승인할 수 없는 설명입니다.')]})
    result = run(Driver([p]))
    assert result.result.status == 'needs_review'
    assert not result.result.sources and '승인할 수 없는 설명' not in result.result.answer


def test_review_can_find_evidence_that_writer_left_unanswered():
    driver = Driver([proposal(disposition='missing_evidence'), proposal()])
    def review(v, payload, attempt):
        assert payload['evidence']  # Not restricted to citations selected by the writer.
        return v.model_copy(update={'question_answered': False, 'recovery': 'rewrite',
            'missing_item_ids': ['rule']}) if attempt == 1 else v
    driver.review = review
    assert run(driver).result.status == 'answered'
    assert driver.calls == ['draft', 'verify', 'draft', 'verify']


def test_only_reviewer_requests_additional_retrieval():
    retriever = FixtureRetriever()
    driver = Driver([proposal(disposition='missing_evidence'), proposal()])
    driver.review = lambda v, p, n: v.model_copy(update={'recovery': 'research',
        'query': '누락된 참조 기준', 'missing_item_ids': ['rule']}) if n == 1 else v
    result = run(driver, retriever)
    assert result.result.status == 'answered'
    assert len(retriever.searches) == 2 and retriever.searches[1].startswith('누락된 참조 기준')
    assert driver.calls == ['draft', 'verify', 'draft', 'verify']


def test_unchanged_verified_item_is_not_regenerated_but_is_reviewed_again():
    initial = proposal()
    other = initial.items[0].model_copy(update={'item_id': 'case', 'requirement': '실제 적용 판단',
                                             'answer_scope': 'case_application'})
    initial = initial.model_copy(update={'items': [*initial.items, other], 'blocks': [
        *initial.blocks, block(1, item_id='case', text='수정해야 하는 적용 설명입니다.')]})
    fixed = ProposedAnswer(items=[other], blocks=[block(item_id='case', text='수정된 적용 설명입니다.')],
                           category='franchise', follow_up_questions=[])
    driver = Driver([initial, fixed])
    def review(v, payload, n):
        if n == 2:
            assert {c['item_id'] for c in payload['claims_to_check']} == {'rule', 'case'}
            return v
        return v.model_copy(update={'supported': False, 'unsupported_claims': ['case 오류'],
            'claims': [c.model_copy(update={'supported': c.claim_id != 1}) for c in v.claims],
            'item_checks': [i.model_copy(update={'complete': i.item_id == 'rule'}) for i in v.item_checks],
            'recovery': 'rewrite', 'missing_item_ids': ['case']})
    driver.review = review
    result = run(driver)
    assert driver.writes[1]['verified_item_ids'] == ['rule']
    assert [i['item_id'] for i in driver.writes[1]['requirements']] == ['case']
    assert result.result.status == 'answered' and '문서에 명시된' in result.result.answer


def test_final_rejection_cannot_publish_a_cited_but_wrong_statement():
    driver = Driver()
    driver.review = lambda v,p,n: v.model_copy(update={'supported': False, 'unsupported_claims': ['원문과 다름'],
        'claims': [c.model_copy(update={'supported': False}) for c in v.claims],
        'item_checks': [ItemCheck(item_id='rule', complete=False)], 'recovery': 'rewrite'})
    result = run(driver)
    assert result.result.status == 'needs_review' and not result.result.sources
    assert '비용 일부를 분담' not in result.result.answer
    assert len(driver.calls) == 4


def test_forged_citation_never_reaches_public_answer():
    fake = proposal().model_copy(update={'blocks': [block(item_id='rule', refs=[ref('not-retrieved')])]})
    driver = Driver([fake])
    result = run(driver)
    assert result.result.status == 'needs_review' and not result.result.sources
    assert len(driver.writes) <= 2


def test_writer_source_numbers_restore_exact_original_units():
    from app.generation.agent import evidence_payload
    from app.generation.review import prepare_draft_sources, resolve_draft_sources
    payload = {'evidence': [evidence_payload(source('原문', 'first')), evidence_payload(source('두 번째 원문', 'second'))]}
    encoded, schema, choices = prepare_draft_sources(payload, ProposedAnswer.model_json_schema())
    assert [r['source_units'][0]['source_id'] for r in encoded['evidence']] == [0, 1]
    assert schema['$defs']['Quote']['enum'] == [0, 1]
    raw = {'items': [{'lookup': {'source_refs': [0]}}], 'blocks': [{'evidence_refs': [1], 'quantities': [
        {'evidence_ref': 1, 'context_refs': [0]}]}]}
    restored = resolve_draft_sources(raw, choices)
    assert restored['items'][0]['lookup']['source_refs'] == [{'chunk_id': 'first', 'unit_id': 'unit-0'}]
    assert restored['blocks'][0]['evidence_refs'] == [{'chunk_id': 'second', 'unit_id': 'unit-0'}]
    assert restored['blocks'][0]['quantities'][0]['context_refs'][0]['chunk_id'] == 'first'
    assert raw['items'][0]['lookup']['source_refs'] == [0]
    for bad in (True, -1, 99, '0'):
        from app.errors import ServiceError
        with pytest.raises(ServiceError):
            resolve_draft_sources({'evidence_refs': [bad]}, choices)


def test_semantic_rewrite_receives_failed_sentence_and_specific_missing_conditions():
    driver = Driver([proposal('대상을 넓힌 설명입니다.'), proposal('한정된 대상에 대한 수정 설명입니다.')])
    def review(v, payload, n):
        if n != 1:
            return v
        return v.model_copy(update={'supported': False, 'unsupported_claims': ['대상 한정 누락'],
            'claims': [c.model_copy(update={'supported': False, 'conditions_preserved': False}) for c in v.claims],
            'item_checks': [i.model_copy(update={'complete': False}) for i in v.item_checks], 'recovery': 'rewrite'})
    driver.review = review
    assert run(driver, FixtureRetriever([source('원문에 명시된 대상 한정: 지정된 담당자만 해당합니다.')])).result.status == 'answered'
    targets = json.loads(driver.writes[1]['correction'])['repair_targets']
    assert targets[0]['previous_text'] == '대상을 넓힌 설명입니다.'
    assert 'CONDITION_LOSS' in targets[0]['codes']
    assert '대상 한정 누락' in json.loads(driver.writes[1]['correction'])['claims_to_correct']
    assert targets[0]['evidence_refs'] == [ref().model_dump()]


def test_numeric_rewrite_receives_failed_amount_and_its_original_source_binding():
    from app.generation.review import Quantity
    wrong = proposal().model_copy(update={'blocks': [block(item_id='rule', text='교육비는 1,500천원입니다.',
        quantities=[Quantity(label='교육비', value='1,500', unit='천원', evidence_ref=ref())])]})
    driver = Driver([wrong, proposal()])
    result = run(driver, FixtureRetriever([source('교육비: 금액 3,300천원')]))
    assert result.result.status == 'answered'
    target = json.loads(driver.writes[1]['correction'])['repair_targets'][0]
    assert target['quantities'][0]['value'] == '1,500'
    assert target['quantities'][0]['evidence_ref'] == ref().model_dump()
    assert 'QUANTITY_SOURCE_MISMATCH' in target['codes']
