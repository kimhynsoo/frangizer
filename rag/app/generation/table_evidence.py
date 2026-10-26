"""Server-owned table choices, bound to literal text in an authorized revision."""
import re
from copy import deepcopy

from ..errors import upstream_error
from .evidence_policy import (NUMBER_PATTERN as NUMBER, UNIT_PATTERN as UNIT,
                              assigned_values, number_tokens, percent_notation, table_blocked)
from .review import Quantity, Quote, source_units

TOTAL = re.compile(r'합계|합산|총액|총계|소계')

def table_rows(item):
    """Only complete reviewed rows whose title, units and text match this chunk.

    Metadata alone is never evidence. Legacy, partial and unreviewed tables use
    the existing text path until a complete row and its context are available.
    """
    t = item.table or {}
    if (t.get('schema') != 'reviewed-cells-v1' or t.get('reviewed') is not True
            or table_blocked(item) or item.truncated):
        return []
    columns, rows = t.get('columns'), t.get('rows')
    title, unit = t.get('title'), t.get('unit') or ''
    if (not isinstance(columns, list) or not 2 <= len(columns) <= 30
            or any(not isinstance(c, str) or not c for c in columns)
            or not isinstance(rows, list) or len(rows) > 5000
            or not isinstance(title, str) or not title or not isinstance(unit, str)):
        return []
    units, result = source_units(item), []
    for n, row in enumerate(rows):
        if (not isinstance(row, list) or len(row) != len(columns) or not row[0]
                or any(c is not None and not isinstance(c, str) for c in row)):
            continue
        line = '- ' + row[0] + ': ' + ' · '.join(
            f'{name} {value}' for name, value in zip(columns[1:], row[1:]) if value)
        original = next((u for u in units if line in u['text'] and title in u['text']
                         and (not unit or unit in u['text'])), None)
        if original is None:
            continue
        result.append({'row_key': f'{item.metadata.chunk_id}:{n}', 'label': row[0],
                       'title': title, 'unit': unit,
                       'fields': [{'column': c, 'value': v, 'column_index': i}
                                  for i, (c, v) in enumerate(zip(columns[1:], row[1:]), 1)],
                       'evidence_ref': {'chunk_id': item.metadata.chunk_id, 'unit_id': original['unit_id']}})
    return result


def lookup_sources(question, evidence):
    """Supply verified source choices for an explicitly named row or column.

    Selection and final question/condition verification still have to pass.
    """
    compact = lambda s: re.sub(r'\s+', '', s)
    question, refs = compact(question), []
    for item in evidence.values():
        rows = table_rows(item)
        names = [r['label'] for r in rows] + [f['column'] for r in rows for f in r['fields']]
        if any(len(compact(name)) >= 3 and compact(name) in question for name in names):
            for row in rows:
                if row['evidence_ref'] not in refs:
                    refs.append(row['evidence_ref'])
    return refs[:8]


def source_choices(evidence, *, item_scoped):
    """One source-owned cell catalog for literal and general drafts."""
    choices = []
    for record in evidence:
        units = {u['unit_id']: u for u in record['source_units']}
        for row in record.get('table_rows', []):
            original = units.get(row['evidence_ref']['unit_id'])
            if original is None:
                continue
            base = {k: v for k, v in row.items() if k != 'fields'}
            for item_id in (original.get('approved_item_ids', []) if item_scoped else [None]):
                for field in row['fields']:
                    if (not field['value'] or sum(len(v) for v in (
                            row['label'], row['unit'], field['column'], field['value'])) > 950
                            or sum(len(re.findall(NUMBER, v)) for v in
                                   (row['label'], row['unit'], field['value'])) > 8):
                        continue
                    if len(choices) < 80:
                        choices.append({'id': len(choices), 'item_id': item_id, **base,
                                        **field, '_source_text': original['text']})
    return choices


def prepare_table_writer(payload, schema):
    """Encode the item, row, field, context and citation in each selectable ID."""
    payload, schema = deepcopy(payload), deepcopy(schema)
    if payload.get('question_plan', {}).get('intent') != 'lookup':
        return payload, schema, []
    choices = source_choices(payload['evidence'], item_scoped=True)
    requirements = {i['item_id']: i.get('requirement', '') for i in payload['assessment']['items']}
    for choice in choices:
        choice['_total_requested'] = bool(TOTAL.search(requirements.get(choice['item_id'], '')))
    approved = {i['item_id'] for i in payload['assessment']['items'] if i['state'] == 'supported'}
    table_items = approved & {c['item_id'] for c in choices}
    if not choices or not table_items:
        return payload, schema, []
    for record in payload['evidence']:
        record.pop('table_rows', None)
    payload['table_choices'] = [{k: v for k, v in c.items() if k not in ('fields', '_source_text', '_total_requested')}
                                for c in choices]
    schema['properties']['table_choice_ids'] = {
        'type': 'array', 'items': {'type': 'integer', 'enum': list(range(len(choices)))}, 'maxItems': 8}
    schema.setdefault('required', []).append('table_choice_ids')
    # Each item has one owner: literal cells or prose, never both. Mixed lookup
    # questions no longer lose cell binding because another item is ordinary text.
    prose_items = sorted(approved - table_items)
    if prose_items:
        schema['$defs']['AnswerBlock']['properties']['item_id']['enum'] = prose_items
    else:
        schema['properties']['blocks']['maxItems'] = 0
    return payload, schema, choices


def _quantities(text, label, choice, *, cell=False):
    quantities = []
    # Bind a literal ratio to its numerator, never mistake the fixed denominator
    # for a separate amount. Rendered table text and original source stay intact.
    text = percent_notation(text)
    header = re.search(r'단위\s*[:：]\s*(' + UNIT + r')', choice['unit'])
    for match in re.finditer(NUMBER, text):
        explicit = re.match(r'\s*(' + UNIT + r')', text[match.end():])
        unit = explicit[1] if explicit else header[1] if cell and header and re.fullmatch(NUMBER, text) else ''
        # A field's row key can be the literal assignment (e.g. "교육비: 금액 ...").
        if cell and choice['column_index'] == 1 and any(number_tokens(match[0]) <= number_tokens(v)
                        for v in assigned_values(choice['_source_text'], choice['label'])):
            label = choice['label']
        quantities.append(Quantity(label=label[:150], value=match[0], unit=unit,
                                   evidence_ref=Quote.model_validate(choice['evidence_ref'])))
    return [q.model_dump() for q in quantities]


def _literal_block(cells, block_id):
    c = cells[0]
    body = c['label'] + ' — ' + ' · '.join(f"{v['column']}: {v['value']}" for v in cells)
    include_unit = bool(re.search(r'단위\s*[:：]', c['unit'])
                        and any(re.fullmatch(NUMBER, v['value']) for v in cells))
    if include_unit:
        body += ' ' + c['unit']
    quantities = [q for v in cells for q in _quantities(v['value'], v['column'], v, cell=True)]
    quantities += _quantities(c['label'], '행 항목', c)
    if include_unit:
        quantities += _quantities(c['unit'], '표 단위·조건', c)
    return {'block_id': block_id, 'item_id': c['item_id'], 'text': body,
            'placement': 'answer', 'evidence_refs': [c['evidence_ref']], 'quantities': quantities}


def _expand_totals(selected, choices):
    selected = [{**c, '_total_requested': c.get('_total_requested', False)} for c in selected]
    # A displayed total carries the source table's same-column rows, not an
    # inferred arithmetic relationship. Expand only a bounded original table.
    for cell in list(selected):
        if TOTAL.search(cell['label']) or cell.get('_total_requested'):
            siblings = [{**c, 'item_id': cell['item_id'], '_total_requested': cell.get('_total_requested', False)} for c in choices
                if c['evidence_ref'] == cell['evidence_ref'] and c['title'] == cell['title']
                and c['column_index'] == cell['column_index']]
            if not any(TOTAL.search(c['label']) for c in siblings):
                continue
            expanded = list(selected)
            for sibling in siblings:
                if sibling not in expanded:
                    expanded.append(sibling)
            if len(expanded) <= 8:
                selected = expanded
    selected.sort(key=lambda c: (c['item_id'], c['row_key'], c['column_index']))
    return selected


def resolve_table_writer(parsed, choices, *, expansion_choices=None):
    """Restore exact strings and provenance; never accept model-rewritten cells."""
    if not choices:
        return parsed
    result = deepcopy(parsed)
    selected = result.pop('table_choice_ids', [])
    if (not isinstance(selected, list) or len(selected) > 8
            or any(type(i) is not int or not 0 <= i < len(choices) for i in selected)):
        raise upstream_error()
    table_items = {c['item_id'] for c in choices}
    if any(b['item_id'] in table_items for b in result['blocks']):
        raise upstream_error()
    next_id = max((b['block_id'] for b in result['blocks']), default=-1) + 1
    rows = {}
    cells_to_render = _expand_totals(
        [choices[i] for i in dict.fromkeys(selected)],
        choices if expansion_choices is None else expansion_choices,
    )
    for c in cells_to_render:
        rows.setdefault((c['item_id'], c['row_key']), []).append(c)
    for cells in rows.values():
        cells.sort(key=lambda c: c['column_index'])
        combined = _literal_block(cells, next_id)
        parts = [cells] if len(combined['text']) <= 1200 and len(combined['quantities']) <= 8 else [[c] for c in cells]
        for part in parts:
            result['blocks'].append(_literal_block(part, next_id))
            next_id += 1
    return result


def prepare_draft_tables(payload, schema):
    """The writer assigns source cell IDs to its own proposed question items."""
    payload, schema = deepcopy(payload), deepcopy(schema)
    choices = source_choices(payload['evidence'], item_scoped=False)
    if not choices:
        return payload, schema, []
    for record in payload['evidence']:
        record.pop('table_rows', None)
    payload['table_choices'] = [{k: v for k, v in c.items() if k not in ('item_id', '_source_text')}
                                for c in choices]
    # The item route schema owns selection fields; this adapter only supplies
    # the cell catalog and reserves output space for source reconstruction.
    # Reserve bounded space for literal restoration (at most eight selections).
    schema['properties']['blocks']['maxItems'] = 4
    return payload, schema, choices


def resolve_draft_tables(parsed, choices):
    if not choices:
        return parsed
    result = deepcopy(parsed)
    selections = result.pop('table_selections', [])
    items = {i['item_id'] for i in result['items'] if i['disposition'] == 'answer'}
    requirements = {i['item_id']: i.get('requirement', '') for i in result['items']}
    selected = []
    for selection in selections:
        n = selection['choice_id']
        if type(n) is not int or not 0 <= n < len(choices) or selection['item_id'] not in items:
            raise upstream_error()
        choice = {**choices[n], 'item_id': selection['item_id'],
                  '_total_requested': bool(TOTAL.search(requirements[selection['item_id']]))}
        if choice not in selected:
            selected.append(choice)
    owners = {c['item_id'] for c in selected}
    result['blocks'] = [b for b in result['blocks'] if b['item_id'] not in owners]
    result['table_choice_ids'] = list(range(len(selected)))
    if selected:
        return resolve_table_writer(result, selected, expansion_choices=choices)
    result.pop('table_choice_ids')
    return result


def _literal_row_matches(blocks, evidence):
    """Recognize exact server-rendered rows, not merely a model's table citation."""
    records = [{'source_units': source_units(e), 'table_rows': table_rows(e)} for e in evidence.values()]
    choices = source_choices(records, item_scoped=False)
    rows = {}
    for c in choices:
        rows.setdefault(c['row_key'], []).append(c)
    for block in blocks:
        for cells in rows.values():
            selected = [{**c, 'item_id': block.item_id} for c in cells
                        if f"{c['column']}: {c['value']}" in block.text]
            if not selected:
                continue
            selected.sort(key=lambda c: c['column_index'])
            restored = _literal_block(selected, block.block_id)
            if restored == block.model_dump():
                yield block, selected
                break


def literal_claim_ids(blocks, evidence):
    return [b.block_id for b, _ in _literal_row_matches(blocks, evidence)]


def table_display_rows(blocks, evidence):
    """Share identical literal row headings; retain differing source contexts.

    This changes final display only. Original reviewed blocks, quantities and
    citations remain intact. No equivalence of differently worded conditions
    is inferred, and different cells, units or source periods stay apart.
    Identical rows may answer multiple items; their coverage blocks stay intact.
    """
    groups, display = {}, {}
    for block, cells in _literal_row_matches(blocks, evidence):
        c = cells[0]
        unit = re.search(r'단위\s*[:：]\s*(' + UNIT + r')', c['unit'])
        if unit is None or block.placement != 'answer':
            continue
        metadata = evidence[c['evidence_ref']['chunk_id']].metadata
        scope = (metadata.document_id, metadata.version_id, metadata.index_revision,
                 metadata.effective_from, metadata.effective_until)
        # Include every selected field, including deadlines and return terms.
        fields = tuple((v['column'], v['value']) for v in cells)
        key = (scope, c['label'], fields, unit[1])
        groups.setdefault(key, []).append((block, c, metadata))
    for key, rows in groups.items():
        if len(rows) < 2:
            continue
        first, c, _ = rows[0]
        core = c['label'] + ' — ' + ' · '.join(f'{column}: {value}' for column, value in key[2])
        contexts = list(dict.fromkeys((r['title'], r['unit']) for _, r, _ in rows))
        if len(contexts) == 1:
            text = first.text
        else:
            # Conditions are quoted separately rather than silently unioned.
            text = core + '\n' + '\n'.join(dict.fromkeys(
                f"· {('PDF ' + str(m.page) + '쪽 / ') if m.page else ''}{r['title']}: {r['unit']}"
                for _, r, m in rows))
        display[first.block_id] = text
        display.update((b.block_id, None) for b, _, _ in rows[1:])
    return display


def prepare_item_routes(schema, table_choices):
    schema = deepcopy(schema)
    variants = [{'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['prose']}},
                 'required': ['kind'], 'additionalProperties': False}]
    if table_choices:
        variants.append({'type': 'object', 'properties': {
            'kind': {'type': 'string', 'enum': ['table']},
            'choice_ids': {'type': 'array', 'minItems': 1, 'maxItems': 8,
                           'items': {'type': 'integer', 'enum': list(range(len(table_choices)))}}},
            'required': ['kind', 'choice_ids'], 'additionalProperties': False})
    item = schema['$defs']['DraftItem']
    item['properties']['route'] = {'anyOf': variants}
    item.setdefault('required', []).append('route')
    return schema


def resolve_item_routes(parsed, table_choices):
    result = deepcopy(parsed)
    tables, literal_owners = [], set()
    item_ids = [i['item_id'] for i in result['items']]
    if len(set(item_ids)) != len(item_ids):
        raise upstream_error()
    for item in result['items']:
        route = item.pop('route', None)
        if route == {'kind': 'prose'}:
            continue
        if not isinstance(route, dict) or set(route) != {'kind', 'choice_ids'} or route['kind'] != 'table':
            raise upstream_error()
        ids = route['choice_ids']
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 8
                or any(type(n) is not int or not 0 <= n < len(table_choices) for n in ids)):
            raise upstream_error()
        tables.extend({'item_id': item['item_id'], 'choice_id': n} for n in dict.fromkeys(ids))
        literal_owners.add(item['item_id'])
    result['blocks'] = [b for b in result['blocks'] if b['item_id'] not in literal_owners]
    if table_choices:
        result['table_selections'] = tables
    return result
