"""Conservative masking of recognizable identifiers, not general-purpose PII detection."""
import re

from .schemas import HistoryTurn, InternalQuery

PATTERNS = (
    (re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"), "[이메일]"),
    (re.compile(r"(?<!\d)(?:\+82[- ]?)?0?1[016789][- ]?\d{3,4}[- ]?\d{4}(?!\d)"), "[전화번호]"),
    (re.compile(r"(?<!\d)\d{6}[- ]?[1-4]\d{6}(?!\d)"), "[식별번호]"),
    (re.compile(r"(고객명|고객 이름|주문번호)\s*[:：=]\s*[^\s,;]+"), r"\1: [비식별]"),
)


def redact(text: str) -> str:
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_query(query: InternalQuery) -> InternalQuery:
    return query.model_copy(update={
        "question": redact(query.question),
        "history": tuple(HistoryTurn(question=redact(t.question), answer=redact(t.answer)) for t in query.history),
    })
