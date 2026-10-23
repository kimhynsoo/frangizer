from ..errors import upstream_error
from ..privacy import redact
from ..retrieval.base import Evidence, is_allowed
from ..schemas import (
    Classification, InternalAnswer, InternalQuery, RagAnswer, Retrieval, Source, Usage,
)
from .model import Draft


def assemble_answer(
    context: InternalQuery, draft: Draft, evidence: dict[str, Evidence], *,
    latency_ms: int, candidate_count: int, usage: Usage,
    llm_called: bool = True,
) -> InternalAnswer:
    selected: list[Evidence] = []
    for chunk_id in dict.fromkeys(draft.used_chunk_ids):
        item = evidence.get(chunk_id)
        if item is None or not is_allowed(item, context):
            raise upstream_error()
        selected.append(item)

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
            material_type=m.material_type, is_demo=m.is_demo, excerpt=redact(item.text)[:1500],
            detail_url=detail_url, file_url=detail_url + "/file",
        ))
    answer = redact(draft.answer)
    if any(e.legal_temporal_unreviewed for e in selected):
        answer = "[시행일 미검수] 인용 자료의 조문별 시행일 검수가 완료되지 않았습니다. " + answer
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
