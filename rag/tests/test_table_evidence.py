import asyncio
import copy
import json
from dataclasses import replace

import httpx
import jsonschema
import pytest
from langchain_core.messages import HumanMessage, AIMessage
from langchain_openai import ChatOpenAI

from app.errors import ServiceError
from app.generation.agent import CounselAgent, evidence_payload
from app.generation.model import CounselDraft, LangChainModel, ProposedAnswer, DraftItem
from app.generation.review import Quantity, Quote, block_failures
from app.retrieval.base import EvidenceBatch
from app.generation.table_evidence import (prepare_table_writer, resolve_table_writer, table_rows, lookup_sources,
                                         prepare_draft_tables, resolve_draft_tables)
from app.schemas import InternalQuery
from tests.support import (
    assessment, FixtureRetriever, query_payload, source, block, Driver, proposal, inspection_fixture,
    item, ref, Blocks, reviewed_table, writer_payload,
)


@pytest.mark.parametrize('damage', ['number', 'column', 'unit', 'review', 'blocked', 'truncated', 'legacy'])
def test_metadata_cannot_create_table_facts_outside_the_literal_reviewed_source(damage):
    item = reviewed_table()
    t = copy.deepcopy(item.table)
    if damage == 'number':
        t['rows'][0][1] = '9,999'
    elif damage == 'column':
        t['columns'][1] = '보증금'
    elif damage == 'unit':
        t['unit'] = '(단위: 원)'
    elif damage == 'review':
        t['reviewed'] = False
    elif damage == 'legacy':
        t.pop('schema')
    elif damage == 'blocked':
        item = replace(item, quality_flags=('table_needs_review',))
    elif damage == 'truncated':
        item = replace(item, truncated=True)
    assert table_rows(replace(item, table=t)) == []


def test_selected_cell_ids_restore_literal_values_units_and_item_owned_sources():
    item = reviewed_table()
    payload = writer_payload(item)
    _, schema, choices = prepare_table_writer(payload, CounselDraft.model_json_schema())
    assert schema['properties']['blocks']['maxItems'] == 0
    assert [c['value'] for c in choices] == ['3,300', '계약체결일로부터 3일 이내']
    raw = {'blocks': [], 'category': 'franchise', 'table_choice_ids': [0, 1]}
    resolved = resolve_table_writer(raw, choices)
    draft = CounselDraft.model_validate(resolved)
    assert '3,300' in draft.blocks[0].text and '천원' in draft.blocks[0].text
    assert len(draft.blocks) == 1 and '3일 이내' in draft.blocks[0].text
    assert draft.blocks[0].quantities[0].unit == '천원'
    assert draft.blocks[0].quantities[1].unit == '일'
    assert block_failures(draft.blocks, assessment(), {item.metadata.chunk_id: item}) == []
    assert raw['blocks'] == []  # Provider output is not mutated.
    assert 'table_rows' in payload['evidence'][0]


@pytest.mark.parametrize('selected', [[999], [True], ['0']])
def test_unknown_or_noninteger_cell_ids_are_rejected(selected):
    _, _, choices = prepare_table_writer(writer_payload(reviewed_table()), CounselDraft.model_json_schema())
    with pytest.raises(ServiceError):
        resolve_table_writer({'blocks': [], 'category': 'franchise', 'table_choice_ids': selected}, choices)


def test_unapproved_source_units_produce_no_choices():
    payload = writer_payload(reviewed_table())
    payload['evidence'][0]['source_units'] = []
    _, schema, choices = prepare_table_writer(payload, CounselDraft.model_json_schema())
    assert choices == [] and 'table_choice_ids' not in schema['properties']


def test_exact_lookup_uses_verified_source_selection_and_final_review_without_extra_judges():
    class Writer(Blocks):
        async def assess(self, messages):
            raise AssertionError('The source already contains the named table row')
    class Ranker:
        async def rank(self, *args):
            raise AssertionError('An exact structured source does not need another ranking call')
    record = reviewed_table()
    writer = Writer([block(item_id='table-lookup', text='교육비는 3,300천원입니다.', quantities=[
        Quantity(label='교육비', value='3,300', unit='천원', evidence_ref=ref())])])
    context = InternalQuery.model_validate(query_payload('교육비 얼마인가요?'))
    answer = asyncio.run(CounselAgent(writer, FixtureRetriever([record]), reranker=Ranker()).run(context))
    assert writer.final_calls == writer.verify_calls == 1
    assert answer.result.status == 'answered'
    assert lookup_sources('아르바이트생 최저시급 얼마인가요?', {record.metadata.chunk_id: record}) == []


def test_mixed_items_keep_literal_cells_and_prose_in_separate_scopes():
    payload = writer_payload(reviewed_table())
    payload['assessment']['items'].append(item('other-item').to_payload())
    _, schema, choices = prepare_table_writer(payload, CounselDraft.model_json_schema())
    assert choices and 'table_choice_ids' in schema['properties']
    assert schema['$defs']['AnswerBlock']['properties']['item_id']['enum'] == ['other-item']
    prose = block(item_id='other-item', text='별도의 문서에서 확인한 설명입니다.').model_dump()
    result = resolve_table_writer({'blocks': [prose], 'category': 'franchise', 'table_choice_ids': [0]}, choices)
    assert result['blocks'][0] == prose
    assert result['blocks'][1]['item_id'] == 'item-1' and '3,300' in result['blocks'][1]['text']
    with pytest.raises(ServiceError):
        resolve_table_writer({'blocks': [block().model_dump()], 'category': 'franchise', 'table_choice_ids': [0]}, choices)


def test_interpretive_counsel_keeps_the_existing_prose_and_quantity_contract():
    payload = writer_payload(reviewed_table())
    payload['question_plan']['intent'] = 'inclusion'
    _, schema, choices = prepare_table_writer(payload, CounselDraft.model_json_schema())
    assert choices == [] and schema['properties']['blocks']['maxItems'] == 12


def test_numeric_prose_cannot_duplicate_or_rewrite_a_selected_table_value():
    import jsonschema
    _, schema, choices = prepare_table_writer(writer_payload(reviewed_table()), CounselDraft.model_json_schema())
    raw = {'blocks': [block(text='교육비는 3,300원입니다.').model_dump()],
           'category': 'franchise', 'table_choice_ids': [0]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(raw, schema)
    with pytest.raises(ServiceError):
        resolve_table_writer(raw, choices)


def test_null_is_not_turned_into_a_zero_or_a_selectable_fact():
    item = reviewed_table()
    item.table['columns'].append('별도 비용')
    item.table['rows'][0].append(None)
    _, _, choices = prepare_table_writer(writer_payload(item), CounselDraft.model_json_schema())
    assert len(choices) == 2 and all(c['value'] is not None for c in choices)


def test_general_draft_can_mix_server_restored_cells_with_an_independent_explanation():
    payload = writer_payload(reviewed_table())
    payload.pop('assessment')
    _, schema, choices = prepare_draft_tables(payload, ProposedAnswer.model_json_schema())
    assert 'table_selections' not in schema['properties']
    coverage = [DraftItem(item_id=i, requirement=i, answer_scope='document_rule', disposition='answer',
                         missing_information=None, lookup=None).model_dump() for i in ('cost', 'policy')]
    parsed = {'items': coverage, 'blocks': [block(item_id='policy', text='문서의 독립적인 기준 설명입니다.').model_dump()],
              'category': 'franchise', 'follow_up_questions': [],
              'table_selections': [{'item_id': 'cost', 'choice_id': 0}, {'item_id': 'cost', 'choice_id': 1}]}
    result = ProposedAnswer.model_validate(resolve_draft_tables(parsed, choices))
    assert len(result.blocks) == 2 and '3,300' in result.blocks[1].text
    assert result.item_sources(result.items[0]) == [ref()]
    assert result.blocks[0].item_id == 'policy' and result.blocks[1].item_id == 'cost'
    assert 'evidence_refs' not in parsed['items'][0]


@pytest.mark.parametrize('selection', [{'item_id': 'unknown', 'choice_id': 0},
                                     {'item_id': 'cost', 'choice_id': True},
                                     {'item_id': 'cost', 'choice_id': 999}])
def test_general_draft_cannot_forge_a_source_choice_or_item_owner(selection):
    _, _, choices = prepare_draft_tables(writer_payload(reviewed_table()), ProposedAnswer.model_json_schema())
    parsed = {'items': [{'item_id': 'cost', 'disposition': 'answer', 'evidence_refs': []}],
              'blocks': [], 'category': 'franchise', 'table_selections': [selection]}
    with pytest.raises(ServiceError):
        resolve_draft_tables(parsed, choices)


def test_langchain_writer_selects_only_ids_and_server_restores_the_actual_cells():
    async def run():
        calls = []
        def respond(request):
            payload = json.loads(request.content)
            calls.append(payload)
            schema = payload['response_format']['json_schema']['schema']
            assert schema['properties']['table_choice_ids']['items']['enum'] == [0, 1]
            assert schema['properties']['blocks']['maxItems'] == 0
            content = {'blocks': [], 'category': 'franchise', 'table_choice_ids': [0, 1]}
            return httpx.Response(200, json={'id': 'offline-table', 'object': 'chat.completion', 'created': 1,
                'model': 'offline-model', 'choices': [{'index': 0, 'finish_reason': 'stop',
                    'message': {'role': 'assistant', 'content': json.dumps(content)}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            chat = ChatOpenAI(model='offline-model', api_key='offline-test-only',
                              http_async_client=client, max_retries=0)
            result, _ = await LangChainModel(chat, 'offline-model').finalize([
                HumanMessage(content=json.dumps(writer_payload(reviewed_table())))])
            assert len(calls) == 1 and len(result.blocks) == 1
            assert '3,300' in result.blocks[0].text and '3일 이내' in result.blocks[0].text
    asyncio.run(run())


def test_literal_fraction_in_a_reviewed_cell_is_not_two_independent_amounts():
    record = source('분담 비율\n- 비용: 비율 100분의 20')
    record = replace(record, quality_flags=('table_reviewed',), table={
        'schema': 'reviewed-cells-v1', 'reviewed': True, 'title': '분담 비율', 'unit': '',
        'columns': ['구분', '비율'], 'rows': [['비용', '100분의 20']]})
    _, _, choices = prepare_table_writer(writer_payload(record), CounselDraft.model_json_schema())
    draft = CounselDraft.model_validate(resolve_table_writer(
        {'blocks': [], 'category': 'franchise', 'table_choice_ids': [0]}, choices))
    assert '100분의 20' in draft.blocks[0].text
    assert [(q.value, q.unit) for q in draft.blocks[0].quantities] == [('20', '%')]
    assert block_failures(draft.blocks, assessment(), {record.metadata.chunk_id: record}) == []


@pytest.mark.parametrize('mode', ['exact', 'draft'])
@pytest.mark.parametrize('selected', [[2], [0, 1]])
def test_total_selection_restores_same_column_rows_and_literal_checks_are_exact(mode, selected):
    from app.generation.table_evidence import literal_claim_ids
    e = source('비용 기준\n(단위: 천원, 부가세 포함)\n- 등록비: 금액 해당없음\n- 교육비: 금액 3,300\n- 합계: 금액 3,300')
    e = replace(e, quality_flags=('table_reviewed',), table={'schema': 'reviewed-cells-v1',
        'reviewed': True, 'title': '비용 기준', 'unit': '(단위: 천원, 부가세 포함)',
        'columns': ['구분','금액'], 'rows': [['등록비','해당없음'],['교육비','3,300'],['합계','3,300']]})
    if mode == 'exact':
        payload=writer_payload(e)
        payload['assessment']['items'][0]['requirement']='비용 합계'
        _, _, choices = prepare_table_writer(payload, CounselDraft.model_json_schema())
        parsed = resolve_table_writer({'blocks': [], 'category': 'franchise', 'table_choice_ids': selected}, choices)
    else:
        _, _, choices = prepare_draft_tables({'evidence': [evidence_payload(e)]}, ProposedAnswer.model_json_schema())
        parsed = resolve_draft_tables({'items': [DraftItem(item_id='item-1', requirement='비용 합계',
            answer_scope='document_rule', disposition='answer', missing_information=None).model_dump()],
            'blocks': [], 'category': 'franchise', 'table_selections': [{'item_id': 'item-1','choice_id': n} for n in selected]}, choices)
        parsed.pop('items')
    result = CounselDraft.model_validate(parsed)
    assert len(result.blocks) == 3
    assert all(b.evidence_refs == [ref()] for b in result.blocks)
    assert literal_claim_ids(result.blocks, {e.metadata.chunk_id:e}) == [0,1,2]
    changed = result.blocks[1].model_copy(update={'text': result.blocks[1].text.replace('3,300','9,999')})
    assert literal_claim_ids([changed], {e.metadata.chunk_id:e}) == []
    assert any('등록비 — 금액: 해당없음' in b.text for b in result.blocks)


@pytest.mark.parametrize('intent,expected', [('unknown', ['prose', 'table']), ('lookup', ['prose', 'table'])])
def test_production_schema_never_offers_rule_cards_and_keeps_field_lookup(intent, expected):
    e = reviewed_table()
    payload = {'question': '교육비 조건을 확인해 줘', 'question_plan': {'intent': intent},
               'evidence': [evidence_payload(e)]}
    model = LangChainModel.__new__(LangChainModel)
    async def capture(schema, prefix, sent):
        variants = schema['$defs']['DraftItem']['properties']['route']['anyOf']
        assert [v['properties']['kind']['enum'][0] for v in variants] == expected
        assert 'rule_choices' not in sent
        assert 'table_choices' in sent  # Source field lookup is item-owned, not gated by coarse intent.
        p = proposal('교육비 조건은 인용한 원문에 명시되어 있습니다.').model_dump()
        p['items'][0]['route'] = {'kind': 'prose'}
        p['blocks'][0]['evidence_refs'] = [0]
        jsonschema.validate(p, schema)
        return p, AIMessage(content='')
    model._structured = capture
    result, _ = asyncio.run(model.draft([HumanMessage(content=json.dumps(payload))]))
    assert result.blocks[0].text == '교육비 조건은 인용한 원문에 명시되어 있습니다.'
    assert result.blocks[0].evidence_refs[0].chunk_id == e.metadata.chunk_id



def test_normal_counsel_keeps_its_direct_answer_instead_of_replacing_it_with_registered_criteria():
    e, ctx = inspection_fixture()
    direct = '사전 승인을 받은 자격 있는 담당자만 점검하며, 휴직자는 제외합니다.'
    driver = Driver([proposal(direct)])
    result = asyncio.run(CounselAgent(driver, FixtureRetriever([e])).run(ctx)).result
    assert driver.calls == ['draft', 'verify']
    assert 'rule_choices' not in driver.writes[0]
    assert 'pending_rule_items' not in driver.reviews[0]
    assert direct in result.answer and result.status == 'answered'
    assert driver.reviews[0]['original_question'] == ctx.question
    assert driver.reviews[0]['question'] == ctx.question



def test_removed_rule_route_is_not_accepted_as_a_lookup():
    from app.generation.table_evidence import resolve_item_routes
    parsed = {'items': [{'item_id': 'old', 'route': {'kind': 'rule', 'choice_ids': ['rule-0']}}], 'blocks': []}
    with pytest.raises(ServiceError):
        resolve_item_routes(parsed, [])



@pytest.mark.parametrize('approved', [True, False])
def test_missing_definition_uses_existing_read_and_sentence_review_without_rule_cards(approved):
    e = source('제9조 ① 대통령령으로 정하는 담당자는 사전 승인을 받아야 한다.')
    e = replace(e, metadata=e.metadata.model_copy(update={'title': '테스트법', 'article': '제9조', 'section_key': 'art-9'}))
    d = source('제17조 법 제9조에서 “대통령령으로 정하는 담당자”란 지정 업무 담당자를 말한다.', 'definition')
    d = replace(d, metadata=d.metadata.model_copy(update={'title': '테스트법 시행령', 'article': '제17조', 'section_key': 'art-17'}))
    class References(FixtureRetriever):
        async def read_sections(self, ids, context):
            self.section_reads.append(ids)
            return EvidenceBatch((e, d))
    second = proposal('지정 업무 담당자는 사전 승인을 받아야 합니다.')
    second = second.model_copy(update={'blocks': [second.blocks[0].model_copy(update={
        'evidence_refs': [Quote(chunk_id=e.metadata.chunk_id, unit_id='unit-1'),
                          Quote(chunk_id='definition', unit_id='unit-0')]})]})
    def review(v, payload, attempt):
        if attempt == 1:
            return v.model_copy(update={'question_answered': False, 'recovery': 'read_sections',
                'section_ids': [e.metadata.chunk_id], 'missing_item_ids': ['rule']})
        if not approved:
            return v.model_copy(update={'supported': False, 'question_answered': False,
                'claims': [c.model_copy(update={'supported': False}) for c in v.claims],
                'item_checks': [c.model_copy(update={'complete': False}) for c in v.item_checks]})
        return v
    driver = Driver([proposal(disposition='missing_evidence'), second], review=review)
    retriever = References([e])
    _, ctx = inspection_fixture()
    result = asyncio.run(CounselAgent(driver, retriever).run(ctx)).result
    assert driver.calls == ['draft', 'verify', 'draft', 'verify']
    assert retriever.section_reads == [(e.metadata.chunk_id,)]
    assert all('rule_choices' not in p for p in driver.writes)
    assert result.status == ('answered' if approved else 'needs_review')
    assert {s.chunk_id for s in result.sources} == ({e.metadata.chunk_id, 'definition'} if approved else set())

