"""Control-flow regressions, not a claim that an LLM judge is always correct."""
import asyncio
import hashlib
import json
from dataclasses import replace

import pytest

from app.errors import ServiceError
from app.generation.agent import CounselAgent, claim_statements
from app.generation.review import Verification, ClaimCheck, ItemCheck, Quote
from app.schemas import InternalQuery
from tests.support import FixtureRetriever, ScriptedModel, assessment, draft, fixture_evidence, query_payload, recovery_review, verified_fixture


def run(model, retriever=None, **options):
    context = InternalQuery.model_validate({**query_payload(), 'deadline_ms': 30000})
    return asyncio.run(CounselAgent(model, retriever or FixtureRetriever(), **options).run(context))


def check(supported, reason='적용 대상과 수치의 연결 확인 필요'):
    return Verification(supported=supported, reason=reason, unsupported_claims=[] if supported else ['다른 업종의 기준을 적용함'],
                        context_consistent=True, question_answered=True, item_checks=[ItemCheck(item_id='item-1', complete=supported)],
                        recovery='none' if supported else 'rewrite', missing_item_ids=[], query=None, section_ids=[],
                        claims=[ClaimCheck(claim_id=c['id'], supported=supported, quotes=[Quote(chunk_id='fixture-chunk-1', unit_id='unit-0')], applicable=True, conditions_preserved=True, source_subject='테스트 항목 A', answer_subject='테스트 항목 A', scope_relation='same') for c in claim_statements(draft())])


def test_verified_clarification_survives_without_sources():
    model = ScriptedModel(assessments=[assessment('clarify', questions=['매장에서 만든 음료인가요, 포장 제품인가요?'])])
    result = run(model, FixtureRetriever([])).result
    assert result.sources == () and result.status == 'needs_review'
    assert result.follow_up_questions == ('매장에서 만든 음료인가요, 포장 제품인가요?',)
    assert result.answer == result.follow_up_questions[0]
    assert model.final_calls == model.verify_calls == 1


def test_unsupported_clarification_is_discarded():
    model = ScriptedModel(assessments=[assessment('clarify', questions=['모든 음료는 3일 안에 환불해야 하는데 맞나요?'])],
                          verifications=[check(False)])
    result = run(model, FixtureRetriever([])).result
    assert result.follow_up_questions == () and '3일' not in result.answer


def test_handoff_without_sources_is_fixed_guidance_not_a_generated_policy():
    model = ScriptedModel(assessments=[assessment('handoff', reason='배상액은 500만원', questions=['500만원 맞나요?'])])
    result = run(model, FixtureRetriever([])).result
    assert result.status == 'handoff' and result.handoff and result.sources == ()
    assert '500' not in str(result) and result.follow_up_questions == ()
    assert model.final_calls == model.verify_calls == 1


def test_authorized_candidate_is_not_automatically_a_claim_source():
    rejected = fixture_evidence('rejected')
    text = 'REJECTED_SOURCE_SECRET'
    rejected = replace(rejected, text=text, metadata=rejected.metadata.model_copy(update={
        'content_sha256': hashlib.sha256(text.encode()).hexdigest()}))

    class Capture(ScriptedModel):
        async def finalize(self, messages):
            self.writer_payload = json.loads(messages[-1].content)
            return await super().finalize(messages)

    model = Capture(assessments=[assessment(ids=['fixture-chunk-1'])])
    valid = run(model, FixtureRetriever([rejected, fixture_evidence()])).result
    assert valid.status == 'answered'
    assert [s.chunk_id for s in valid.sources] == ['fixture-chunk-1']
    assert {e['chunk_id'] for e in model.writer_payload['evidence']} == {'rejected', 'fixture-chunk-1'}
    def reject_unrelated_source(messages):
        v = verified_fixture(messages)
        return v.model_copy(update={'supported': False, 'unsupported_claims': ['인용 원문은 답변을 뒷받침하지 않음'],
            'claims': [c.model_copy(update={'supported': False}) for c in v.claims],
            'item_checks': [i.model_copy(update={'complete': False}) for i in v.item_checks], 'recovery': 'rewrite'})
    model = Capture(final=draft(used_chunk_ids=['rejected']), assessments=[assessment(ids=['fixture-chunk-1'])],
                    verifications=[reject_unrelated_source])
    result = run(model, FixtureRetriever([rejected, fixture_evidence()])).result
    assert result.status == 'needs_review' and not result.sources
    assert model.final_calls == model.verify_calls == 2
    assert 'REJECTED_SOURCE_SECRET' not in result.answer




@pytest.mark.parametrize('eventually_supported', [True, False])
def test_claim_failure_gets_one_rewrite_then_verified_or_safe_response(eventually_supported):
    class Capture(ScriptedModel):
        async def finalize(self, messages):
            self.correction = json.loads(messages[-1].content)['correction']
            return await super().finalize(messages)
    model = Capture(verifications=[check(False), check(eventually_supported)])
    result = run(model).result
    assert model.final_calls == model.verify_calls == 2
    assert '다른 업종' in model.correction
    assert result.status == ('answered' if eventually_supported else 'needs_review')
    if not eventually_supported:
        assert result.sources == () and result.steps == ()


def test_budget_reserves_verification_and_prevents_rewrite():
    model = ScriptedModel(verifications=[check(False)])
    result = run(model, max_model_calls=3).result
    assert result.status == 'needs_review' and model.final_calls == model.verify_calls == 1
    assert model.assess_calls + model.final_calls + model.verify_calls == 3


@pytest.mark.parametrize('field', ['question_answered', 'context_consistent'])
def test_completion_or_context_failure_without_action_gets_one_rewrite(field):
    def incomplete(messages):
        return verified_fixture(messages).model_copy(update={field: False, 'recovery': 'none'})
    model = ScriptedModel(verifications=[incomplete, verified_fixture])
    result = run(model).result
    assert result.status == 'answered'
    assert model.final_calls == model.verify_calls == 2


def test_missing_source_with_explicit_location_reuses_one_section_read_without_action():
    def missing(messages):
        v = verified_fixture(messages)
        return v.model_copy(update={'question_answered': False, 'recovery': 'none',
            'missing_item_ids': ['item-1'], 'section_ids': ['fixture-chunk-1']})
    model = ScriptedModel(verifications=[missing, verified_fixture])
    retriever = FixtureRetriever()
    result = run(model, retriever).result
    assert result.status == 'answered'
    assert retriever.section_reads == [('fixture-chunk-1',)]
    assert model.final_calls == model.verify_calls == 2


def test_missing_user_condition_is_not_researched_even_if_judge_requests_a_read():
    def unnecessary_read(messages):
        return verified_fixture(messages).model_copy(update={'recovery': 'read_sections',
            'missing_item_ids': ['item-1'], 'section_ids': ['fixture-chunk-1']})
    model = ScriptedModel(assessments=[assessment('clarify', questions=['매장의 업종은 무엇인가요?'])],
                          verifications=[unnecessary_read])
    retriever = FixtureRetriever()
    result = run(model, retriever).result
    assert not retriever.section_reads and model.final_calls == model.verify_calls == 1
    assert result.follow_up_questions == ('매장의 업종은 무엇인가요?',)


@pytest.mark.parametrize('failure', ['timeout', 'upstream'])
def test_interrupted_rewrite_returns_only_previously_verified_items_on_timeout(failure):
    from tests.support import Blocks, block, item, ref, source
    first = source('첫 항목의 확인된 내용입니다.', 'first')
    second = source('둘째 항목의 미확인 내용입니다.', 'second')
    a = assessment(items=[item(refs=[ref('first')]), item('item-2', refs=[ref('second')])])
    class Interrupted(Blocks):
        async def finalize(self, messages):
            if self.final_calls:
                if failure == 'timeout':
                    raise TimeoutError()
                raise ServiceError(502, 'UPSTREAM_ERROR', '오류', retryable=True)
            return await super().finalize(messages)
    def partial(messages):
        v = verified_fixture(messages)
        return v.model_copy(update={'supported': False, 'question_answered': False,
            'unsupported_claims': ['둘째 항목은 미확인'], 'recovery': 'rewrite',
            'claims': [c.model_copy(update={'supported': c.claim_id == 0}) for c in v.claims],
            'item_checks': [c.model_copy(update={'complete': c.item_id == 'item-1'}) for c in v.item_checks]})
    model = Interrupted([block(text=first.text, refs=[ref('first')]),
        block(1, item_id='item-2', text=second.text, refs=[ref('second')])],
        assessments=[a], verifications=[partial])
    if failure == 'upstream':
        with pytest.raises(ServiceError) as exc:
            run(model, FixtureRetriever([first, second]))
        assert exc.value.status == 502
    else:
        result = run(model, FixtureRetriever([first, second])).result
        assert result.status == 'needs_review' and not result.handoff
        assert first.text in result.answer and second.text not in result.answer
        assert [s.chunk_id for s in result.sources] == ['first']
        assert '제한 시간' in result.handoff_reason


def test_timeout_without_a_reviewed_answer_still_returns_timeout_error():
    class Interrupted(ScriptedModel):
        async def finalize(self, messages):
            raise TimeoutError()
    with pytest.raises(ServiceError) as exc:
        run(Interrupted())
    assert exc.value.status == 504 and exc.value.code == 'UPSTREAM_TIMEOUT'


def test_parent_expansion_cannot_escape_reviewed_section():
    class Leaky(FixtureRetriever):
        async def read_sections(self, chunk_ids, context):
            from app.retrieval.base import EvidenceBatch
            return EvidenceBatch((fixture_evidence('foreign-parent', section_key='other-section'),))
    model = ScriptedModel(assessments=[assessment('read_sections', sections=['fixture-chunk-1'])],
                         verifications=[recovery_review('read_sections', sections=['fixture-chunk-1'])])
    with pytest.raises(ServiceError) as error:
        run(model, Leaky())
    assert error.value.status == 502


@pytest.mark.parametrize('bad_quote', [False, True])
def test_supported_flag_cannot_override_wrong_context_or_fabricated_quote(bad_quote):
    verdict = check(True)
    if bad_quote:
        claim = verdict.claims[0].model_copy(update={'quotes': [Quote(chunk_id='fixture-chunk-1', unit_id='unit-999')]})
        verdict = verdict.model_copy(update={'claims': [claim]})
    else:
        verdict = verdict.model_copy(update={'context_consistent': False})
    model = ScriptedModel(verifications=[verdict])
    result = run(model, max_model_calls=3).result
    assert result.status == 'needs_review' and result.sources == ()


def test_missing_claim_coverage_is_not_accepted_as_a_valid_answer():
    verdict = check(True)
    verdict = verdict.model_copy(update={'claims': verdict.claims[:-1]})
    result = run(ScriptedModel(verifications=[verdict]), max_model_calls=3).result
    assert result.status == 'needs_review' and result.sources == ()
    assert '일치를 확인하지 못해' in result.answer
    assert result.handoff_reason == '근거와 답변의 적합성 추가 확인 필요'


def test_repaired_partial_answer_is_verified_again_before_retaining_sources():
    partial = draft(answer='항목 A를 확인합니다.', steps=[], status='needs_review', handoff_reason='일부 확인 필요')
    accepted = check(True).model_copy(update={'item_checks': [ItemCheck(item_id='item-1', complete=False)], 'claims': [
        ClaimCheck(claim_id=c['id'], supported=True, quotes=[Quote(chunk_id='fixture-chunk-1', unit_id='unit-0')], applicable=True, conditions_preserved=True, source_subject='테스트 항목 A', answer_subject='테스트 항목 A', scope_relation='same') for c in claim_statements(partial)]})

    class Repair(ScriptedModel):
        async def finalize(self, messages):
            if self.final_calls:
                payload = json.loads(messages[-1].content)
                assert 'failures' in json.loads(payload['correction'])
                self.final = partial
            return await super().finalize(messages)

    model = Repair(verifications=[check(False), accepted])
    result = run(model).result
    assert model.final_calls == model.verify_calls == 2
    assert result.status == 'needs_review' and not result.sources
    assert result.follow_up_questions == ()


def test_partial_status_does_not_bypass_condition_or_quote_checks():
    partial = draft(status='needs_review', handoff_reason='일부 항목 근거 부족')
    verdict = check(True)
    verdict = verdict.model_copy(update={'claims': [
        c.model_copy(update={'conditions_preserved': False}) for c in verdict.claims]})
    result = run(ScriptedModel(final=partial, verifications=[verdict]), max_model_calls=3).result
    assert result.sources == () and '항목 A' not in result.answer


def test_diagnostics_distinguish_content_failure_without_logging_text(caplog):
    verdict = check(True)
    verdict = verdict.model_copy(update={'context_consistent': False, 'reason': 'PRIVATE_MODEL_REASON',
        'claims': [c.model_copy(update={'conditions_preserved': False}) for c in verdict.claims]})
    with caplog.at_level('INFO', logger='uvicorn.error'):
        run(ScriptedModel(verifications=[verdict]), max_model_calls=3)
    events = [json.loads(r.message.split('counsel_decision ', 1)[1])
              for r in caplog.records if r.message.startswith('counsel_decision ')]
    review = next(e for e in events if e['stage'] == 'verification')
    assert {'CONTEXT_MISMATCH', 'CONDITION_LOSS'} <= set(review['codes'])
    assert any(f['claim_ids'] == [0] for f in review['claim_failures'])
    assert events[-1]['codes'] == ['REPAIR_BUDGET_EXHAUSTED']
    assert 'PRIVATE_MODEL_REASON' not in caplog.text
    assert fixture_evidence().text not in caplog.text and draft().answer not in caplog.text


def test_verification_timeout_is_not_reported_as_semantic_rejection(caplog):
    class Timeout(ScriptedModel):
        async def verify(self, messages):
            raise TimeoutError
    with caplog.at_level('INFO', logger='uvicorn.error'):
        with pytest.raises(ServiceError) as error:
            run(Timeout())
    assert error.value.status == 504 and error.value.retryable
    assert 'VERIFICATION_TIMEOUT' in caplog.text
    assert 'CONDITION_LOSS' not in caplog.text


def test_empty_source_span_cannot_be_selected():
    verdict = check(True)
    verdict = verdict.model_copy(update={'claims': [c.model_copy(update={
        'quotes': [Quote(chunk_id='fixture-chunk-1', unit_id='unit-999')]}) for c in verdict.claims]})
    result = run(ScriptedModel(verifications=[verdict]), max_model_calls=3).result
    assert result.sources == ()


@pytest.mark.parametrize('scope', ['broader', 'uncertain'])
def test_valid_source_unit_does_not_override_expanded_or_uncertain_subject(scope):
    verdict = check(True)
    verdict = verdict.model_copy(update={'claims': [c.model_copy(update={'scope_relation': scope}) for c in verdict.claims]})
    result = run(ScriptedModel(verifications=[verdict]), max_model_calls=3).result
    assert result.sources == () and result.status == 'needs_review'


def test_source_units_keep_wrapped_subject_and_subitems_inside_paragraph():
    from app.generation.review import source_units
    text = '제9조(합성 규정) ① 특정 자격을 갖춘\n담당자는 항목 A를 확인한다.\n1. 예외 조건\n② 다른 대상의 기준.'
    item = replace(fixture_evidence(), text=text)
    units = source_units(item)
    assert len(units) == 3
    assert units[1]['text'] == '① 특정 자격을 갖춘\n담당자는 항목 A를 확인한다.\n1. 예외 조건\n'
    assert ''.join(text[u['start']:u['end']] for u in units) == text


def test_verifier_receives_only_own_task_and_no_old_assistant_facts():
    class Capture(ScriptedModel):
        async def verify(self, messages):
            assert len(messages) == 1 and messages[0].type == 'human'
            payload = json.loads(messages[0].content)
            assert 'history' not in payload and 'answer' not in payload['draft']
            assert 'text' not in payload['evidence'][0]
            assert payload['evidence'][0]['source_units'][0]['unit_id'] == 'unit-0'
            return await super().verify(messages)
    assert run(Capture()).result.status == 'answered'


def test_valid_paraphrase_does_not_require_verbatim_source_sentence():
    from app.generation.review import verification_failures
    item = replace(fixture_evidence(), text='① 본사가 정한 담당자는 당일 점검표를 확인하여야 한다.')
    verdict = check(True).model_copy(update={'claims': [check(True).claims[0]]})
    facts = [{'id': 0, 'text': '본사가 정한 담당자는 당일 점검표를 확인해야 합니다.',
              'evidence_refs': [Quote(chunk_id='fixture-chunk-1', unit_id='unit-0')]}]
    assert verification_failures(verdict, facts, {'fixture-chunk-1': item}, status='answered') == []


def test_fact_blocks_keep_ids_and_wrapped_quotes_without_resplitting():
    from app.generation.review import AnswerBlock, Quote
    text = '원문은 “특정 자격을 갖춘\n담당자는 항목 A를 확인하여야 한다. 예외도 확인한다.”입니다.'
    block = AnswerBlock(block_id=17, item_id='item-1', text=text, placement='answer',
                        evidence_refs=[Quote(chunk_id='fixture-chunk-1', unit_id='unit-0')], quantities=[])
    claims = claim_statements(draft(blocks=[block], answer=text, steps=[]))
    assert len(claims) == 1 and claims[0]['id'] == 17 and claims[0]['text'] == text


def test_unselected_candidate_can_be_read_without_another_search():
    from app.retrieval.base import EvidenceBatch
    first = fixture_evidence(section_key='first')
    tail = fixture_evidence('tail', section_key='tail')
    class Candidates(FixtureRetriever):
        async def search(self, query, context):
            self.searches.append(query)
            return EvidenceBatch((first,), candidates=(first, tail))
    class Capture(ScriptedModel):
        async def assess(self, messages):
            payload = json.loads(messages[-1].content)
            if not self.assess_calls:
                assert payload['candidate_catalog'][0]['chunk_id'] == 'tail'
                assert 'text' not in payload['candidate_catalog'][0]
            return await super().assess(messages)
    model = Capture(final=draft(used_chunk_ids=['tail']), assessments=[
        assessment('read_sections', sections=['tail']), assessment(ids=['tail'])],
        verifications=[recovery_review('read_sections', sections=['tail']), verified_fixture])
    retriever = Candidates([first, tail])
    result = run(model, retriever).result
    assert result.status == 'answered' and result.sources[0].chunk_id == 'tail'
    assert len(retriever.searches) == 1 and retriever.section_reads == [('tail',)]


def test_candidate_catalog_cannot_leak_unauthorized_rows():
    from app.retrieval.base import EvidenceBatch
    class Leaky(FixtureRetriever):
        async def search(self, query, context):
            return EvidenceBatch((fixture_evidence(),), candidates=(fixture_evidence('secret', visibility='hq'),))
    model = ScriptedModel()
    with pytest.raises(ServiceError) as error:
        run(model, Leaky())
    assert error.value.status == 502 and model.assess_calls == 0


def test_comparison_ranks_once_and_leaves_one_search_for_recovery():
    from app.generation.question import QuestionPlan
    from app.retrieval.reranker import RankResult
    class Ranker:
        calls = 0
        async def rank(self, question, candidates):
            self.calls += 1
            assert question == '두 대상의 기준을 비교해줘'
            return RankResult((0,), 11, 3)
    plan = QuestionPlan(intent='comparison', resolved_question='두 대상의 기준을 비교해줘',
        queries=['첫 대상의 기준', '다른 대상의 기준'], focus=None, focus_turn=None, clarification=False)
    model = ScriptedModel(plans=[plan], assessments=[assessment('research', query='빠진 적용 조건'), assessment()],
                          verifications=[recovery_review('research', query='빠진 적용 조건'), verified_fixture])
    retriever, ranker = FixtureRetriever(), Ranker()
    context = InternalQuery.model_validate({**query_payload('두 대상의 기준을 비교해줘'), 'deadline_ms': 30000})
    result = asyncio.run(CounselAgent(model, retriever, reranker=ranker).run(context))
    assert result.result.status == 'answered'
    assert retriever.searches == [*plan.queries, '빠진 적용 조건'] and ranker.calls == 1
    assert model.plan_calls + model.final_calls + model.verify_calls + ranker.calls == 6
    assert result.usage.input_tokens == 31 and result.usage.output_tokens == 13


@pytest.mark.parametrize('fails', [False, True])
def test_empty_or_failed_ranking_keeps_hybrid_candidates_and_counts_attempt(fails, caplog):
    from app.retrieval.reranker import RankResult
    class Ranker:
        async def rank(self, question, candidates):
            if fails:
                raise RuntimeError('provider unavailable')
            return RankResult(())
    with caplog.at_level('INFO', logger='uvicorn.error'):
        result = run(ScriptedModel(), reranker=Ranker()).result
    assert result.status == 'answered' and result.sources
    events = [json.loads(r.message.split('counsel_decision ', 1)[1]) for r in caplog.records
              if r.message.startswith('counsel_decision ')]
    assert next(e for e in events if e['stage'] == 'verification')['model_calls'] == 3
