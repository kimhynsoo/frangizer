"""Generation may use verified text; unreviewed tables remain source pointers only."""
import re

TABLE_BLOCK_FLAGS = frozenset({'table_needs_review', 'table_pairing_mismatch', 'table_detection_failed'})
NO_EVIDENCE_ANSWER = '등록된 문서에서 이 질문을 뒷받침하는 근거를 확인하지 못했습니다. 매장 운영·가맹 관련 문서에서 확인할 수 있는 질문을 남겨 주세요.'


def table_blocked(item):
    return bool(TABLE_BLOCK_FLAGS.intersection(item.quality_flags))


def number_tokens(text):
    # No unit conversion or arithmetic: quantities must exist in the cited evidence.
    return {m.replace(',', '') for m in re.findall(r'\d+(?:,\d{3})*(?:\.\d+)?', text)}


def unsupported_numbers(draft, selected, context):
    if draft.status != 'answered':
        return False
    available = set()
    for item in selected:
        available |= number_tokens(item.text + ' ' + item.metadata.version)
        if item.metadata.page:
            available.add(str(item.metadata.page))
    available |= number_tokens(context.as_of.isoformat())
    return bool(number_tokens(draft.answer + '\n' + '\n'.join(draft.steps)) - available)
