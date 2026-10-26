import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.generation.review import prepare_review, resolve_review
from tests.support import (
    assessment, verified_fixture, Blocks, block, item,
    ref, run_coverage as run,
)


def payload():
    return {'claims_to_check': [
        {'id': 0, 'item_id': 'item-1', 'text': '지정된 담당자가 점검합니다.', 'evidence_refs': [ref().model_dump()]},
        {'id': 1, 'item_id': 'item-2', 'text': '다른 담당자가 확인합니다.', 'evidence_refs': [ref('second').model_dump()]},
    ], 'evidence': [{'usable_as_evidence': True, 'source_units': [{'text': '본사가 지정한 담당자는 점검한다.'}]}],
        'items': [item().to_payload(), item('item-2').to_payload()]}


def wire_result(original):
    result = verified_fixture([HumanMessage(content=json.dumps(original))]).model_dump()
    result['claims'] = [{'claim_id': c['claim_id'], 'verdict': 'supported',
                         'reason': '인용 근거와 일치', 'evidence_ids': [c['claim_id']]} for c in result['claims']]
    result.pop('supported')
    result.pop('unsupported_claims')
    return result


def test_choices_roundtrip_to_exact_original_reference_and_require_single_verdict():
    original = payload()
    request, schema, choices = prepare_review(original)
    props = schema['$defs']['ClaimCheck']['properties']
    assert 'quotes' not in props and 'reference_error' not in props
    assert props['evidence_ids']['items']['enum'] == [0, 1]
    assert set(schema['$defs']['ClaimCheck']['properties']) == {'claim_id', 'verdict', 'reason', 'evidence_ids'}
    result = resolve_review(wire_result(original), choices)
    assert result.claims[0].quotes == [ref()]
    assert result.claims[1].quotes == [ref('second')]
    assert all(not c.reference_error for c in result.claims)
    assert 'evidence_refs' in original['claims_to_check'][0]  # no shared input mutation


def test_review_schema_binds_claim_item_coverage_and_each_claims_own_citations():
    import jsonschema
    original = payload()
    _, schema, choices = prepare_review(original)
    result = wire_result(original)
    result['claims'] = {str(c.pop('claim_id')): c for c in result['claims']}
    result['item_checks'] = {i.pop('item_id'): i for i in result['item_checks']}
    jsonschema.validate(result, schema)
    restored = resolve_review(result, choices)
    assert [c.claim_id for c in restored.claims] == [0, 1]
    assert [i.item_id for i in restored.item_checks] == ['item-1', 'item-2']
    assert restored.claims[0].quotes == [ref()]
    assert restored.claims[1].quotes == [ref('second')]
    result['claims']['0']['evidence_ids'] = [1]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(result, schema)
    result['claims']['0']['evidence_ids'] = [0]
    result['item_checks'].pop('item-2')
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(result, schema)


@pytest.mark.parametrize('decision,code', [('condition_missing','CONDITION_LOSS'),
    ('subject_mismatch','SUBJECT_NOT_APPLICABLE'),('unsupported','UNSUPPORTED_CLAIM'),('uncertain','UNSUPPORTED_CLAIM')])
def test_single_provider_verdict_has_consistent_failure_flags(decision,code):
    from app.generation.review import verification_failures
    original=payload();_,schema,choices=prepare_review(original)
    wire=wire_result(original)
    wire['claims'][0].update(verdict=decision,reason='원문과 답변의 조건 불일치')
    v=resolve_review(wire,choices)
    assert not v.supported and not v.claims[0].supported and v.unsupported_claims
    failures=verification_failures(v,original['claims_to_check'],{},status='needs_review')
    assert code in {f['code'] for f in failures}
    assert not {'supported','unsupported_claims'} & schema['properties'].keys()
    assert not {'supported','conditions_preserved','comparison'} & schema['$defs']['ClaimCheck']['properties'].keys()


@pytest.mark.parametrize('ids', [[1], [999], [0, 0], [True], ['0']])
def test_unknown_other_claim_duplicate_and_noninteger_choices_cannot_pass(ids):
    original = payload()
    request, _, choices = prepare_review(original)
    output = wire_result(original)
    output['claims'][0]['evidence_ids'] = ids
    result = resolve_review(output, choices)
    assert result.claims[0].reference_error
    assert ref('second') not in result.claims[0].quotes


@pytest.mark.parametrize('repaired', [True, False])
def test_reference_error_retries_only_review_with_same_draft_and_one_recovery(repaired):
    class Review(Blocks):
        async def verify(self, messages):
            self.verify_calls += 1
            v = verified_fixture(messages)
            if self.verify_calls == 1 or not repaired:
                v = v.model_copy(update={'claims': [c.model_copy(update={'reference_error': True}) for c in v.claims]})
            else:
                assert 'VERIFIER_REFERENCE_MISMATCH' in json.loads(messages[-1].content)['review_correction']
            return v, AIMessage(content='')

    model = Review([block()], assessments=[assessment()])
    result = run(model, max_calls=4)
    assert model.final_calls == 1 and model.verify_calls == 2
    assert bool(result.sources) == repaired
    assert result.status == ('answered' if repaired else 'needs_review')
