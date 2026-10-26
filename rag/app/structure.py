"""Conservative, source-linked section context; never modifies citation text.

Run on authorized rows at retrieval time. Headings are literal source spans, not
inferred legal business classifications. Multiple headings in one row stay scoped.
"""
import re
from collections import defaultdict

CONTEXT_VERSION = 'source-hierarchy-v1'
NUMBERED = re.compile(r'^\d{1,3}\.\s*\S')
SUB = re.compile(r'^(?:\d{1,2}\)|\(\d{1,2}\)|[①-⑳])\s*\S')
ANNEX = re.compile(r'^(?:■.*\[별표|<별표|\[별표)')
SUBJECT = re.compile(r'업|영업|준수사항|시설기준|품종|품목|식료품|제품|류|서비스|가맹|보증금|교육비|로열티|예치')
SENTENCE = re.compile(r'(?:한다|된다|있다|없다|하여야|해서는|경우|때에는|이내|것으로|제외한다|말\s*것|할\s*것)[.。]?$')
BLOCKED = {'table_needs_review', 'table_pairing_mismatch', 'table_detection_failed'}


def usable(row):
    return not row.get('truncated', False) and not BLOCKED.intersection(row.get('review', {}).get('quality_flags', []))


def heading_level(line):
    if not 2 <= len(line) <= 140 or SENTENCE.search(line):
        return None
    if ANNEX.match(line):
        return 0
    if NUMBERED.match(line) and SUBJECT.search(line):
        return 1
    if SUB.match(line) and SUBJECT.search(line):
        return 2
    return None


def infer_hierarchy(records):
    """Return original-offset segments with heading provenance, keyed by chunk ID.

    Caller must filter permission/version/time BEFORE this function. Scopes with
    different effective intervals cannot donate headings to one another.
    """
    groups = defaultdict(list)
    result = {}
    for order, row in enumerate(records):
        m = row['metadata']
        key = tuple(str(m.get(k)) for k in ('brand_id', 'document_id', 'version_id', 'index_revision',
                                           'effective_from', 'effective_until'))
        groups[key].append((order, row))
    for group in groups.values():
        stack = []
        previous_page = None
        first_parts = {}
        for _, row in group:
            section = row.get('section', {})
            parent = section.get('parent_section_key', row['metadata']['section_key'])
            if section.get('part', 1) == 1 and usable(row):
                first_parts[parent] = row
        for _, row in sorted(group, key=lambda pair: (pair[1]['metadata'].get('page') or 0, pair[0])):
            m, text = row['metadata'], row['text']
            page = m.get('page')
            if previous_page is not None and page is not None and page > previous_page + 1:
                stack = []  # Excluded/missing pages may contain a new scope heading.
            previous_page = page
            cid = m['chunk_id']
            spans = []
            if not usable(row):
                stack = []
                spans = [{'start': 0, 'end': len(text), 'headings': []}]
            elif m.get('article'):
                # Article qualifiers live in the original first part, not a guessed category.
                parent = row.get('section', {}).get('parent_section_key', m['section_key'])
                first = first_parts.get(parent, row)
                title = first['text'].splitlines()[0] if first['text'] else m['article']
                path = [{'level': 1, 'text': title, 'chunk_id': first['metadata']['chunk_id']}]
                spans = [{'start': 0, 'end': len(text), 'headings': path}]
            else:
                # Disclosure tables already have local titles/units; do not inherit another table's title.
                if m.get('material_type') != 'official':
                    stack = []
                start, offset = 0, 0
                lines = text.splitlines(keepends=True)
                for line_no, raw in enumerate(lines):
                    line = raw.strip()
                    level = heading_level(line)
                    if level is not None:
                        title = line
                        # Keep a wrapped qualification such as '(… 제외한다)' with its subject.
                        for continuation in lines[line_no + 1:line_no + 3]:
                            if title.count('(') <= title.count(')'):
                                break
                            if re.match(r'^[가-하]\.\s', continuation.strip()):
                                break
                            title += '\n' + continuation.strip()
                        new = [h for h in stack if h['level'] < level]
                        new.append({'level': level, 'text': title, 'chunk_id': cid})
                        if [h['text'] for h in new] != [h['text'] for h in stack]:
                            if offset > start:
                                spans.append({'start': start, 'end': offset, 'headings': list(stack)})
                            stack, start = new, offset
                    offset += len(raw)
                if len(text) > start:
                    spans.append({'start': start, 'end': len(text), 'headings': list(stack)})
            refs = list(dict.fromkeys(h['chunk_id'] for span in spans for h in span['headings'] if h['chunk_id'] != cid))
            paths = {tuple(h['text'] for h in span['headings'] if h['level'] <= 1)
                     for span in spans if span['headings']}
            result[cid] = {'version': CONTEXT_VERSION, 'segments': spans, 'source_chunk_ids': refs,
                           'multiple_scopes': len(paths) > 1}
    return result


def search_text(text, hierarchy):
    headings = list(dict.fromkeys(h['text'] for span in hierarchy.get('segments', []) for h in span['headings']))
    prefix = '\n'.join(headings)
    return ('[검색용 상위 문맥]\n' + prefix + '\n[인용 원문]\n' + text) if prefix else text
