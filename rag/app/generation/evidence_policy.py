"""Generation may use verified text; unreviewed tables remain source pointers only."""
import re

TABLE_BLOCK_FLAGS = frozenset({'table_needs_review', 'table_pairing_mismatch', 'table_detection_failed'})
NO_EVIDENCE_ANSWER = '현재 접근 가능한 문서에서 이 질문을 뒷받침하는 근거를 확인하지 못했습니다. 답변은 등록된 매장 운영·가맹 자료에서 확인되는 내용으로 제한됩니다.'
NUMBER_PATTERN = r'\d+(?:,\d{3})*(?:\.\d+)?'
UNIT_PATTERN = r'천원|만원|억원|원|mL|ml|kg|mg|g|L|일|개월|년|시간|분|회|가지|%'


def table_blocked(item):
    return bool(TABLE_BLOCK_FLAGS.intersection(item.quality_flags))


def percent_notation(text):
    """Equivalent spelling only; no arithmetic or conversion of physical units."""
    return re.sub(r'100\s*분의\s*(' + NUMBER_PATTERN + r')(?![\d,.])', r'\1%', text)


def number_tokens(text):
    # No unit conversion or arithmetic: quantities must exist in the cited evidence.
    return {m.replace(',', '') for m in re.findall(NUMBER_PATTERN, percent_notation(text))}


def assigned_values(text, label):
    """Read explicit row keys or numeric cells, never a matching table title."""
    compact = lambda s: re.sub(r'\s+', '', s).replace(',', '')
    target, values = compact(label), []
    for line in re.split(r'[\n;；]', text):
        line = re.sub(r'^\s*(?:[-•]|\[행\s*\d+\])\s*', '', line)
        row = re.match(r'^([^:：|]+)[:：|]\s*(.+)$', line.strip())
        if not row:
            continue
        cells = re.split(r'[·|]', row[2])
        if compact(row[1]) in (target, target + '금액'):
            values.append(cells[0])
        for cell in cells:
            # A column may be rendered as '항목 275' inside another row key.
            match = re.match(re.escape(target) + r'(?:[:：=]|금액[:：]?)?(?=\d)(.+)$', compact(cell))
            if match:
                values.append(match[1])
    return values


def unsupported_numbers(draft, selected, context):
    # Block-based counseling is checked against the exact selected units. Legacy
    # deterministic recipe responses still use their hash-validated source text.
    if draft.blocks:
        from .review import evidence_units
        return any(quantity_failures(b, evidence_units({e.metadata.chunk_id: e for e in selected})) for b in draft.blocks)
    available = set()
    for item in selected:
        available |= number_tokens(item.text)
    return bool(number_tokens(draft.answer + '\n' + '\n'.join(draft.steps)) - available)


def bind_literal_quantities(blocks, evidence):
    """Keep metadata for printed numbers; repair unique cited literal pairs.

    No new source, fact, arithmetic or unit conversion is introduced. Ambiguous
    matches and wrong values/units still go through the ordinary failure checks.
    """
    from .review import Quantity, evidence_units, reference_key
    units = evidence_units(evidence)
    result = []
    for block in blocks:
        refs = [r for r in block.evidence_refs if reference_key(r) in units]
        def matches(q):
            found = []
            for ref in refs:
                text = percent_notation(units[reference_key(ref)]['text']).replace(',', '')
                pairs = re.findall(r'(?<![\d.])(' + NUMBER_PATTERN + r')\s*(' + UNIT_PATTERN + r')', text)
                if (q.value.replace(',', ''), q.unit) not in pairs:
                    continue
                candidate = q.model_copy(update={'evidence_ref': ref, 'context_refs': []})
                if q.label == '명시된 수치' and q.unit in {'원', '천원', '만원', '억원'}:
                    labels = [m[1].strip() for line in text.splitlines()
                        if (m := re.match(r'^\s*[-•]?\s*([^:：|]{2,150})[:：|]', line))
                        and re.sub(r'\s+', '', m[1]) in re.sub(r'\s+', '', block.text)]
                    if labels:
                        candidate = candidate.model_copy(update={'label': max(labels, key=len)})
                    elif len(set(pairs)) > 1:
                        continue
                probe = block.model_copy(update={'text': '', 'quantities': [candidate]})
                if not quantity_failures(probe, units):
                    found.append(candidate)
            return found
        quantities = []
        printed_numbers = number_tokens(block.text)
        for q in block.quantities:
            # Unused provider metadata is not a public claim. Removing it cannot
            # excuse a printed number: quantity_failures still requires every
            # number in the final text to have a valid source/unit binding.
            declared_numbers = number_tokens(q.value)
            if declared_numbers and not declared_numbers <= printed_numbers:
                continue
            probe = block.model_copy(update={'text': '', 'quantities': [q]})
            failures = set(quantity_failures(probe, units))
            candidates = matches(q) if failures == {'QUANTITY_SOURCE_MISMATCH'} and q.unit else []
            quantities.append(candidates[0] if len(candidates) == 1 else q)
        bound = {(q.value.replace(',', ''), q.unit) for q in quantities}
        for value, unit in re.findall(r'(?<![\d.])(' + NUMBER_PATTERN + r')\s*(' + UNIT_PATTERN + r')',
                                      percent_notation(block.text)):
            key = (value.replace(',', ''), unit)
            if key in bound or not refs or len(quantities) >= 8:
                continue
            q = Quantity(label='명시된 수치', value=value, unit=unit, evidence_ref=refs[0])
            candidates = matches(q)
            if len(candidates) == 1:
                quantities.append(candidates[0])
                bound.add(key)
        result.append(block.model_copy(update={'quantities': quantities}))
    return result


def quantity_failures(block, units):
    """Bind numbers to cited units and explicit label/value/unit tuples.

    Recognizable labelled rows get a deterministic mismatch guard. Ambiguous
    prose still requires semantic verification; this is not a general table parser.
    """
    from .review import reference_key
    compact = lambda s: re.sub(r'\s+', '', s).replace(',', '')
    refs = {reference_key(r) for r in block.evidence_refs}
    values = set()
    codes = []
    for q in block.quantities:
        key = reference_key(q.evidence_ref)
        if key not in refs or key not in units:
            codes.append('QUANTITY_SOURCE_MISMATCH')
            continue
        text = percent_notation(units[key]['text'])
        context_keys = [reference_key(r) for r in q.context_refs]
        if any(k not in refs or k not in units or units[k]['parent'] != units[key]['parent'] for k in context_keys):
            codes.append('QUANTITY_SOURCE_MISMATCH')
            continue
        # A table header/footnote may be a separate source unit. Its link must
        # remain explicit and belong to the same table/section, never another source.
        context_text = percent_notation('\n'.join([text, *(units[k]['text'] for k in context_keys)]))
        unit = '%' if q.unit == '100분의' else q.unit
        values |= number_tokens(q.value)
        if (not number_tokens(q.value) <= number_tokens(text) or compact(q.value) not in compact(text)
                or (unit and compact(unit) not in compact(context_text))):
            codes.append('QUANTITY_SOURCE_MISMATCH')
        # A unit must match, rather than treating '원' as interchangeable with
        # '천원'. Only explicit local value/unit pairs or table unit headers count.
        paired = re.findall(re.escape(q.value.replace(',', '')) + r'\s*(' + UNIT_PATTERN + r')', text.replace(',', ''))
        headers = re.findall(r'단위\s*[:：]\s*(' + UNIT_PATTERN + r')', context_text)
        if unit and (paired or headers) and unit not in (paired or headers):
            codes.append('QUANTITY_UNIT_MISMATCH')
        # Compare the assigned cell, rather than other numbers in the row or
        # headings that merely contain the label and the word "금액".
        rows = assigned_values(text, q.label)
        if rows and not any(number_tokens(q.value) <= number_tokens(r) and compact(q.value) in compact(r) for r in rows):
            codes.append('QUANTITY_LABEL_VALUE_MISMATCH')
    # Explicit source references can use the matching source's article/page.
    # These metadata numbers are never made available to factual amounts.
    factual_text = block.text
    # Resolve short paragraph references only within an explicitly named article.
    # The cited unit must actually be that paragraph; arbitrary numbers remain
    # factual quantities and still need their usual binding.
    article_context = None
    short_references = []
    for match in re.finditer(
            r'(?P<article>제\s*\d+\s*조(?:의\s*\d+)?)'
            r'(?:\s*제\s*\d+\s*항)?(?:\s*제\s*\d+\s*호)?'
            r'|(?P<paragraph>제\s*(?P<number>\d+)\s*항)(?!\s*제\s*\d+\s*호)', block.text):
        if match['article']:
            article_context = compact(match['article'])
        elif article_context:
            n = int(match['number'])
            if 1 <= n <= 20 and any(
                    compact(units.get(key, {}).get('article') or '') == article_context
                    and re.match(r'\s*' + chr(ord('①') + n - 1), units.get(key, {}).get('text', ''))
                    for key in refs):
                short_references.append(match.span())
    for start, end in reversed(short_references):
        factual_text = factual_text[:start] + factual_text[end:]
    for key in refs:
        source = units.get(key, {})
        article = source.get('article') or ''
        paragraph = re.match(r'\s*([①-⑳])', source.get('text', ''))
        paragraph_number = ord(paragraph[1]) - ord('①') + 1 if paragraph else None
        canonical = compact(article) + (f'제{paragraph_number}항' if paragraph_number else '')
        for citation in re.findall(r'제\s*\d+\s*조(?:의\s*\d+)?(?:\s*제\s*\d+\s*항)?(?:\s*제\s*\d+\s*호)?', block.text):
            if compact(citation) in (compact(article), canonical) or compact(citation) in compact(source.get('text', '')):
                factual_text = factual_text.replace(citation, '')
        if source.get('page'):
            factual_text = factual_text.replace(f"PDF {source['page']}쪽", '')
    if number_tokens(factual_text) - values:
        codes.append('MISSING_QUANTITY_BINDING')
    return list(dict.fromkeys(codes))
