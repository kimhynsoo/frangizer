import os
import json
from typing import Annotated, Literal, Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import Field

from ..errors import upstream_error
from ..schemas import Category, ContractModel, Id, Status
from .review import (
    Assessment, AnswerItem, Grounding, Verification, AnswerBlock, ClaimCheck,
    Quote, RecipeLookup, VERIFY_PROMPT,
)
from .question import QuestionPlan, QuestionChoice, PLAN_PROMPT


class Draft(ContractModel):
    """Internal model output. URLs and source metadata are server-owned."""

    answer: Annotated[str, Field(min_length=1, max_length=6000)]
    steps: Annotated[list[Annotated[str, Field(min_length=1, max_length=300)]], Field(max_length=12)]
    status: Status
    used_chunk_ids: Annotated[list[Id], Field(max_length=20)]
    follow_up_questions: Annotated[list[Annotated[str, Field(min_length=1, max_length=200)]], Field(max_length=3)]
    handoff_reason: Annotated[str, Field(min_length=1, max_length=1000)] | None
    category: Category
    blocks: Annotated[list[AnswerBlock], Field(max_length=12)] = Field(default_factory=list)
    cited_units: list[Quote] = Field(default_factory=list)


class CounselDraft(ContractModel):
    """The writer supplies only factual blocks; the server owns coverage/status."""
    blocks: Annotated[list[AnswerBlock], Field(max_length=12)]
    category: Category


class DraftItem(ContractModel):
    """Coverage proposed by the writer, never permission to publish a claim."""
    item_id: Id
    requirement: Annotated[str, Field(min_length=1, max_length=200)]
    answer_scope: Literal['document_rule', 'case_application']
    disposition: Literal['answer', 'missing_evidence', 'missing_user_context', 'handoff']
    missing_information: Annotated[str, Field(min_length=1, max_length=200)] | None
    lookup: RecipeLookup | None = None


class ProposedAnswer(CounselDraft):
    items: Annotated[list[DraftItem], Field(min_length=1, max_length=6)]
    follow_up_questions: Annotated[list[Annotated[str, Field(min_length=1, max_length=200)]], Field(max_length=2)]

    def item_sources(self, item):
        """Group actual sentence citations; this is provenance, not approval."""
        refs = [r for b in self.blocks if b.item_id == item.item_id for r in b.evidence_refs]
        if item.lookup:
            refs.extend(item.lookup.source_refs)
        return list({(r.chunk_id, r.unit_id): r for r in refs}.values())

    def to_assessment(self, previous, *, locked_ids, resolved_question):
        """Derive unverified coverage from one draft; this does not call a model."""
        known = {i.item_id: i for i in previous.items} if previous else {}
        if len({i.item_id for i in self.items}) != len(self.items):
            raise upstream_error()
        for item in self.items:
            if item.item_id in locked_ids:
                continue
            refs = self.item_sources(item)
            if len(refs) > 32:
                raise upstream_error()
            needs_context = item.disposition == 'missing_user_context'
            known[item.item_id] = AnswerItem(
                item_id=item.item_id, requirement=item.requirement, answer_scope=item.answer_scope,
                lookup=item.lookup, evidence_refs=refs, conditions=[], missing_information=item.missing_information,
                grounding=Grounding(
                    evidence_status='complete' if item.disposition in ('answer', 'missing_user_context') else 'missing',
                    applicability='needs_user_context' if needs_context else 'established',
                    source_scope=('질문에 필요한 사용자 조건' if needs_context else
                                  '초안이 연결한 원문, 최종 검수 전' if refs else None),
                    question_scope=item.requirement),
            )
        if not 1 <= len(known) <= 6:
            raise upstream_error()
        items = list(known.values())
        action = ('handoff' if all(i.disposition == 'handoff' for i in self.items) else
                  'sufficient' if any(i.state == 'supported' for i in items) else
                  'clarify' if self.follow_up_questions and any(i.state == 'missing_user_context' for i in items) else
                  'insufficient')
        return Assessment(action=action, items=items, reason='작성된 항목과 초안, 최종 검수 전',
            query=None, section_ids=[], follow_up_questions=self.follow_up_questions,
            resolved_question=resolved_question)


class ModelDriver(Protocol):
    name: str

    async def finalize(self, messages: list[BaseMessage]) -> tuple[CounselDraft, AIMessage]: ...

    async def draft(self, messages: list[BaseMessage]) -> tuple[ProposedAnswer, AIMessage]: ...

    async def verify(self, messages: list[BaseMessage]) -> tuple[Verification, AIMessage]: ...

    async def plan(self, messages: list[BaseMessage]) -> tuple[QuestionPlan, AIMessage]: ...


class LangChainModel:
    """Works with a compatible BaseChatModel; no model state is shared across requests."""

    def __init__(self, model, name: str):
        self.name = name
        self.chat = model
        self.planner = model.with_structured_output(
            QuestionChoice.model_json_schema(), method='json_schema', strict=True, include_raw=True,
        )

    async def _structured(self, schema, prefix, payload):
        """One serialization and parsing boundary for draft, lookup and review."""
        writer = self.chat.with_structured_output(schema, method='json_schema', strict=True, include_raw=True)
        result = await writer.ainvoke([*prefix, HumanMessage(content=json.dumps(payload, ensure_ascii=False))])
        if result.get('parsing_error') or not isinstance(result.get('parsed'), dict):
            raise upstream_error()
        return result['parsed'], result['raw']

    @classmethod
    def from_env(cls):
        # Called only by an integration factory after a real retriever is available.
        from langchain_openai import ChatOpenAI

        name, key = os.environ.get("LLM_MODEL"), os.environ.get("LLM_API_KEY")
        if not name or not key:
            raise ValueError("LLM_MODEL and LLM_API_KEY are required")
        return cls(ChatOpenAI(model=name, api_key=key, max_retries=0), name)

    async def finalize(self, messages):
        from .table_evidence import prepare_table_writer, resolve_table_writer
        payload = json.loads(messages[-1].content)
        approved = [item['item_id'] for item in payload['assessment']['items'] if item['state'] == 'supported']
        if not approved:
            raise upstream_error()
        schema = CounselDraft.model_json_schema()
        schema['$defs']['AnswerBlock']['properties']['item_id']['enum'] = approved
        payload, schema, choices = prepare_table_writer(payload, schema)
        parsed, raw = await self._structured(schema, messages[:-1], payload)
        return CounselDraft.model_validate(resolve_table_writer(parsed, choices)), raw

    async def draft(self, messages):
        from .table_evidence import prepare_draft_tables, resolve_draft_tables, prepare_item_routes, resolve_item_routes
        from .review import prepare_draft_sources, resolve_draft_sources
        payload = json.loads(messages[-1].content)
        schema = ProposedAnswer.model_json_schema()
        # Source-owned field lookup stays available per item, including textual
        # deadlines/return terms. Registered rules are never answer routes.
        payload, schema, choices = prepare_draft_tables(payload, schema)
        payload, schema, sources = prepare_draft_sources(payload, schema)
        schema = prepare_item_routes(schema, choices)
        # Coverage and one source route are selected together for each item.
        order = ('items', 'blocks', 'category', 'follow_up_questions')
        schema['properties'] = {k: schema['properties'][k] for k in order if k in schema['properties']}
        parsed, raw = await self._structured(schema, messages[:-1], payload)
        parsed = resolve_item_routes(parsed, choices)
        parsed = resolve_draft_sources(parsed, sources)
        parsed = resolve_draft_tables(parsed, choices)
        return ProposedAnswer.model_validate(parsed), raw

    async def verify(self, messages):
        from .review import prepare_review, resolve_review
        payload = json.loads(messages[-1].content)
        literal_ids = set(payload.pop('literal_claim_ids', []))
        literals = [c for c in payload['claims_to_check'] if c['id'] in literal_ids]
        payload['claims_to_check'] = [c for c in payload['claims_to_check'] if c['id'] not in literal_ids]
        payload['expected_claim_ids'] = [c['id'] for c in payload['claims_to_check']]
        payload['code_verified_claims'] = literals
        payload, schema, choices = prepare_review(payload)
        parsed, raw = await self._structured(schema, [SystemMessage(content=VERIFY_PROMPT), *messages[:-1]], payload)
        verdict = resolve_review(parsed, choices)
        complete = {i.item_id for i in verdict.item_checks if i.complete}
        for claim in literals:
            verdict.claims.append(ClaimCheck(claim_id=claim['id'],
                supported=claim['item_id'] in complete, applicable=claim['item_id'] in complete,
                conditions_preserved=True, quotes=[Quote.model_validate(r) for r in claim['evidence_refs']],
                source_subject='코드로 원문과 대조한 표 항목', answer_subject='코드로 원문과 대조한 표 항목',
                scope_relation='same'))
        return verdict.model_copy(update={'supported': all(c.supported for c in verdict.claims)}), raw

    async def plan(self, messages):
        result = await self.planner.ainvoke([SystemMessage(content=PLAN_PROMPT), *messages])
        if result.get('parsing_error') or not isinstance(result.get('parsed'), dict):
            raise upstream_error()
        choice = QuestionChoice.model_validate(result['parsed'])
        payload = json.loads(messages[-1].content)
        candidates = {c['id']: c for c in payload.get('focus_candidates', [])}
        focus = candidates.get(choice.focus_candidate_id)
        plan = QuestionPlan(intent=choice.intent, resolved_question=choice.resolved_question,
                            queries=choice.queries, focus=focus['text'] if focus else None,
                            focus_turn=focus['turn'] if focus else None,
                            focus_source=focus.get('source', 'question') if focus else 'question',
                            clarification=choice.clarification or (choice.focus_candidate_id is not None and focus is None))
        return plan, result['raw']
