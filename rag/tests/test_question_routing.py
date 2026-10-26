import asyncio
import hashlib
import json
from dataclasses import replace

import pytest

from app.generation.agent import CounselAgent
from app.generation.question import QuestionPlan, validate_focus
from app.schemas import InternalQuery
from tests.support import FixtureRetriever, ScriptedModel, assessment, draft, fixture_evidence, query_payload


def context(question='교육비가 얼마예요?', history=()):
    return InternalQuery.model_validate({**query_payload(question), 'history': history, 'deadline_ms': 30000})


def test_reviewed_cost_table_keeps_semantic_review_until_exact_row_binding():
    text = '교육비: 3,300천원. 부가세 포함.'
    item = fixture_evidence(material_type='public_disclosure', is_demo=False, section_key='p19.table1', page=19)
    item = replace(item, text=text, quality_flags=('table_reviewed',), metadata=item.metadata.model_copy(update={
        'content_sha256': hashlib.sha256(text.encode()).hexdigest()}))
    model = ScriptedModel(final=draft(answer='교육비는 3,300천원입니다.', steps=[]))
    result = asyncio.run(CounselAgent(model, FixtureRetriever([item])).run(context()))
    assert result.result.status == 'answered'
    assert model.final_calls == model.assess_calls == model.verify_calls == 1 and model.plan_calls == 0
    other = ScriptedModel(final=model.final)
    assert asyncio.run(CounselAgent(other, FixtureRetriever([item]), review_mode='always').run(context())).result.status == 'answered'
    assert other.assess_calls == other.verify_calls == 1


def test_followup_focus_must_come_from_user_turn_not_old_answer():
    c = context('교육비까지 포함이야?', [{'question': '계약이행보증금과 가맹비는 얼마야?', 'answer': '예치가맹금도 별도로 언급'}])
    invented = QuestionPlan(intent='inclusion', resolved_question='예치가맹금에 교육비가 포함되나요?',
        queries=['예치가맹금 교육비'], focus='예치가맹금', focus_turn=0, clarification=False)
    assert validate_focus(invented, c).clarification
    model = ScriptedModel(plans=[invented])
    result = asyncio.run(CounselAgent(model, FixtureRetriever()).run(c)).result
    assert result.follow_up_questions and result.sources == () and model.final_calls == 0


def test_failed_model_selection_can_recover_one_literal_immediate_user_target():
    c = context('그 금액은 언제 내야 해?', [{'question': '계약이행보증금은 얼마야?',
                                           'answer': '없는 대상 99,999천원도 언급'}])
    wrong = QuestionPlan(intent='lookup', resolved_question='없는 대상은 언제 내나요?',
        queries=['없는 대상 납부 기한'], focus='없는 대상', focus_turn=0, clarification=False)
    model = ScriptedModel(plans=[wrong])
    retriever = FixtureRetriever([])
    result = asyncio.run(CounselAgent(model, retriever).run(c)).result
    assert len(retriever.searches) == 1 and '계약이행보증금' in retriever.searches[0]
    assert '99,999' not in result.answer and not result.sources


def test_explicit_model_ambiguity_is_not_overridden_by_a_single_noun_candidate():
    from app.generation.question import repair_single_focus, focus_candidates
    c = context('그 금액은 언제 내?', [{'question': '계약이행보증금은 얼마야?', 'answer': '확인 전'}])
    plan = QuestionPlan(intent='lookup', resolved_question=c.question, queries=[c.question],
        focus=None, focus_turn=None, clarification=True)
    assert repair_single_focus(plan, c, focus_candidates(c)) is None


def test_resolved_focus_is_fixed_before_search_and_cannot_drift_in_assessment():
    c = context('교육비까지 포함이야?', [{'question': '계약이행보증금은 얼마야?', 'answer': '예치가맹금도 별도로 언급'}])
    plan = QuestionPlan(intent='inclusion', resolved_question='계약이행보증금에 교육비가 포함되나요?',
        queries=['계약이행보증금 교육비 포함 여부'], focus='계약이행보증금', focus_turn=0, clarification=False)
    class Capture(ScriptedModel):
        async def finalize(self, messages):
            self.payload = json.loads(messages[-1].content)
            return await super().finalize(messages)
    model = Capture(plans=[plan], assessments=[assessment(ids=['fixture-chunk-1'])])
    retriever = FixtureRetriever()
    asyncio.run(CounselAgent(model, retriever).run(c))
    assert len(retriever.searches) == 1 and retriever.searches[0].startswith('계약이행보증금')
    assert model.plan_calls == 1
    assert model.payload['question'] == plan.resolved_question
    assert model.payload['original_question'] == c.question


def test_comparison_searches_both_requested_parts_within_two_search_budget():
    c = context('영업자 교육과 직원 관리를 같은 기준으로 봐도 돼?')
    plan = QuestionPlan(intent='comparison', resolved_question=c.question,
                        queries=['영업자 교육 대상', '직원 건강진단 위생관리'],
                        focus=None, focus_turn=None, clarification=False)
    model = ScriptedModel(plans=[plan])
    retriever = FixtureRetriever()
    asyncio.run(CounselAgent(model, retriever).run(c))
    assert retriever.searches == plan.queries and model.plan_calls == 1


def test_explicit_comparison_does_not_require_a_copied_single_focus():
    c = context('영업자 교육과 직원 위생 관리를 같은 기준으로 보면 되나요?')
    plan = QuestionPlan(intent='comparison', resolved_question=c.question,
        queries=['영업자 교육', '직원 건강관리'], focus='영업자 교육/직원 위생관리', focus_turn=0, clarification=False)
    checked = validate_focus(plan, c)
    assert not checked.clarification and checked.focus is None


@pytest.mark.parametrize('question', [
    '계약이행보증금과 교육비는 별도인가요? 납부 기한도 같은가요?',
    '- 계약이행보증금과 교육비는 별도인가요? 납부 기한도 같은가요?',
    '배송비와 설치비는 별도인가요? 지급기한도 동일한가요?',
    '가입비와 이용료의 납부일이 같나요?',
])
def test_relational_requests_cannot_be_reduced_to_one_table_lookup(question):
    from app.generation.question import default_plan, needs_planning
    c = context(question)
    assert default_plan(c).intent == 'comparison' and needs_planning(c)
    mistaken = QuestionPlan(intent='lookup', resolved_question=question, queries=[question],
                            focus=None, focus_turn=None, clarification=False)
    checked = validate_focus(mistaken, c)
    assert checked.intent == 'comparison' and not checked.clarification
    model = ScriptedModel(plans=[mistaken])
    agent = CounselAgent(model, FixtureRetriever())
    assert agent._exact_coverage({'context': c, 'plan': mistaken, 'evidence': {}}) is None
    result = asyncio.run(agent.run(c))
    assert model.plan_calls == 1 and model.assess_calls == 1


def test_simple_amount_request_remains_lookup_without_extra_planning():
    from app.generation.question import default_plan, needs_planning
    c = context('로열티는 얼마이고 언제까지 내나요? 부가세도 포함인가요?')
    assert default_plan(c).intent == 'lookup' and not needs_planning(c)


def test_comparison_keeps_requested_table_fields_from_authorized_candidates():
    from app.generation.question import default_plan
    from tests.support import source
    c = context('배송비와 설치비는 별도인가요? 납부 기한도 같은가요?')
    rows = {}
    for n, name in enumerate(('배송비', '설치비')):
        title, unit = '납부 기준', '(단위: 천원)'
        e = source(f'{title}\n{unit}\n- {name}: 금액 30 · 지급기한 결제 후 3일 이내', f'cost-{n}')
        rows[e.metadata.chunk_id] = replace(e, table={'schema': 'reviewed-cells-v1', 'reviewed': True,
            'title': title, 'unit': unit, 'columns': ['구분', '금액', '지급기한'],
            'rows': [[name, '30', '결제 후 3일 이내']]})
    other = source('일반 예치 절차 설명', 'other')
    updates = {'candidates': {other.metadata.chunk_id: other, **rows},
               'evidence': {other.metadata.chunk_id: other}}
    agent = CounselAgent(ScriptedModel(), FixtureRetriever())
    state = {'context': c, 'plan': default_plan(c)}
    result = agent._comparison_context(state, updates)
    assert list(result['evidence']) == [*rows, 'other']
    assert result['evidence']['cost-0'] is rows['cost-0']
    assert list(updates['evidence']) == ['other']
    state['plan'] = state['plan'].model_copy(update={'intent': 'lookup'})
    assert agent._comparison_context(state, updates) is updates


def test_focus_candidates_are_literal_user_spans_and_exclude_the_included_component():
    from app.generation.question import focus_candidates
    c = context('교육비까지 포함이야?', [{'question': '계약이행보증금은 얼마야?', 'answer': '예치가맹금 4,800천원도 언급'}])
    candidates = focus_candidates(c)
    assert {r['text'] for r in candidates if r['source'] == 'question'} == {'계약이행보증금'}
    assert any(r['source'] == 'answer' and r['text'] == '예치가맹금' for r in candidates)


def test_self_contained_inclusion_question_does_not_inherit_an_old_target():
    c = context('교육비에 부가세가 포함돼?', [{'question': '보증금은 얼마야?', 'answer': '확인한 보증금 설명'}])
    model = ScriptedModel()
    retriever = FixtureRetriever()
    asyncio.run(CounselAgent(model, retriever).run(c))
    assert model.plan_calls == 0 and retriever.searches == [c.question]
    assert json.loads(model.seen_messages[-1].content)['user_question_chain'] == [c.question]


def test_independent_question_hides_old_topics_but_reference_keeps_antecedent():
    history = [{'question': '교육비는 얼마야?', 'answer': '이전 교육비 안내'}]
    agent = CounselAgent(ScriptedModel(), FixtureRetriever())
    c = context('계약이 끝나면 보증금을 돌려받으려면 어떤 서류가 필요해?', history)
    payload = agent._context({'context': c, 'plan': None})
    assert payload['history'] == [] and payload['user_question_chain'] == [c.question]
    followup = context('그 금액에 교육비도 포함돼?', history)
    payload = agent._context({'context': followup, 'plan': None})
    assert payload['history'] and payload['user_question_chain'] == [history[0]['question'], followup.question]


@pytest.mark.parametrize('question', [
    '로열티는 얼마이고 언제까지 내나요? 부가세도 포함인가요?',
    '매장을 이전하며 인테리어를 바꾸면 본사는 얼마나 부담하고, 장비 교체비도 포함되나요?',
    '배송비는 얼마인가요? 설치비도 포함인가요?',
    '제품을 교환하면 비용이 얼마나 들고, 반송비도 포함되나요?',
])
def test_current_message_clauses_supply_inclusion_target_without_old_conversation(question):
    c = context(question, [{'question': '계약이행보증금은 얼마야?', 'answer': '다른 항목의 안내'}])
    from app.generation.question import needs_reference, needs_planning
    assert not needs_reference(c) and not needs_planning(c)
    model, retriever = ScriptedModel(), FixtureRetriever()
    asyncio.run(CounselAgent(model, retriever).run(c))
    assert model.plan_calls == 0 and retriever.searches == [question]


@pytest.mark.parametrize('question', ['교육비까지 포함이야?', '부가세도 포함인가요?',
    '그 금액에 교육비도 포함돼?', '아까 그 비용은 얼마이고, 교육비도 포함인가요?'])
def test_genuinely_omitted_or_explicit_backward_targets_still_require_reference(question):
    from app.generation.question import needs_reference
    c = context(question, [{'question': '계약이행보증금은 얼마야?', 'answer': '기존 항목 안내'}])
    assert needs_reference(c)


def test_answer_topic_can_be_resolved_but_its_amount_is_not_evidence():
    c = context('그 금액에 교육비도 포함돼?', [{'question': '개설 비용을 알려줘', 'answer': '예치가맹금 99,999천원입니다.'}])
    plan = QuestionPlan(intent='inclusion', resolved_question='예치가맹금에 교육비가 포함되나요?',
        queries=['예치가맹금 교육비 포함 여부'], focus='예치가맹금', focus_turn=0, focus_source='answer', clarification=False)
    model = ScriptedModel(plans=[plan])
    retriever = FixtureRetriever([])
    result = asyncio.run(CounselAgent(model, retriever).run(c)).result
    assert not validate_focus(plan, c).clarification
    assert retriever.searches == plan.queries
    assert model.final_calls == 1 and result.sources == () and '99,999' not in result.answer


def test_long_answer_cannot_push_user_targets_out_of_the_focus_catalog():
    from app.generation.question import focus_candidates
    c = context('그 금액이야?', [{'question': '계약이행보증금은 얼마야?',
                              'answer': ' '.join(f'안내항목{n} 설명입니다.' for n in range(100))}])
    candidates = focus_candidates(c)
    assert len(candidates) <= 32
    assert any(r['source'] == 'question' and r['text'] == '계약이행보증금' for r in candidates)


def test_conditional_payment_and_explanation_requests_keep_deep_review():
    text = '교육비: 3,300천원. 부가세 포함.'
    item = fixture_evidence(material_type='public_disclosure', is_demo=False, section_key='p19.table1', page=19)
    item = replace(item, text=text, quality_flags=('table_reviewed',), metadata=item.metadata.model_copy(update={
        'content_sha256': hashlib.sha256(text.encode()).hexdigest()}))
    for question in ['교육비 납부기한이 지나면 어떻게 돼?', '교육비 금액을 면제받을 수 있어?', '왜 교육비를 내야 하나요?']:
        model = ScriptedModel(final=draft(answer='교육비는 3,300천원입니다.', steps=[]))
        asyncio.run(CounselAgent(model, FixtureRetriever([item])).run(context(question)))
        assert model.assess_calls == model.verify_calls == 1


def test_total_antecedent_is_a_literal_compound_span_not_three_unrelated_candidates():
    from app.generation.question import focus_candidates, resolve_aggregate_followup
    for target in ['가맹비와 교육비의 합계', '배송비와 설치비의 총액']:
        c=context('그 금액은 부가세 포함이야?', [{'question':target+'는 얼마인가요?',
            'answer':'문서 기준: 부가가치세 포함. 합계 99,999천원.'}])
        candidates=focus_candidates(c)
        assert [r['text'] for r in candidates if r['source']=='question']==[target]
        plan=resolve_aggregate_followup(c,candidates)
        assert plan and plan.focus==target and plan.focus_source=='question'
        assert plan.queries==[target+('은' if target.endswith('총액') else '는')+' 부가세 포함이야?']
        assert not plan.clarification and not validate_focus(plan,c).clarification
        assert '99,999' not in plan.resolved_question


def test_independent_amounts_older_reference_or_explicit_new_target_do_not_use_total_shortcut():
    from app.generation.question import focus_candidates, resolve_aggregate_followup
    samples=[
        ('그 금액은 부가세 포함이야?', '교육비와 보증금은 각각 얼마인가요?'),
        ('아까 그 금액은 부가세 포함이야?', '배송비와 설치비의 합계는 얼마인가요?'),
        ('교육비에는 부가세가 포함돼?', '배송비와 설치비의 합계는 얼마인가요?'),
        ('그 금액은 부가세 포함이야? 보증금은 언제 내?', '배송비와 설치비의 합계는 얼마인가요?'),
    ]
    for question,previous in samples:
        c=context(question,[{'question':previous,'answer':'금액별 안내'}])
        assert resolve_aggregate_followup(c,focus_candidates(c)) is None


def test_clear_total_followup_skips_model_target_guessing_but_still_requires_document_evidence():
    c=context('그 금액에 교육비도 포함돼?', [{'question':'등록비와 교육비의 합계는 얼마야?',
        'answer':'이전 답변은 99,999천원이며 교육비를 포함한다고 주장했다.'}])
    class NoTargetGuess(ScriptedModel):
        async def plan(self,messages):
            raise AssertionError('A unique requested total is already bound to the user span')
    model=NoTargetGuess()
    retriever=FixtureRetriever([])
    result=asyncio.run(CounselAgent(model,retriever).run(c)).result
    assert retriever.searches==['등록비와 교육비의 합계에 교육비도 포함돼?']
    assert result.status=='needs_review' and not result.sources
    assert '99,999' not in result.answer


def test_multiple_amounts_keep_the_existing_clarification_path():
    c=context('그 금액은 부가세 포함이야?', [{'question':'교육비와 보증금은 각각 얼마야?',
        'answer':'서로 다른 두 금액을 안내했다.'}])
    model=ScriptedModel(plans=[QuestionPlan(intent='inclusion', resolved_question=c.question,
        queries=[c.question],focus=None,focus_turn=None,clarification=True)])
    retriever=FixtureRetriever([])
    result=asyncio.run(CounselAgent(model,retriever).run(c)).result
    assert model.plan_calls==1 and not retriever.searches
    assert result.follow_up_questions and not result.sources


def test_repeating_a_failed_followup_keeps_its_original_single_total_target():
    from app.generation.question import focus_candidates, resolve_aggregate_followup
    question='그 금액은 부가세 포함이야?'
    c=context(question,[{'question':'배송비와 설치비의 합계는 얼마야?', 'answer':'원문에 있는 합계 안내'},
        {'question':question, 'answer':'어떤 금액을 말씀하시는지 알려 주세요.'}])
    plan=resolve_aggregate_followup(c,focus_candidates(c))
    assert plan and plan.focus=='배송비와 설치비의 합계' and plan.focus_turn==0
    assert plan.resolved_question=='배송비와 설치비의 합계는 부가세 포함이야?'
    assert not validate_focus(plan,c).clarification
