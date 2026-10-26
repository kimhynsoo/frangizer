import asyncio
import hashlib
import json
import re
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import jwt
from langchain_core.messages import AIMessage

from app.auth import AuthSettings
from app.generation.model import Draft, CounselDraft, ProposedAnswer, DraftItem
from app.generation.review import Assessment, AnswerItem, Grounding, AnswerBlock, Quantity, Verification, ClaimCheck, ItemCheck, Quote, RecipeLookup
from app.generation.question import QuestionPlan
from app.retrieval.base import Evidence, EvidenceBatch, is_allowed
from app.schemas import IndexedChunkMetadata, InternalQuery

AUTH = AuthSettings("offline-service-token", "offline-test-signing-key-at-least-32-bytes", "frangizer-test", "frangizer-rag")


def query_payload(question="테스트용 자료에서 확인 항목을 찾아줘"):
    return {
        "question_id": "q-test-001", "attempt_id": "attempt-test-001", "conversation_id": "conv-test-001",
        "question": question,
        "principal": {"user_id": "test-user", "role": "owner", "brand_id": "brand-test", "store_id": "store-test", "permissions": ["rag:ask"]},
        "as_of": "2026-10-23T10:00:00+09:00", "basis": "current", "scope_revision": "scope-test-001",
        "allowed_versions": [{"document_id": "fixture-document", "version_id": "fixture-v1", "index_revision": "fixture-index-v1"}],
        "history": [], "deadline_ms": 10000,
    }


def user_token(auth=AUTH, **changes):
    now = datetime.now(timezone.utc)
    claims = {"sub": "test-user", "role": "owner", "brand_id": "brand-test", "store_id": "store-test", "permissions": ["rag:ask"],
              "iss": auth.issuer, "aud": auth.audience, "iat": now, "exp": now + timedelta(hours=1)}
    claims.update(changes)
    return jwt.encode(claims, auth.jwt_secret, algorithm=auth.algorithm)


def headers(auth=AUTH, **changes):
    return {"X-Internal-Token": auth.internal_token, "Authorization": "Bearer " + user_token(auth, **changes)}


def fixture_evidence(chunk_id="fixture-chunk-1", **changes):
    text = "[테스트 전용 합성 데이터] 항목 A를 확인합니다. 실제 매장 정책이나 법령이 아닙니다."
    data = {
        "chunk_id": chunk_id, "brand_id": "brand-test", "document_id": "fixture-document", "version_id": "fixture-v1",
        "index_revision": "fixture-index-v1", "title": "테스트 전용 합성 검색 결과", "version": "1.0",
        "visibility": "owner", "material_type": "synthetic", "is_demo": True, "page": None,
        "article": None, "section_key": "fixture-section-1", "effective_from": "2026-10-01T00:00:00+09:00",
        "effective_until": None, "original_source": {"title": "테스트 fixture", "url": None, "edition": "1.0", "publisher": "frangizer tests", "acquired_on": "2026-10-23", "note": "검색 품질 평가에 사용하지 않음"},
        "embedding_model": "fixture-no-embeddings", "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }
    truncated = changes.pop("truncated", False)
    data.update(changes)
    return Evidence(text, IndexedChunkMetadata.model_validate(data), truncated)


class FixtureRetriever:
    ready = True

    def __init__(self, items=None):
        self.items = (fixture_evidence(),) if items is None else tuple(items)
        self.searches = []
        self.section_reads = []

    async def search(self, query, context):
        self.searches.append(query)
        return EvidenceBatch(tuple(i for i in self.items if is_allowed(i, context)), embedding_model="fixture-no-embeddings")

    async def read_sections(self, chunk_ids, context):
        self.section_reads.append(chunk_ids)
        sections = {i.metadata.section_key for i in self.items if i.metadata.chunk_id in chunk_ids}
        return EvidenceBatch(tuple(i for i in self.items if i.metadata.section_key in sections and is_allowed(i, context)))


def draft(**changes):
    data = {"answer": "테스트용 항목 A를 확인하세요. 실제 상담 답변이 아닙니다.", "steps": ["테스트 항목 A 확인"],
            "status": "answered", "used_chunk_ids": ["fixture-chunk-1"], "follow_up_questions": [], "handoff_reason": None, "category": "consumer"}
    data.update(changes)
    if 'blocks' not in data:
        refs = [Quote(chunk_id=i, unit_id='unit-0') for i in data['used_chunk_ids']]
        texts = [(text.strip(), 'answer') for text in re.split(r'(?<=[.!?。])\s+', data['answer']) if text.strip()]
        texts += [(text, 'step') for text in data['steps']]
        data['blocks'] = [AnswerBlock(block_id=i, item_id='item-1', text=text, placement=place,
             evidence_refs=refs, quantities=[Quantity(label='교육비', value=n, unit='천원', evidence_ref=refs[0])
                 for n in re.findall(r'\d+(?:,\d{3})*(?:\.\d+)?', text)] if refs else [])
             for i, (text, place) in enumerate(texts)] if refs else []
        data['cited_units'] = refs
    return Draft.model_validate(data)


def tool_call(name="search_documents", args=None, call_id="call-test-1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args or {"query": "테스트 확인 항목"}, "id": call_id, "type": "tool_call"}])


class ScriptedModel:
    """Deterministic model substitute; no question-understanding claim is made."""

    name = "scripted-test-model"

    def __init__(self, decisions=None, final=None, *, assessments=None, verifications=None, plans=None):
        self.decisions = list(decisions if decisions is not None else [tool_call(), AIMessage(content="최종 작성")])
        self.final = final or draft()
        self.decide_calls = self.final_calls = 0
        self.seen_messages = []
        self.assessments = list(assessments or [])
        self.verifications = list(verifications or [])
        self.assess_calls = self.verify_calls = 0
        self.plans = list(plans or [])
        self.plan_calls = 0

    async def plan(self, messages):
        self.plan_calls += 1
        self.seen_messages = list(messages)
        if self.plans:
            result = self.plans[min(self.plan_calls - 1, len(self.plans) - 1)]
        else:
            question = json.loads(messages[-1].content)['question']
            result = QuestionPlan(intent='unknown', resolved_question=question, queries=[question],
                                  focus=None, focus_turn=None, clarification=False)
        return deepcopy(result), AIMessage(content='')

    async def assess(self, messages):
        self.seen_messages = list(messages)
        self.assess_calls += 1
        if self.assessments:
            result = self.assessments[min(self.assess_calls - 1, len(self.assessments) - 1)]
        else:
            payload = json.loads(messages[-1].content)
            ids = [e['chunk_id'] for e in payload['candidates'] if e['usable_as_evidence']]
            action = 'sufficient' if ids else 'insufficient'
            if self.final.status == 'handoff':
                action = 'handoff'
            elif self.final.status == 'needs_review' and self.final.follow_up_questions:
                action = 'clarify'
            result = assessment(action, ids=ids, questions=self.final.follow_up_questions)
        return deepcopy(result), AIMessage(content='')

    async def verify(self, messages):
        self.seen_messages = list(messages)
        self.verify_calls += 1
        result = (self.verifications[min(self.verify_calls - 1, len(self.verifications) - 1)]
                  if self.verifications else verified_fixture(messages))
        if callable(result):
            result = result(messages)
        return deepcopy(result), AIMessage(content='')

    async def decide(self, messages):
        self.seen_messages = list(messages)
        self.decide_calls += 1
        return deepcopy(self.decisions[min(self.decide_calls - 1, len(self.decisions) - 1)])

    async def finalize(self, messages):
        self.seen_messages = list(messages)
        self.final_calls += 1
        return CounselDraft(blocks=self.final.blocks, category=self.final.category), AIMessage(content="", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})

    async def draft(self, messages):
        """Adapt declarative coverage/block fixtures into one writer response.

        These helpers are offline data builders, not production approval calls.
        test_draft_flow independently forbids any actual assess() invocation.
        """
        from langchain_core.messages import HumanMessage
        payload = json.loads(messages[-1].content)
        payload['candidates'] = payload['evidence']
        payload['previous_failure'] = payload.get('correction')
        a, _ = await self.assess([HumanMessage(content=json.dumps(payload))])
        locked = set(payload.get('verified_item_ids', []))
        payload['assessment'] = {'items': [i.to_payload() for i in a.items if i.item_id not in locked]}
        generated, raw = await self.finalize([*messages[:-1], HumanMessage(content=json.dumps(payload))])
        items = [DraftItem(item_id=i.item_id, requirement=i.requirement, answer_scope=i.answer_scope,
            disposition='handoff' if a.action == 'handoff' else 'answer' if i.state == 'supported'
                else 'missing_user_context' if i.state == 'missing_user_context' else 'missing_evidence',
            missing_information=i.missing_information,
            lookup=i.lookup.model_copy(update={'source_refs': i.evidence_refs}) if i.lookup else None)
                 for i in a.items if i.item_id not in locked]
        # A rewrite may only contain locked items in a synthetic legacy fixture.
        # Keep its coverage declarations, while the server ignores their blocks.
        if not items:
            items = [DraftItem(item_id=i.item_id, requirement=i.requirement, answer_scope=i.answer_scope,
                disposition='answer', missing_information=None,
                lookup=i.lookup.model_copy(update={'source_refs': i.evidence_refs}) if i.lookup else None) for i in a.items]
        allowed = {i.item_id for i in items if i.disposition == 'answer'}
        blocks = [b for b in generated.blocks if b.item_id in allowed]
        return ProposedAnswer(items=items, blocks=blocks, category=generated.category,
                              follow_up_questions=a.follow_up_questions), raw


def fixture_grounding(state='supported'):
    return Grounding(evidence_status='complete' if state in ('supported', 'missing_user_context') else
                     ('conflicting' if state == 'conflict' else 'missing'),
                     applicability='needs_user_context' if state == 'missing_user_context' else 'established',
                     source_scope='합성 테스트 자료의 적용 범위', question_scope='합성 테스트 질문의 대상')


def assessment(action='sufficient', *, ids=None, query=None, sections=None, questions=None, reason='offline fixture', items=None):
    ids = ids if ids is not None else (['fixture-chunk-1'] if action == 'sufficient' else [])
    state = 'supported' if ids else ('missing_user_context' if action == 'clarify' else 'missing_evidence')
    return Assessment(action=action, items=items or [AnswerItem(item_id='item-1', requirement='테스트 항목 A 확인',
                grounding=fixture_grounding(state), evidence_refs=[Quote(chunk_id=i, unit_id='unit-0') for i in ids], conditions=[],
                missing_information=None if ids else '테스트 항목의 근거 또는 적용 조건')],
                reason=reason, query=query, section_ids=sections or [], follow_up_questions=(questions or [])[:2],
                resolved_question='테스트용 자료에서 확인 항목을 찾아줘')


def verified_fixture(messages):
    payload = json.loads(messages[-1].content)
    claims = [ClaimCheck(claim_id=claim['id'], supported=True, quotes=[Quote.model_validate(r) for r in claim['evidence_refs']], applicable=True, conditions_preserved=True, source_subject='테스트 항목 A', answer_subject='테스트 항목 A', scope_relation='same') for claim in payload['claims_to_check']]
    covered = {c['item_id'] for c in payload['claims_to_check']}
    return Verification(supported=True, reason='offline fixture', unsupported_claims=[],
                        context_consistent=True, question_answered=True, claims=claims,
                        item_checks=[ItemCheck(item_id=i['item_id'], complete=i['state']=='supported' and i['item_id'] in covered)
                                     for i in payload['items']],
                        recovery='none', missing_item_ids=[], query=None, section_ids=[])


def recovery_review(action, *, query=None, sections=()):
    def review(messages):
        payload = json.loads(messages[-1].content)
        return verified_fixture(messages).model_copy(update={'question_answered': False, 'recovery': action,
            'query': query, 'section_ids': list(sections),
            'missing_item_ids': [i['item_id'] for i in payload['items']]})
    return review


# Shared generation fixtures: test modules contain scenarios, not one another's builders.

def source(text, cid='fixture-chunk-1'):
    original = fixture_evidence(cid)
    return replace(original, text=text, metadata=original.metadata.model_copy(update={
        'content_sha256': hashlib.sha256(text.encode()).hexdigest()}))


def ref(cid='fixture-chunk-1', unit='unit-0'):
    return Quote(chunk_id=cid, unit_id=unit)


def item(i='item-1', *, state='supported', refs=None, conditions=()):
    return AnswerItem(item_id=i, requirement=f'{i}의 적용 기준 확인', grounding=fixture_grounding(state),
                      evidence_refs=refs if refs is not None else ([ref()] if state=='supported' else []),
                      conditions=list(conditions), missing_information=None if state=='supported' else '적용 기준의 근거')


def lookup_item():
    return item().model_copy(update={'lookup': RecipeLookup(operation='quantity', menu_id='vanilla_latte',
        temperature='ICED', ingredients=['우유'], option=None, servings=1, source_refs=[ref()])})


def block(i=0, *, item_id='item-1', text='확인된 항목의 기준을 안내합니다.', refs=None, quantities=()):
    return AnswerBlock(block_id=i, item_id=item_id, text=text, placement='answer',
                       evidence_refs=refs if refs is not None else [ref()], quantities=list(quantities))


class Blocks(ScriptedModel):
    def __init__(self, blocks, **kwargs):
        super().__init__(**kwargs)
        self.blocks = blocks

    async def finalize(self, messages):
        self.final_calls += 1
        return CounselDraft(blocks=self.blocks, category='franchise'), AIMessage(content='')


def run_coverage(model, sources=None, *, question='등록된 자료의 기준을 확인해 줘', max_calls=3):
    from app.generation.agent import CounselAgent
    context = InternalQuery.model_validate({**query_payload(question), 'deadline_ms': 30000})
    return asyncio.run(CounselAgent(model, sources or FixtureRetriever(), max_model_calls=max_calls).run(context)).result


def proposal(text='문서에 명시된 조건에서 비용 일부를 분담합니다.', *, disposition='answer'):
    item = DraftItem(item_id='rule', requirement='문서의 부담 기준', answer_scope='document_rule',
        disposition=disposition, missing_information=None if disposition == 'answer' else '참조 기준',
        lookup=None)
    return ProposedAnswer(items=[item], blocks=[block(item_id='rule', text=text)] if disposition == 'answer' else [],
                          category='franchise', follow_up_questions=[])


class Driver:
    name = 'offline-draft-test'

    def __init__(self, drafts=None, review=None):
        self.drafts = drafts or [proposal()]
        self.calls, self.writes, self.reviews = [], [], []
        self.review = review

    async def assess(self, *args):
        raise AssertionError('No pre-writing approval call is allowed')

    async def draft(self, messages):
        self.calls.append('draft')
        self.writes.append(json.loads(messages[-1].content))
        return self.drafts[min(len(self.writes)-1, len(self.drafts)-1)], AIMessage(content='')

    async def verify(self, messages):
        self.calls.append('verify')
        self.reviews.append(json.loads(messages[-1].content))
        v = verified_fixture(messages)
        if self.review:
            v = self.review(v, self.reviews[-1], len(self.reviews))
        return v, AIMessage(content='')


def run_draft_flow(driver, retriever=None):
    from app.generation.agent import CounselAgent
    ctx = InternalQuery.model_validate({**query_payload('문서의 비용 부담 기준을 설명해 줘'), 'deadline_ms': 30000})
    return asyncio.run(CounselAgent(driver, retriever or FixtureRetriever(), max_model_calls=4).run(ctx))


def reviewed_table():
    title, unit = '비용 기준', '(단위: 천원, 부가세 포함)'
    item = source(title + '\n' + unit + '\n- 교육비: 금액 3,300 · 지급기한 계약체결일로부터 3일 이내')
    return replace(item, quality_flags=('table_reviewed',), table={
        'schema': 'reviewed-cells-v1', 'reviewed': True, 'title': title, 'unit': unit,
        'columns': ['구분', '금액', '지급기한'], 'rows': [['교육비', '3,300', '계약체결일로부터 3일 이내']]})


def writer_payload(item):
    from app.generation.agent import evidence_payload
    evidence = evidence_payload(item)
    for unit in evidence['source_units']:
        unit['approved_item_ids'] = ['item-1']
    return {'evidence': [evidence], 'assessment': {'items': assessment().to_payload()['items']},
            'question_plan': {'intent': 'lookup'}}


def inspection_fixture():
    e = source('① 자격을 갖춘 담당자는 사전 승인된 경우에만 점검한다. 다만 휴직자는 제외한다.')
    ctx = InternalQuery.model_validate({**query_payload('문서의 점검 기준을 설명해 줘'), 'deadline_ms': 30000})
    return e, ctx
