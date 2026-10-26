import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from ..errors import ServiceError, unavailable, upstream_error
from ..retrieval.base import Evidence, Retriever, validate_batch, context_related, reference_targets
from ..retrieval.hybrid import reciprocal_rank_fusion
from ..schemas import InternalAnswer, InternalQuery, Usage
from .answer import assemble_answer, compose, retain_verified, preserve_verified, limitations, deduplicate_verified
from .evidence_policy import table_blocked, bind_literal_quantities
from .model import Draft, CounselDraft, ProposedAnswer, ModelDriver
from .review import (Assessment, AnswerItem, Grounding, Quote, Verification, verification_failures, source_units,
                     assessment_failures, block_failures, repair_details)
from .question import (QuestionPlan, default_plan, needs_planning, validate_focus, focus_candidates,
                       resolve_aggregate_followup, repair_single_focus, COMPARISON)
from .table_evidence import table_rows, lookup_sources
from .table_evidence import literal_claim_ids

PROMPT = Path(__file__).resolve().parents[2] / 'prompts' / 'counsel.md'
log = logging.getLogger('uvicorn.error')


class AgentState(TypedDict):
    context: InternalQuery
    pending_tools: list[dict]
    evidence: dict[str, Evidence]
    candidates: dict[str, Evidence]
    accepted_ids: list[str]
    assessment: Assessment | None
    model_calls: int
    tool_calls: int
    search_calls: int
    candidate_count: int
    input_tokens: int
    output_tokens: int
    embedding_tokens: int
    embedding_model: str
    next_node: str
    draft: Draft | None
    end_at: float
    repairs: int
    correction: str
    verified: bool
    evidence_notes: list[str]
    plan: QuestionPlan | None
    fixed_clarification: bool
    fallback_draft: Draft | None
    verified_partial: Draft | None
    exact_lookup: bool
    stage_ms: dict[str, int]


def tokens(message):
    usage = message.usage_metadata or {}
    return {'input_tokens': usage.get('input_tokens', 0), 'output_tokens': usage.get('output_tokens', 0)}


def evidence_payload(item):
    m = item.metadata
    return {
        'chunk_id': m.chunk_id,
        'usable_as_evidence': not table_blocked(item) and not item.truncated,
        'truncated': item.truncated, 'title': m.title, 'page': m.page, 'article': m.article,
        'section_key': m.section_key, 'parent_section_key': item.parent_section_key,
        'part': item.section_part, 'version': m.version, 'material_type': m.material_type,
        'is_demo': m.is_demo, 'legal_temporal_unreviewed': item.legal_temporal_unreviewed,
        'quality_flags': list(item.quality_flags), 'effective_from': m.effective_from.isoformat(),
        'hierarchy': item.hierarchy,
        'references': [{'title': title, 'article': article} for title, article in reference_targets(item)],
        'source_units': source_units(item) if not table_blocked(item) and not item.truncated else [],
        'table_rows': table_rows(item),
        'effective_until': m.effective_until.isoformat() if m.effective_until else None,
    }


def claim_statements(draft):
    # Stable IDs from creation through review; never re-split prose/quotations.
    return [{'id': b.block_id, 'text': b.text, 'item_id': b.item_id,
             'evidence_refs': [r.model_dump() for r in b.evidence_refs],
             'quantities': [q.model_dump() for q in b.quantities]}
            for b in draft.blocks]


class CounselAgent:
    """Bounded draft, source verification and one shared recovery path."""
    def __init__(self, model: ModelDriver, retriever: Retriever, *, max_model_calls=6,
                 max_tool_calls=3, max_search_calls=3, reranker=None, review_mode=None, item_lookup=None):
        if not (2 <= max_model_calls <= 10 and 1 <= max_tool_calls <= 10
                and 1 <= max_search_calls <= min(3, max_tool_calls)):
            raise ValueError('invalid agent budget')
        self.model, self.retriever = model, retriever
        self.reranker = reranker
        self.item_lookup = item_lookup
        self.max_model_calls, self.max_tool_calls, self.max_search_calls = max_model_calls, max_tool_calls, max_search_calls
        self.prompt = PROMPT.read_text()
        # Legacy configuration is accepted, but every generated counsel answer is reviewed.
        mode = review_mode or os.environ.get('RAG_REVIEW_MODE', 'always')
        if mode not in ('adaptive', 'always'):
            raise ValueError('invalid review mode')
        if mode == 'adaptive':
            log.warning('RAG_REVIEW_MODE=adaptive is deprecated; counsel always uses semantic review')
        graph = StateGraph(AgentState)
        graph.add_node('prepare', self._prepare)
        graph.add_node('initial_search', self._initial_search)
        graph.add_node('tools', self._tools)
        graph.add_node('generate', self._generate)
        graph.add_node('verify', self._verify)
        graph.add_edge(START, 'prepare')
        graph.add_conditional_edges('prepare', lambda s: s['next_node'], {'initial_search': 'initial_search', 'generate': 'generate'})
        graph.add_edge('initial_search', 'generate')
        graph.add_edge('tools', 'generate')
        graph.add_conditional_edges('generate', lambda s: s['next_node'], {'generate': 'generate', 'verify': 'verify', 'tools': 'tools', 'done': END})
        graph.add_conditional_edges('verify', lambda s: s['next_node'], {'verify': 'verify', 'generate': 'generate', 'tools': 'tools', 'done': END})
        self.graph = graph.compile()

    def _remaining(self, s):
        return max(0.0, s['end_at'] - time.monotonic())

    def _diagnostic(self, s, stage, codes, *, failures=()):
        # Deliberately exclude question, source text, draft, and free-form model reasons.
        record = {'question_id': s['context'].question_id, 'attempt_id': s['context'].attempt_id,
                  'stage': stage, 'codes': codes, 'claim_failures': list(failures),
                  'accepted_ids': s['accepted_ids'], 'candidate_count': s['candidate_count'],
                  'model_calls': s['model_calls'], 'search_calls': s['search_calls'],
                  'repairs': s['repairs'], 'remaining_ms': int(self._remaining(s) * 1000),
                  'stage_ms': s['stage_ms'],
                  'items': [{'item_id': i.item_id, 'state': i.state,
                             'answer_scope': i.answer_scope,
                             'evidence_status': i.grounding.evidence_status, 'applicability': i.grounding.applicability,
                             'refs': [r.model_dump() for r in i.evidence_refs]}
                            for i in s['assessment'].items] if s['assessment'] else []}
        log.info('counsel_decision %s', json.dumps(record, ensure_ascii=True))

    def _reserve(self, *stages):
        # Fixed admission estimates: one slow request must not change later requests.
        seconds = {'plan': 1.5, 'generation': 2.0, 'verification': 3.0, 'retrieval': 2.0}
        return sum(seconds[stage] for stage in stages)

    async def _invoke(self, s, stage, operation, *, reserve=0):
        remaining = self._remaining(s) - reserve - .25
        if remaining <= .05:
            self._diagnostic(s, stage, ['STAGE_BUDGET_EXHAUSTED'])
            raise ServiceError(504, 'UPSTREAM_TIMEOUT', '상담 처리 제한 시간을 초과했습니다.', retryable=True)
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(operation(), timeout=remaining)
        except TimeoutError:
            self._diagnostic(s, stage, [stage.upper() + '_TIMEOUT'])
            raise ServiceError(504, 'UPSTREAM_TIMEOUT', '상담 처리 제한 시간을 초과했습니다.', retryable=True) from None
        elapsed = time.monotonic() - started
        s['stage_ms'][stage] = s['stage_ms'].get(stage, 0) + int(elapsed * 1000)
        return result

    def _usage(self, s, raw):
        usage = tokens(raw)
        return {'model_calls': s['model_calls'] + 1,
                'input_tokens': s['input_tokens'] + usage['input_tokens'],
                'output_tokens': s['output_tokens'] + usage['output_tokens']}

    def _context(self, s):
        c = s['context']
        return {'question': c.question,
                'history': [t.model_dump() for t in c.history] if s['plan'] is None else [],
                'user_question_chain': [t.question for t in c.history] + [c.question],
                'question_plan': s['plan'].model_dump() if s['plan'] else None,
                'service_domain': os.environ.get('RAG_DOMAIN', '카페 가맹점 운영·창업'),
                'as_of': c.as_of.isoformat(), 'basis': c.basis,
                'answer_requirements': {'document_first': True,
                                        'explain_components': bool(re.search(r'합계|합산|총액', c.question))}}

    def _safe_draft(self, s):
        return Draft(answer='확보한 문서만으로는 질문에 적용할 기준과 조건을 확인하지 못했습니다.', steps=[],
                     status='needs_review', used_chunk_ids=[], follow_up_questions=[],
                     handoff_reason='적용 조건과 근거 추가 확인 필요', category='other')

    async def _prepare(self, s):
        plan, updates = default_plan(s['context']), {}
        selection_failed = False
        if needs_planning(s['context']):
            candidates = focus_candidates(s['context'])
            payload = {**self._context(s), 'focus_candidates': candidates}
            resolved = resolve_aggregate_followup(s['context'], candidates)
            if resolved is not None:
                plan = resolved
            else:
                result, raw = await self._invoke(s, 'plan', lambda: self.model.plan(
                    [HumanMessage(content=json.dumps(payload, ensure_ascii=False))]),
                    reserve=self._reserve('generation', 'verification'))
                proposed = QuestionPlan.model_validate(result)
                plan = validate_focus(proposed, s['context'])
                selection_failed = plan.clarification and not proposed.clarification
                if selection_failed:
                    repaired = repair_single_focus(proposed, s['context'], candidates)
                    if repaired is not None:
                        plan = validate_focus(repaired, s['context'])
                    self._diagnostic(s, 'plan', ['FOCUS_SELECTION_REPAIRED' if not plan.clarification
                                               else 'FOCUS_SELECTION_INVALID'])
                updates = self._usage(s, raw)
        updates.update(plan=plan, next_node='initial_search')
        if plan.clarification:
            a = Assessment(action='clarify', items=[AnswerItem(item_id='focus', requirement='대화의 지시 대상',
                           grounding=Grounding(evidence_status='complete', applicability='needs_user_context',
                               source_scope='사용자 대화의 지시 대상', question_scope=s['context'].question[:300]),
                           evidence_refs=[], conditions=[], missing_information='대상 또는 금액')],
                           reason='대상 선택 검증 실패' if selection_failed else '지시 대상 불명확', query=None, section_ids=[],
                           follow_up_questions=['어떤 대상이나 금액을 말씀하시는지 알려 주세요.'],
                           resolved_question=s['context'].question)
            updates.update(assessment=a, fixed_clarification=True, next_node='generate')
        return updates

    async def _initial_search(self, s):
        # One scoped search is mandatory; a planner call is not needed to initiate it.
        calls = [{'name': 'search_documents', 'args': {'query': query}}
                 for query in s['plan'].queries[:self.max_search_calls]]
        updates = await self._tools({**s, 'pending_tools': calls})
        current = {**s, **updates}
        if s['plan'].intent == 'lookup' and lookup_sources(s['plan'].resolved_question, current['evidence']):
            return updates
        # One ranking call for the combined initial candidates. Corrections use
        # direct context reads or Hybrid search; they do not pay for ranking again.
        if (self.reranker is None or not updates['candidates']
                or current['model_calls'] + 3 > self.max_model_calls
                or self._remaining(current) < self._reserve('generation', 'verification') + 1):
            return updates
        candidates = list(updates['candidates'].values())[:30]
        updates['model_calls'] = current['model_calls'] + 1  # Count attempts, including provider/format failures.
        try:
            result = await self._invoke(current, 'reranking', lambda: asyncio.wait_for(
                self.reranker.rank(s['plan'].resolved_question, candidates), timeout=3),
                                        reserve=self._reserve('generation', 'verification'))
            if (len(result.positions) != len(set(result.positions)) or any(
                    type(i) is not int or not 0 <= i < len(candidates) for i in result.positions)):
                raise ValueError('reranker mapping')
            ordered = [candidates[i] for i in result.positions] + list(updates['evidence'].values())
            evidence, size = {}, 0
            for item in ordered:
                cid = item.metadata.chunk_id
                if cid not in evidence and len(evidence) < 20 and size + len(item.text) <= 12000:
                    evidence[cid] = item
                    size += len(item.text)
            updates.update(evidence=evidence,
                           input_tokens=current['input_tokens'] + result.input_tokens,
                           output_tokens=current['output_tokens'] + result.output_tokens)
        except Exception:
            self._diagnostic({**current, **updates}, 'reranking', ['RERANK_FALLBACK'])
        return updates

    async def _tools(self, s):
        evidence = dict(s['evidence'])
        candidates = dict(s['candidates'])
        rankings = []
        tool_calls, search_calls = s['tool_calls'], s['search_calls']
        embedding_tokens = s['embedding_tokens']
        embedding_model = s['embedding_model']
        notes = list(s['evidence_notes'])
        for call in s['pending_tools']:
            if tool_calls >= self.max_tool_calls:
                raise upstream_error()
            tool_calls += 1
            name, args = call['name'], call['args']
            if name == 'search_documents':
                if (set(args) != {'query'} or not isinstance(args['query'], str)
                        or not 1 <= len(args['query'].strip()) <= 2000 or search_calls >= self.max_search_calls):
                    raise upstream_error()
                search_calls += 1
                batch = await self._invoke(s, 'retrieval', lambda: self.retriever.search(args['query'],
                    s['context'].model_copy(update={'question': s['plan'].resolved_question,
                        'deadline_ms': max(1, int(self._remaining(s) * 1000))})),
                    reserve=self._reserve('generation', 'verification'))
            elif name == 'read_sections':
                ids = args.get('chunk_ids')
                if (set(args) != {'chunk_ids'} or not isinstance(ids, list) or not 1 <= len(ids) <= 5
                        or any(not isinstance(i, str) or i not in candidates for i in ids)):
                    raise upstream_error()
                batch = await self._invoke(s, 'retrieval', lambda: self.retriever.read_sections(tuple(ids), s['context']),
                                          reserve=self._reserve('generation', 'verification'))
                if any(not any(context_related(candidates[i], e) for i in ids) for e in batch.items):
                    raise upstream_error()
            else:
                raise upstream_error()
            batch = validate_batch(batch, s['context'])
            notes.extend(batch.notes)
            models = {e.metadata.embedding_model for e in (*batch.items, *batch.candidates)}
            batch_model = batch.embedding_model if batch.embedding_model != 'not-used' else next(iter(models), 'not-used')
            if len(models) > 1 or (batch_model != 'not-used' and (
                    embedding_model not in ('not-used', batch_model) or any(m != batch_model for m in models))):
                raise upstream_error()
            if batch_model != 'not-used':
                embedding_model = batch_model
            rankings.append([e.metadata.chunk_id for e in (batch.candidates or batch.items)])
            embedding_tokens += batch.embedding_tokens
            for item in (*batch.items, *batch.candidates):
                cid = item.metadata.chunk_id
                if cid in candidates and candidates[cid] != item:
                    raise upstream_error()
                candidates[cid] = item
            evidence.update((e.metadata.chunk_id, e) for e in batch.items)
        ranked_ids = reciprocal_rank_fusion(rankings)
        # Retain every candidate in request state, while bounding the full text
        # sent to the writer. New corrective results precede stale context.
        candidates = {cid: candidates[cid] for cid in dict.fromkeys([*ranked_ids, *candidates])}
        ordered = [evidence[cid] for cid in ranked_ids if cid in evidence]
        ordered += [e for cid, e in evidence.items() if cid not in ranked_ids]
        evidence, size = {}, 0
        for item in ordered:
            if len(evidence) < 20 and size + len(item.text) <= 12000:
                evidence[item.metadata.chunk_id] = item
                size += len(item.text)
        return {'pending_tools': [], 'evidence': evidence, 'candidates': candidates,
                'tool_calls': tool_calls, 'search_calls': search_calls,
                'candidate_count': len(candidates), 'embedding_tokens': embedding_tokens, 'embedding_model': embedding_model,
                'evidence_notes': notes}

    def _can_recover(self, s, *, calls, stages):
        return (s['repairs'] == 0 and s['model_calls'] + calls <= self.max_model_calls
                and self._remaining(s) > self._reserve(*stages) + .25)

    def _schedule_recovery(self, s, action, *, query=None, section_ids=(), missing_item_ids=None):
        """Schedule one bounded recovery from review or declared reference gaps.

        Missing documents trigger retrieval; explanation errors trigger rewriting.
        Only an already identified user-context gap may become a user question.
        """
        items = {i.item_id: i for i in s['assessment'].items}
        targets = set(missing_item_ids) if missing_item_ids is not None else {
            i.item_id for i in items.values() if i.state != 'supported'}
        if targets - items.keys():
            return {}
        if action in ('research', 'read_sections'):
            targets = {key for key in targets if items[key].state != 'missing_user_context'}
            if (not targets or s['tool_calls'] >= self.max_tool_calls
                    or not self._can_recover(s, calls=2,
                        stages=('retrieval', 'generation', 'verification'))):
                return {}
            if action == 'research' and query and s['search_calls'] < self.max_search_calls:
                call = {'name': 'search_documents', 'args': {'query': query}}
            elif action == 'read_sections' and section_ids and all(i in s['candidates'] for i in section_ids):
                call = {'name': 'read_sections', 'args': {'chunk_ids': list(section_ids)}}
            else:
                return {}
            return {'pending_tools': [call],
                    'next_node': 'tools', 'repairs': 1, 'fallback_draft': None}
        if action == 'clarify':
            # Do not reclassify missing documents or a failed answer as missing
            # user context. Use the concrete question already assessed, not a
            # generic "tell us your situation" question created after rejection.
            if (not targets or any(items[i].state != 'missing_user_context' for i in targets)
                    or not s['assessment'].follow_up_questions):
                return {}
            if self._can_recover(s, calls=2, stages=('generation', 'verification')):
                return {'assessment': s['assessment'].model_copy(update={'action': 'sufficient'}),
                        'next_node': 'generate', 'repairs': 1}
        if action == 'rewrite' and self._can_recover(s, calls=2, stages=('generation', 'verification')):
            return {'next_node': 'generate', 'repairs': 1}
        if action == 'reverify' and self._can_recover(s, calls=1, stages=('verification',)):
            return {'next_node': 'verify', 'repairs': 1}
        return {}

    def _has_facts(self, a):
        return any(i.state == 'supported' and i.evidence_refs for i in a.items)

    def _exact_coverage(self, s):
        # Matching one table row cannot establish coverage of a compound request.
        if (len(s['plan'].queries) != 1 or COMPARISON.search(s['context'].question)
                or COMPARISON.search(s['plan'].resolved_question)):
            return None
        refs = lookup_sources(s['plan'].resolved_question, s['evidence']) if s['plan'].intent == 'lookup' else []
        if not refs:
            return None
        return Assessment(action='sufficient', items=[AnswerItem(item_id='table-lookup',
            requirement=s['plan'].resolved_question[:200], answer_scope='document_rule',
            grounding=Grounding(evidence_status='complete', applicability='established',
                source_scope='검수 표의 명시적 항목·값 조회', question_scope=s['plan'].resolved_question[:300]),
            evidence_refs=[Quote.model_validate(r) for r in refs], conditions=[], missing_information=None)],
            reason='서버의 표 셀 조회', query=None, section_ids=[], follow_up_questions=[],
            resolved_question=s['plan'].resolved_question)

    def _writing_evidence(self, s):
        return [evidence_payload(e) for e in s['evidence'].values()
                if not table_blocked(e) and not e.truncated]

    def _writing_messages(self, s, *, verification=False, resolved_ids=(), exact=False):
        evidence = self._writing_evidence(s)
        visible_ids = {row['chunk_id'] for row in evidence}
        candidates = [(cid, e) for cid, e in s['candidates'].items() if cid not in visible_ids]
        if verification:
            # Review the item's evidence, not just the writer's selections.
            # Clarifications also need the source that makes the question necessary.
            covered = {b.item_id for b in s['draft'].blocks if b.placement == 'answer'}
            payload = {'original_question': s['context'].question, 'question': s['plan'].resolved_question,
                       'user_question_chain': [t.question for t in s['context'].history] + [s['context'].question],
                       'question_plan': s['plan'].model_dump(),
                       'items': [i.to_payload() for i in s['assessment'].items],
                       'as_of': s['context'].as_of.isoformat(), 'evidence': evidence,
                       'candidate_catalog': [{'chunk_id': cid, 'title': e.metadata.title, 'page': e.metadata.page,
                                              'article': e.metadata.article} for cid, e in candidates],
                       'retrieval_notes': s['evidence_notes'],
                       'answer_requirements': self._context(s)['answer_requirements'],
                       'expected_claim_ids': [b.block_id for b in s['draft'].blocks],
                       'literal_claim_ids': literal_claim_ids(s['draft'].blocks, s['evidence']),
                       'draft': {'status': s['draft'].status, 'category': s['draft'].category},
                       'limitations': limitations(s['assessment'], covered),
                       'follow_up_questions': s['draft'].follow_up_questions,
                       'review_correction': s['correction'],
                       'claims_to_check': claim_statements(s['draft'])}
            return [HumanMessage(content=json.dumps(payload, ensure_ascii=False))]
        payload = {**self._context(s), 'original_question': s['context'].question,
            'question': s['plan'].resolved_question, 'evidence': evidence,
            'candidate_catalog': [{'chunk_id': cid, 'title': e.metadata.title, 'article': e.metadata.article,
                                  'page': e.metadata.page} for cid, e in candidates],
            'requirements': [{'item_id': i.item_id, 'requirement': i.requirement, 'answer_scope': i.answer_scope}
                             for i in s['assessment'].items if i.item_id not in resolved_ids] if s['assessment'] else [],
            'verified_item_ids': sorted(resolved_ids), 'correction': s['correction'],
            'retrieval_notes': s['evidence_notes']}
        if exact:
            a = s['assessment']
            payload['assessment'] = {'items': [i.to_payload() for i in a.items if i.item_id not in resolved_ids]}
            approved = {}
            for item in a.items:
                for r in item.evidence_refs:
                    approved.setdefault((r.chunk_id, r.unit_id), []).append(item.item_id)
            for row in evidence:
                row['source_units'] = [u for u in row['source_units'] if (row['chunk_id'], u['unit_id']) in approved]
                for unit in row['source_units']:
                    unit['approved_item_ids'] = approved[row['chunk_id'], unit['unit_id']]
        return [SystemMessage(content=self.prompt), HumanMessage(content=json.dumps(payload, ensure_ascii=False))]

    def _budget_fallback(self, s):
        if s['fallback_draft']:
            return {'draft': s['fallback_draft'], 'verified': True, 'next_node': 'done'}
        self._diagnostic(s, 'generation', ['GENERATION_BUDGET_EXHAUSTED'])
        raise ServiceError(504, 'UPSTREAM_TIMEOUT', '상담 처리 제한 시간을 초과했습니다.', retryable=True)

    async def _generate(self, s):
        if s['fixed_clarification']:
            a = s['assessment']
            return {'draft': Draft(answer=('이전 대화에서 질문 대상을 확정하지 못했습니다. 대상을 직접 적어 주세요.'
                if a.reason == '대상 선택 검증 실패' else '답변에 필요한 상황을 먼저 확인해 주세요.'), steps=[], status='needs_review',
                used_chunk_ids=[], follow_up_questions=a.follow_up_questions, handoff_reason='지시 대상 확인 필요', category='other'),
                'verified': True, 'next_node': 'done'}
        if (s['model_calls'] + 2 > self.max_model_calls
                or self._remaining(s) < self._reserve('generation', 'verification') + .25):
            return self._budget_fallback(s)
        previous = s['fallback_draft']
        locked = list(previous.blocks) if previous else []
        locked_ids = {b.item_id for b in locked}
        a = s['assessment']
        exact = s['exact_lookup']
        if a is None:
            a = self._exact_coverage(s)
            exact = a is not None
        current = {**s, 'assessment': a}
        messages = self._writing_messages(current, resolved_ids=locked_ids, exact=exact)
        if exact:
            result, raw = await self._invoke(s, 'generation', lambda: self.model.finalize(messages),
                                             reserve=self._reserve('verification'))
            generated = CounselDraft.model_validate(result)
        else:
            result, raw = await self._invoke(s, 'generation', lambda: self.model.draft(messages),
                                             reserve=self._reserve('verification'))
            proposal = ProposedAnswer.model_validate(result)
            a = proposal.to_assessment(a, locked_ids=locked_ids,
                resolved_question=s['plan'].resolved_question)
            generated = CounselDraft(blocks=proposal.blocks, category=proposal.category)
        usage = self._usage(s, raw)
        usable = {cid: e for cid, e in s['evidence'].items() if not table_blocked(e) and not e.truncated}
        current = {**s, **usage, 'assessment': a, 'exact_lookup': exact}
        # Scope/provenance failures are never treated as a new source of authority.
        bad_ref_items = {i.item_id for i in a.items if assessment_failures(a.model_copy(update={'items': [i]}), usable)}
        resolved = list(locked)
        resolved_ids = set(locked_ids)
        if self.item_lookup:
            for item in a.items:
                if item.state != 'supported' or item.item_id in locked_ids or item.item_id in bad_ref_items:
                    continue
                found = await self.item_lookup(s['context'], item, usable)
                if found is not None:
                    found = CounselDraft.model_validate(found)
                    if found.blocks and all(b.item_id == item.item_id for b in found.blocks) and not block_failures(found.blocks, a, usable):
                        resolved.extend(found.blocks)
                        resolved_ids.add(item.item_id)
        proposed = [b for b in generated.blocks if b.item_id not in resolved_ids]
        blocks = [b.model_copy(update={'block_id': n}) for n, b in enumerate([*resolved, *proposed])] if resolved else proposed
        blocks = bind_literal_quantities(blocks, usable)
        structural = block_failures(blocks, a, usable)
        if bad_ref_items:
            structural.append({'code': 'SOURCE_UNIT_NOT_FOUND',
                               'claim_ids': [b.block_id for b in blocks if b.item_id in bad_ref_items]})
        if len(blocks) > 12 or sum(len(b.text) for b in blocks) > 4800 or any(len(b.text) > 300 for b in blocks if b.placement == 'step'):
            raise upstream_error()
        if structural:
            self._diagnostic(current, 'generation', [f['code'] for f in structural], failures=structural)
            correction = json.dumps({'failures': structural,
                'repair_targets': repair_details(blocks, structural),
                'repair_policy': '초안의 항목·값·단위를 실제 제공된 원문 구간에 연결한다. 불확실한 항목은 미확인으로 남긴다.'}, ensure_ascii=False)
            scheduled = self._schedule_recovery(current, 'rewrite')
            if scheduled:
                return {**usage, **scheduled, 'assessment': a, 'exact_lookup': exact, 'correction': correction}
            bad_ids = {n for f in structural for n in f['claim_ids']}
            bad_items = {b.item_id for b in blocks if b.block_id in bad_ids}
            blocks = [b for b in blocks if b.item_id not in bad_items]
            # Unknown/blocked proposed references must not reach review or public sources.
            a = a.model_copy(update={'items': [i.model_copy(update={
                'evidence_refs': [r for r in i.evidence_refs if r.chunk_id in usable and any(
                    u['unit_id'] == r.unit_id for u in source_units(usable[r.chunk_id]))],
                'conditions': []}) for i in a.items]})
        draft = compose(blocks, a, generated.category)
        if a.action == 'handoff' and not blocks:
            draft = draft.model_copy(update={'status': 'handoff', 'answer': '최종 판단은 담당자 확인이 필요합니다.',
                                            'handoff_reason': '개별 사안의 최종 판단 요청'})
        self._diagnostic({**current, 'assessment': a}, 'generation', ['DRAFT_FIRST'] if not exact else ['STRUCTURED_LOOKUP'])
        return {**usage, 'assessment': a, 'exact_lookup': exact, 'accepted_ids': a.accepted_chunk_ids,
                'draft': draft, 'verified': False, 'next_node': 'verify'}

    async def _verify(self, s):
        if s['model_calls'] >= self.max_model_calls:
            return self._budget_fallback(s)
        result, raw = await self._invoke(s, 'verification', lambda: self.model.verify(self._writing_messages(s, verification=True)))
        v = Verification.model_validate(result)
        cited = {i: s['evidence'][i] for i in s['draft'].used_chunk_ids}
        failures = verification_failures(v, claim_statements(s['draft']), cited,
                                         status=s['draft'].status, assessment=s['assessment'])
        failures += block_failures(s['draft'].blocks, s['assessment'], cited)
        codes = {f['code'] for f in failures}
        # The semantic judge cannot see deterministic post-check results yet.
        # Give local number/reference/coverage errors one correction even when
        # the judge itself said recovery=none.
        review_format_codes = {'VERIFIER_REFERENCE_MISMATCH', 'SOURCE_UNIT_NOT_FOUND',
                              'MISSING_QUOTE',
                              'CLAIM_COVERAGE_MISMATCH', 'ITEM_COVERAGE_MISMATCH', 'UNKNOWN_ITEM_ID'}
        recovery = 'reverify' if codes & review_format_codes else v.recovery
        if recovery == 'none' and codes & {'QUANTITY_SOURCE_MISMATCH', 'QUANTITY_LABEL_VALUE_MISMATCH',
                'QUANTITY_UNIT_MISMATCH', 'MISSING_QUANTITY_BINDING', 'CLAIM_COVERAGE_MISMATCH', 'SOURCE_UNIT_NOT_FOUND',
                'ITEM_SOURCE_MISMATCH', 'MISSING_ITEM_ANSWER', 'ITEM_INCOMPLETE',
                'CONDITION_LOSS', 'UNSUPPORTED_EXTENSION', 'SUBJECT_SCOPE_EXPANDED',
                'QUESTION_INCOMPLETE', 'CONTEXT_MISMATCH'}:
            recovery = 'rewrite'
        # Reuse the existing bounded lookup when the judge identifies missing
        # documents but omits the action. Never turn a user-context gap into search.
        missing = [i for i in s['assessment'].items if i.item_id in v.missing_item_ids]
        if (v.recovery == 'none' and not codes & review_format_codes
                and missing and all(i.state != 'missing_user_context' for i in missing)
                and (v.section_ids or v.query)):
            recovery = 'read_sections' if v.section_ids else 'research'
        updates = {**self._usage(s, raw), 'verified': not failures, 'next_node': 'done'}
        self._diagnostic({**s, **updates}, 'verification', [f['code'] for f in failures] or ['VERIFIED'], failures=failures)
        complete = {i.item_id for i in v.item_checks if i.complete}
        passed = retain_verified(s['draft'].blocks, v, failures) if failures else s['draft'].blocks
        passed = preserve_verified(s['fallback_draft'], passed, s['draft'].blocks,
                                   s['assessment'], v, failures)
        passed = deduplicate_verified(passed, s['evidence'])
        complete |= {b.item_id for b in passed}
        if not failures:
            if passed:
                updates['draft'] = compose(passed, s['assessment'], s['draft'].category,
                                           complete_ids=complete, evidence=s['evidence'])
            if recovery in ('research', 'read_sections'):
                scheduled = self._schedule_recovery({**s, **updates}, recovery, query=v.query,
                    section_ids=v.section_ids, missing_item_ids=v.missing_item_ids)
                if scheduled:
                    return {**updates, **scheduled, 'correction': '검수가 요청한 미확인 항목을 새로 조회한 원문으로 보완한다.'}
            return updates
        partial = compose(passed, s['assessment'], s['draft'].category,
                          complete_ids=complete, evidence=s['evidence'])
        if passed:
            # Correctness of global notices/questions must also be checked. Clear
            # generated questions after any failure before retaining independent facts.
            partial = partial.model_copy(update={'status': 'needs_review', 'follow_up_questions': [],
                         'handoff_reason': '일부 질문 항목의 근거 또는 적용 조건 추가 확인 필요'})
            updates['fallback_draft'] = partial
            # Recovery may reset generation's fallback to reconsider new
            # evidence. Interruption recovery retains this reviewed snapshot.
            updates['verified_partial'] = partial
        correction = json.dumps({'reason': v.reason, 'claims_to_correct': v.unsupported_claims,
                      'failures': failures, 'missing_item_ids': v.missing_item_ids,
                      'repair_targets': repair_details(s['draft'].blocks, failures),
                      'repair_policy': ('초안은 그대로 두고 검수 출력을 수정한다. 각 블록의 evidence_choices 번호만 고르고 expected_claim_ids와 항목 ID를 빠짐없이 검사한다.'
                          if recovery == 'reverify' else
                          '문서 누락은 해당 항목만 미확인으로 남기고 독립적으로 확인된 내용만 작성한다. 사용자 조건과 문서 누락을 구분한다.')}, ensure_ascii=False)
        current = {**s, **updates}
        scheduled = self._schedule_recovery(current, recovery, query=v.query,
                                            section_ids=v.section_ids, missing_item_ids=v.missing_item_ids)
        if scheduled:
            updates.update(scheduled, correction=correction)
            return updates
        self._diagnostic({**s, **updates}, 'repair', ['REPAIR_LIMIT_REACHED' if s['repairs'] else 'REPAIR_BUDGET_EXHAUSTED'])
        updates.update(draft=partial if passed else self._safe_draft(s), verified=bool(passed))
        return updates

    async def run(self, context: InternalQuery) -> InternalAnswer:
        if not self.retriever.ready:
            raise unavailable()
        started = time.monotonic()
        state = {'context': context, 'pending_tools': [], 'evidence': {}, 'candidates': {}, 'accepted_ids': [], 'assessment': None,
                 'model_calls': 0, 'tool_calls': 0, 'search_calls': 0, 'candidate_count': 0,
                 'input_tokens': 0, 'output_tokens': 0, 'embedding_tokens': 0, 'embedding_model': 'not-used',
                 'next_node': '', 'draft': None, 'end_at': started + context.deadline_ms / 1000,
                 'repairs': 0, 'correction': '', 'verified': False, 'evidence_notes': [],
                 'plan': None, 'fixed_clarification': False,
                 'fallback_draft': None, 'verified_partial': None, 'exact_lookup': False, 'stage_ms': {}}
        checkpoint = None
        try:
            async with asyncio.timeout(context.deadline_ms / 1000):
                with tracing_context(enabled=False):
                    async for changes in self.graph.astream(state, {'recursion_limit': 30}, stream_mode='updates'):
                        for node, update in changes.items():
                            state = {**state, **update}
                            if node == 'verify' or state['verified']:
                                safe = state['draft'] if state['verified'] else update.get('verified_partial')
                                if safe and safe.used_chunk_ids:
                                    checkpoint = {**state, 'draft': safe,
                                        'accepted_ids': safe.used_chunk_ids, 'verified': True}
            return self._respond(state, started)
        except TimeoutError:
            if checkpoint is not None:
                return self._timeout_answer(checkpoint, started)
            raise ServiceError(504, 'UPSTREAM_TIMEOUT', '상담 처리 제한 시간을 초과했습니다.', retryable=True) from None
        except ServiceError as exc:
            if exc.status == 504 and exc.code == 'UPSTREAM_TIMEOUT' and checkpoint is not None:
                return self._timeout_answer(checkpoint, started)
            raise
        except Exception:
            raise upstream_error() from None

    def _timeout_answer(self, checkpoint, started):
        self._diagnostic(checkpoint, 'timeout', ['VERIFIED_PARTIAL_RETURNED'])
        answer = self._respond(checkpoint, started)
        return answer.model_copy(update={'result': answer.result.model_copy(update={
            'status': 'needs_review', 'needs_review': True, 'handoff': False,
            'follow_up_questions': (),
            'handoff_reason': '추가 확인이 제한 시간 내 끝나지 않아 검증된 내용만 반환했습니다.'})})

    def _respond(self, state, started):
        context = state['context']
        action = state['assessment'].action
        no_evidence_action = action if state['verified'] and action in ('clarify', 'handoff') else None
        if not state['verified'] and (self._has_facts(state['assessment']) or action == 'clarify'):
            no_evidence_action = 'unverified'
        elif (no_evidence_action is None and not state['draft'].used_chunk_ids and any(
                i.evidence_refs and i.grounding.applicability == 'needs_reference'
                for i in state['assessment'].items)):
            no_evidence_action = 'applicability_gap'
        return assemble_answer(context, state['draft'], {i: state['evidence'][i] for i in state['accepted_ids']},
            latency_ms=int((time.monotonic() - started) * 1000), candidate_count=state['candidate_count'],
            usage=Usage(input_tokens=state['input_tokens'], output_tokens=state['output_tokens'],
                        embedding_tokens=state['embedding_tokens'], model=self.model.name,
                        embedding_model=state['embedding_model'], estimated_cost_usd=None),
            no_evidence_action=no_evidence_action)
