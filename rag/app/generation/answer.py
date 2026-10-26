"""Server-owned answer composition, public text and citation rendering."""
import re

from ..errors import upstream_error
from ..privacy import redact
from ..retrieval.base import Evidence, is_allowed
from ..schemas import (
    Classification, InternalAnswer, InternalQuery, RagAnswer, Retrieval, Source, Usage,
)
from .model import Draft
from .review import reference_key
from .evidence_policy import NO_EVIDENCE_ANSWER, table_blocked, unsupported_numbers


def source_excerpt(item, draft):
    """Return selected original units, with explicit elisions when API space is short."""
    from .review import source_units
    wanted = {r.unit_id for r in draft.cited_units if r.chunk_id == item.metadata.chunk_id}
    units = [u for u in source_units(item) if u['unit_id'] in wanted]
    if not units:  # Hash-validated deterministic recipe path.
        return redact(item.text)[:1500]
    separator = '\n[…다른 원문 구간…]\n'
    text = separator.join(u['text'].strip() for u in units)
    if len(text) <= 1500:
        return text
    notice = '[선택한 원문 구간 일부 · 전체 조건은 원문 확인]\n'
    units = units[:8]
    per_unit = max(1, (1500 - len(notice) - len(separator) * (len(units) - 1)) // len(units))
    excerpts = []
    for u in units:
        body = u['text'].strip()
        labels = [q.label for b in draft.blocks for q in b.quantities
                  if q.evidence_ref.chunk_id == item.metadata.chunk_id and q.evidence_ref.unit_id == u['unit_id']]
        found = next((body.find(label) for label in labels if label in body), 0)
        start = max(0, found - per_unit // 4) if len(body) > per_unit else 0
        piece = body[start:start + max(0, per_unit - 2)]
        excerpts.append(('…' if start else '') + piece + ('…' if start + len(piece) < len(body) else ''))
    return notice + separator.join(excerpts)


def assemble_answer(
    context: InternalQuery, draft: Draft, evidence: dict[str, Evidence], *,
    latency_ms: int, candidate_count: int, usage: Usage,
    llm_called: bool = True,
    no_evidence_action: str | None = None,
) -> InternalAnswer:
    draft = clean_draft(draft)
    selected: list[Evidence] = []
    for chunk_id in dict.fromkeys(draft.used_chunk_ids):
        item = evidence.get(chunk_id)
        if item is None or not is_allowed(item, context):
            raise upstream_error()
        selected.append(item)

    if not selected:
        if no_evidence_action == 'handoff':
            draft = draft.model_copy(update={
                'status': 'handoff', 'answer': '이 사안의 최종 판단은 담당자 확인이 필요합니다. 현재 답변으로 책임이나 배상액을 확정할 수 없습니다.',
                'steps': [], 'follow_up_questions': [], 'handoff_reason': '개별 사안의 최종 판단 요청',
            })
        elif no_evidence_action == 'clarify' and draft.follow_up_questions:
            draft = draft.model_copy(update={
                'status': 'needs_review', 'answer': '\n'.join(draft.follow_up_questions),
                'steps': [], 'handoff_reason': '답변이 달라지는 사용자 조건 확인 필요',
            })
        elif no_evidence_action == 'unverified':
            draft = draft.model_copy(update={
                'status': 'needs_review',
                'answer': '검색한 문서의 적용 조건이나 답변과의 일치를 확인하지 못해 답변을 확정하지 않았습니다.',
                'steps': [], 'follow_up_questions': [], 'handoff_reason': '근거와 답변의 적합성 추가 확인 필요',
            })
        elif no_evidence_action == 'applicability_gap':
            draft = draft.model_copy(update={
                'status': 'needs_review',
                'answer': '관련 문서는 찾았지만 질문에 적용할 대상·조건 또는 참조 기준을 확인하지 못했습니다. 현재 자료만으로 적용 여부를 확정하기 어렵습니다.',
                'steps': [], 'follow_up_questions': [], 'handoff_reason': '적용 범위 및 참조 근거 추가 확인 필요',
            })
        else:
            draft = draft.model_copy(update={
                'status': 'needs_review', 'answer': NO_EVIDENCE_ANSWER, 'steps': [],
                'follow_up_questions': [], 'handoff_reason': '등록된 문서 근거 부족',
            })
    elif any(table_blocked(e) for e in selected):
        locations = list(dict.fromkeys(
            f'{e.metadata.title} PDF {e.metadata.page}쪽' if e.metadata.page else e.metadata.title
            for e in selected if table_blocked(e)
        ))
        draft = draft.model_copy(update={
            "status": "needs_review", "answer": '관련 표의 조건과 수치가 아직 검수되지 않아 답변을 확정할 수 없습니다. '
                + ' / '.join(locations)[:1000] + '의 원문을 아래 출처에서 확인해 주세요.',
            "steps": [], "follow_up_questions": [], "handoff_reason": "표 추출 품질 검수 필요",
        })
    elif unsupported_numbers(draft, selected, context):
        draft = draft.model_copy(update={
            'status': 'needs_review', 'answer': '인용한 문서에서 답변의 수치를 확인하지 못해 정확한 기준을 안내하기 어렵습니다. 아래 출처 원문을 확인해 주세요.',
            'steps': [], 'follow_up_questions': [], 'handoff_reason': '인용 근거와 수치 불일치',
        })

    if draft.status == "answered" and (not selected or any(e.truncated for e in selected)):
        raise upstream_error()
    if draft.status == "needs_review" and not (draft.follow_up_questions or draft.handoff_reason):
        raise upstream_error()
    if draft.status == "handoff" and not draft.handoff_reason:
        raise upstream_error()

    sources = []
    for item in selected:
        m = item.metadata
        location = " / ".join(x for x in (
            f"PDF {m.page}쪽" if m.page else None, m.article, m.section_key,
        ) if x)
        detail_url = f"/api/v1/documents/{m.document_id}/versions/{m.version_id}"
        sources.append(Source(
            chunk_id=m.chunk_id, document_id=m.document_id, version_id=m.version_id,
            title=m.title, version=m.version, location=location, page=m.page, article=m.article,
            effective_from=m.effective_from, effective_until=m.effective_until,
            material_type=m.material_type, is_demo=m.is_demo,
            excerpt=('[미검수 표] 추출된 셀 대신 PDF 원문을 확인해 주세요.' if table_blocked(item) else source_excerpt(item, draft)),
            detail_url=detail_url, file_url=detail_url + "/file",
        ))
    answer = draft.answer
    answer = redact(answer)
    if any(s.is_demo for s in sources):
        label = "합성 시연 레시피 기준입니다. " if draft.category == "recipe" else "합성 시연 자료 기준입니다. "
        answer = label + answer

    # Topic IDs are controlled by the gateway; a free-form model topic is not accepted.
    result = RagAnswer(
        question_id=context.question_id, conversation_id=context.conversation_id,
        answer=answer, steps=tuple(redact(s) for s in draft.steps), status=draft.status,
        needs_review=draft.status != "answered", handoff=draft.status == "handoff",
        sources=tuple(sources), follow_up_questions=tuple(redact(q) for q in draft.follow_up_questions),
        handoff_reason=None if draft.status == "answered" else redact(draft.handoff_reason) if draft.handoff_reason else None,
        category=draft.category, topic_id=None, classification_status="pending",
        as_of=context.as_of, basis=context.basis, latency_ms=latency_ms,
    )
    return InternalAnswer(
        result=result, scope_revision=context.scope_revision,
        classification=Classification(category=draft.category, topic_id=None, status="pending", version="counsel-v1"),
        usage=usage, retrieval=Retrieval(
            candidate_count=candidate_count, used_chunk_ids=tuple(s.chunk_id for s in sources),
            llm_called=llm_called,
        ),
    )


def limitations(assessment, covered):
    rows = []
    for item in assessment.items:
        if item.item_id in covered:
            continue
        reason = {'missing_user_context': '적용에 필요한 사용자 조건을 추가로 확인해야 합니다.',
                  'conflict': '확보한 자료 사이의 기준을 확정하지 못했습니다.'}.get(
                      item.state, '현재 확보한 근거로는 답을 확정하지 못했습니다.')
        rows.append(f'「{item.requirement}」: {reason}')
    return rows


def compose(blocks, assessment, category, *, complete_ids=None, evidence=None):
    """Render whole items only. A failed/incomplete item is a notice, not fragments."""
    covered = {b.item_id for b in blocks if b.placement == 'answer'}
    covered &= {i.item_id for i in assessment.items if i.state == 'supported'}
    if complete_ids is not None:
        covered &= set(complete_ids)
    # Before verification keep every proposed block visible to the reviewer,
    # including an invalid step-only item. Only final rendering filters items.
    if complete_ids is not None:
        blocks = [b for b in blocks if b.item_id in covered]
    scopes = {i.item_id: i.answer_scope for i in assessment.items}
    # Group repeated literal rows only after review, leaving provenance intact.
    from .table_evidence import table_display_rows
    display = table_display_rows(blocks, evidence) if complete_ids is not None and evidence else {}
    body, shown = [], set()
    for b in blocks:
        if b.placement == 'answer':
            label = '문서 기준: ' if scopes.get(b.item_id) == 'document_rule' and b.item_id not in shown else ''
            text = display.get(b.block_id, b.text)
            if text is not None:
                body.append(label + text)
            shown.add(b.item_id)
    steps = [b.text for b in blocks if b.placement == 'step']
    refs = [r for b in blocks for r in b.evidence_refs]
    refs += [r for i in assessment.items if i.item_id in covered
             for condition in i.conditions for r in condition.evidence_refs]
    unique = {reference_key(r): r for r in refs}
    answer = '\n\n'.join([*body, *limitations(assessment, covered)])
    status = 'answered' if body and len(covered) == len(assessment.items) else 'needs_review'
    return Draft(answer=answer or '현재 접근 가능한 문서에서 질문의 근거를 확인하지 못했습니다.',
                 steps=steps, status=status, used_chunk_ids=list(dict.fromkeys(k[0] for k in unique)),
                 follow_up_questions=assessment.follow_up_questions if any(
                     i.state == 'missing_user_context' for i in assessment.items) else [],
                 handoff_reason=None if status == 'answered' else '질문 항목의 근거 또는 적용 조건 추가 확인 필요',
                 category=category, blocks=blocks, cited_units=list(unique.values()))


def deduplicate_verified(blocks, evidence):
    """Merge only reviewed identical statements, within one item and source scope.

    No semantic guessing or merging of amounts alone. Keep every approved citation;
    distinct wording, units, periods and items remain independent statements.
    """
    result, seen = [], {}
    compact = lambda text: re.sub(r'\s+', '', text)
    for block in blocks:
        sources = [evidence[r.chunk_id].metadata for r in block.evidence_refs]
        scope = frozenset((m.document_id, m.version_id, m.index_revision, m.effective_from, m.effective_until)
                          for m in sources)
        values = tuple(sorted((compact(q.label), q.value, q.unit) for q in block.quantities))
        key = (block.item_id, block.placement, compact(block.text), scope, values)
        if key in seen:
            pos = seen[key]
            refs = list(result[pos].evidence_refs)
            for ref in block.evidence_refs:
                if ref not in refs:
                    refs.append(ref)
            if len(refs) <= 8:
                result[pos] = result[pos].model_copy(update={'evidence_refs': refs})
                continue
        seen[key] = len(result)
        result.append(block)
    return result


def retain_verified(blocks, verdict, failures):
    """All blocks of an item must pass. Never infer inter-sentence dependencies."""
    codes = {f['code'] for f in failures}
    if not verdict.context_consistent or codes & {
        'CLAIM_COVERAGE_MISMATCH', 'ITEM_COVERAGE_MISMATCH', 'UNKNOWN_ITEM_ID',
    }:
        return []
    bad = {i for f in failures for i in f['claim_ids']}
    if (not verdict.supported or verdict.unsupported_claims) and not bad:
        return []
    if not verdict.question_answered and all(i.complete for i in verdict.item_checks):
        return []
    checked = {c.claim_id for c in verdict.claims}
    failed_items = {b.item_id for b in blocks if b.block_id in bad or b.block_id not in checked}
    complete = {i.item_id for i in verdict.item_checks if i.complete} - failed_items
    complete &= {b.item_id for b in blocks if b.placement == 'answer'}
    return [b for b in blocks if b.item_id in complete]


def preserve_verified(previous, passed, proposed, assessment, verdict, failures):
    """Keep omitted, previously verified items during a rewrite of the same evidence.

    A new assessment clears this snapshot in the graph. Re-proposed items must
    pass the new review; an older version cannot override a newer rejection.
    """
    if previous is None:
        return passed
    codes = {f['code'] for f in failures}
    if (not verdict.context_consistent or codes & {
            'CLAIM_COVERAGE_MISMATCH', 'ITEM_COVERAGE_MISMATCH', 'UNKNOWN_ITEM_ID'}
            or ((not verdict.supported or verdict.unsupported_claims)
                and not any(f['claim_ids'] for f in failures))
            or (not verdict.question_answered and all(i.complete for i in verdict.item_checks))):
        return passed
    attempted = {b.item_id for b in proposed}
    supported = {i.item_id for i in assessment.items if i.state == 'supported'}
    old = [b for b in previous.blocks if b.item_id not in attempted and b.item_id in supported]
    # Keep whole groups and stay within the writer/output size contract.
    combined = list(passed)
    for item_id in dict.fromkeys(b.item_id for b in old):
        group = [b for b in old if b.item_id == item_id]
        if len(combined) + len(group) <= 12 and sum(len(b.text) for b in [*combined, *group]) <= 4800:
            combined.extend(group)
    # Draft-local IDs may collide across two generations. Renumber only after
    # review; never change the IDs submitted to the verifier.
    return [b.model_copy(update={'block_id': i}) for i, b in enumerate(combined)]


PROCESS_SENTENCE = re.compile(
    r'^(?:(?:이제|그럼|먼저)\s*)?'
    r'(?:도구\s*(?:사용|호출)(?:을|를)?\s*(?:종료|중단|마무리|마치|끝내)'
    r'|(?:최종\s*)?(?:초안|답변)(?:을|를)?\s*작성'
    r'|(?:검색|확인|확보)한\s*(?:문서|자료|근거)[^0-9.!?。]*?(?:초안|답변)(?:을|를)?\s*작성'
    r'|(?:관련\s*)?(?:문서|자료|근거)(?:를|을)\s*(?:검색|조회|확인))'
    r'[^0-9.!?。]*(?:하겠습니다|합니다|했습니다)[.!?。]?$',
)


def public_text(text):
    parts = re.split(r'((?<=[.!?。])\s+|[\r\n]+)', text)
    for i in range(0, len(parts), 2):
        if PROCESS_SENTENCE.fullmatch(parts[i].strip()):
            parts[i] = ''
            if i + 1 < len(parts):
                parts[i + 1] = ''
    return ''.join(parts).strip()


def clean_draft(draft):
    answer = public_text(draft.answer)
    if not answer:
        return draft.model_copy(update={
            'status': 'needs_review',
            'answer': '확보한 문서 근거로 질문에 대한 답변을 완성하지 못했습니다. 아래 출처 원문을 확인해 주세요.',
            'steps': [], 'follow_up_questions': [], 'handoff_reason': '질문에 대한 답변 추가 확인 필요',
        })
    steps = [t for s in draft.steps if (t := public_text(s))]
    follow = [t for s in draft.follow_up_questions if (t := public_text(s))]
    reason = public_text(draft.handoff_reason) if draft.handoff_reason else None
    if (draft.status == 'handoff' and not reason) or (draft.status == 'needs_review' and not (follow or reason)):
        reason = '담당자 추가 확인 필요'
    return draft.model_copy(update={
        'answer': answer, 'steps': steps, 'follow_up_questions': follow, 'handoff_reason': reason or None,
    })
