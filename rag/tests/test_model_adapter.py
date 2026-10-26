import asyncio
import json

import httpx
import pytest
from langchain_openai import ChatOpenAI

from app.generation.agent import CounselAgent
from app.generation.model import LangChainModel, CounselDraft, ProposedAnswer, DraftItem
from app.schemas import InternalQuery
from .support import FixtureRetriever, draft, query_payload, assessment, verified_fixture


@pytest.mark.parametrize("model_name", ["offline-model", "gpt-5.4-mini"])
def test_langchain_draft_and_verification_without_network(model_name):
    """Exercise real LangChain serialization/parsing against an in-memory HTTP transport."""
    async def run():
        calls = []

        def respond(request):
            payload = json.loads(request.content)
            calls.append(payload)
            fmt = payload['response_format']
            assert fmt['type'] == 'json_schema' and fmt['json_schema']['strict']
            properties = fmt['json_schema']['schema']['properties']
            assert 'action' not in properties  # No approval-model request.
            if 'item_checks' in properties:
                assert [m['role'] for m in payload['messages']] == ['system', 'user']
                from langchain_core.messages import HumanMessage
                review = json.loads(payload['messages'][-1]['content'])
                check_schema = fmt['json_schema']['schema']['$defs']['ClaimCheck']
                assert 'quotes' not in check_schema['properties']
                choices = {c['id']: c['evidence_choices'] for c in review['claims_to_check']}
                for c in review['claims_to_check']:
                    c['evidence_refs'] = [{k: v for k, v in r.items() if k != 'id'} for r in choices[c['id']]]
                parsed = verified_fixture([HumanMessage(content=json.dumps(review))]).model_dump()
                parsed['claims'] = [{'claim_id': c['claim_id'], 'verdict': 'supported',
                    'reason': '원문 일치', 'evidence_ids': [r['id'] for r in choices[c['claim_id']]]}
                    for c in parsed['claims']]
                parsed.pop('supported')
                parsed.pop('unsupported_claims')
                parsed['claims'] = {str(c.pop('claim_id')): c for c in parsed['claims']}
                parsed['item_checks'] = {c.pop('item_id'): c for c in parsed['item_checks']}
                content = json.dumps(parsed)
            else:
                writer_input = json.loads(payload['messages'][-1]['content'])
                assert 'assessment' not in writer_input and 'items' in properties
                # The provider rejects a description alongside a reference,
                # even though that combination is valid in general JSON Schema.
                quantity = fmt['json_schema']['schema']['$defs']['Quantity']['properties']
                assert set(quantity['evidence_ref']) == {'$ref'}
                assert fmt['json_schema']['schema']['$defs']['Quote'] == {'type': 'integer', 'enum': [0]}
                item = DraftItem(item_id='item-1', requirement='테스트 확인 항목', answer_scope='document_rule',
                    disposition='answer', missing_information=None, lookup=None)
                parsed = ProposedAnswer(items=[item], blocks=draft().blocks, category=draft().category,
                                        follow_up_questions=[]).model_dump()
                assert 'evidence_refs' not in fmt['json_schema']['schema']['$defs']['DraftItem']['properties']
                for item_row in parsed['items']:
                    item_row['route'] = {'kind': 'prose'}
                for row in parsed['blocks']:
                    row['evidence_refs'] = [0]
                    for q in row.get('quantities', []):
                        q['evidence_ref'] = 0
                        q['context_refs'] = []
                content = json.dumps(parsed)
            message = {'role': 'assistant', 'content': content}
            reason = 'stop'
            return httpx.Response(200, json={
                "id": "chatcmpl-offline", "object": "chat.completion", "created": 1,
                "model": model_name, "choices": [{"index": 0, "message": message, "finish_reason": reason}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            chat = ChatOpenAI(model=model_name, api_key="offline-test-only", http_async_client=client, max_retries=0)
            agent = CounselAgent(LangChainModel(chat, model_name), FixtureRetriever())
            result = await agent.run(InternalQuery.model_validate(query_payload()))
            assert result.result.status == "answered"
            assert result.usage.input_tokens == 24 and result.usage.output_tokens == 16
            assert len(calls) == 2
    asyncio.run(run())


@pytest.mark.parametrize('selected_id', [0, 99])
def test_question_planner_selects_server_provided_focus_id(selected_id):
    from langchain_core.messages import HumanMessage
    async def run():
        def respond(request):
            payload = json.loads(request.content)
            schema = payload['response_format']['json_schema']
            assert schema['strict'] and 'focus_candidate_id' in schema['schema']['properties']
            content = {'intent': 'inclusion', 'resolved_question': '계약이행보증금에 교육비가 포함되나요?',
                       'queries': ['계약이행보증금 교육비 포함'], 'focus_candidate_id': selected_id, 'clarification': False}
            return httpx.Response(200, json={'id': 'offline-plan', 'object': 'chat.completion', 'created': 1,
                'model': 'gpt-5.4-mini', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': json.dumps(content)},
                                                   'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            chat = ChatOpenAI(model='gpt-5.4-mini', api_key='offline-test-only', http_async_client=client, max_retries=0)
            model = LangChainModel(chat, 'gpt-5.4-mini')
            result, _ = await model.plan([HumanMessage(content=json.dumps({'focus_candidates': [
                {'id': 0, 'turn': 0, 'text': '계약이행보증금'}]}))])
            assert result.clarification == (selected_id == 99)
            assert result.focus == ('계약이행보증금' if selected_id == 0 else None)
    asyncio.run(run())


@pytest.mark.parametrize('relevant', [True, False])
def test_literal_facts_are_code_checked_but_question_coverage_still_controls_publication(relevant):
    from langchain_core.messages import HumanMessage
    from tests.support import reviewed_table, writer_payload
    from app.generation.agent import evidence_payload
    from app.generation.table_evidence import prepare_table_writer, resolve_table_writer
    e = reviewed_table()
    _, _, choices = prepare_table_writer(writer_payload(e), CounselDraft.model_json_schema())
    b = CounselDraft.model_validate(resolve_table_writer({'blocks': [], 'category':'franchise',
        'table_choice_ids':[0]}, choices)).blocks[0]
    payload = {'items':assessment().to_payload()['items'], 'question':'교육비 반환 조건은 무엇인가요?',
        'original_question':'그 금액은 전부 반환받나요?',
        'evidence':[evidence_payload(e)],'literal_claim_ids':[b.block_id],
        'expected_claim_ids':[b.block_id], 'claims_to_check':[{'id':b.block_id, 'text':b.text,
            'item_id':b.item_id,'evidence_refs':[r.model_dump() for r in b.evidence_refs]}]}
    def respond(request):
        sent=json.loads(request.content)
        data=json.loads(sent['messages'][-1]['content'])
        assert data['question'] == payload['question']
        assert data['original_question'] == payload['original_question']
        assert data['question'] != '\n'.join(i['requirement'] for i in payload['items'])
        assert data['claims_to_check']==[] and len(data['code_verified_claims'])==1
        assert sent['response_format']['json_schema']['schema']['properties']['claims']['properties']=={}
        parsed={'claims':{},'item_checks':{'item-1':{'complete':relevant}},
            'reason':'질문 범위 확인',
            'context_consistent':True,'question_answered':relevant,'recovery':'none',
            'missing_item_ids':[],'query':None,'section_ids':[]}
        return httpx.Response(200,json={'id':'literal-review','object':'chat.completion','created':1,
            'model':'offline-model','choices':[{'index':0,'message':{'role':'assistant',
            'content':json.dumps(parsed)},'finish_reason':'stop'}]})
    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model=LangChainModel(ChatOpenAI(model='offline-model',api_key='offline-only',http_async_client=client,max_retries=0),'offline-model')
            verdict,_=await model.verify([HumanMessage(content=json.dumps(payload))])
            return verdict
    v=asyncio.run(execute())
    assert v.claims[0].supported == relevant
    assert v.claims[0].applicable == relevant
    assert v.claims[0].conditions_preserved and v.claims[0].quotes==b.evidence_refs
