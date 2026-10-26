import pytest

from app.generation.answer import public_text


@pytest.mark.parametrize('notice', [
    '도구 사용을 종료하고 초안을 작성하겠습니다.',
    '이제 도구 사용을 종료합니다.',
    '확보한 근거를 바탕으로 최종 답변을 작성하겠습니다.',
    '관련 문서를 검색하겠습니다.',
])
def test_remove_process_sentences_without_changing_facts(notice):
    answer = '보증금은 1,500천원입니다. 계약체결일로부터 3일 이내입니다.'
    assert public_text(notice + ' ' + answer) == answer
    assert public_text(answer + '\n' + notice) == answer


def test_keep_user_procedures_exceptions_and_original_formatting():
    text = '계약서를 확인하세요. 지급기한을 확인해야 합니다.\n\n예외: 가맹본부 요청 시 반환됩니다.'
    assert public_text(text) == text


def recurring_payment_rows(name='로열티', *, extra_amount=None):
    from dataclasses import replace
    from app.generation.model import CounselDraft
    from app.generation.table_evidence import prepare_table_writer, resolve_table_writer
    from tests.support import source, writer_payload
    records = [
        (24, '(부가세 포함)', ['구분', '지급대상', '지급기한'], [name, '가맹본부', '익월 5일 내 납부']),
        (54, '(단위:천원 부가가치세 포함, 매월납부, 소멸성)', ['기준', name, '비고'], ['매월', '275', '익월 5일 내 납부']),
    ]
    if extra_amount:
        records.append((55, records[1][1], records[1][2], ['매월', extra_amount, '익월 5일 내 납부']))
    blocks, evidence = [], {}
    for n, (page, unit, columns, row) in enumerate(records):
        title = '납부 기준'
        text = f'{title}\n{unit}\n- {row[0]}: ' + ' · '.join(f'{c} {v}' for c, v in zip(columns[1:], row[1:]))
        e = source(text, f'payment-{n}')
        e = replace(e, metadata=e.metadata.model_copy(update={'page': page}),
                    table={'schema': 'reviewed-cells-v1', 'reviewed': True, 'title': title,
                           'unit': unit, 'columns': columns, 'rows': [row]})
        _, _, choices = prepare_table_writer(writer_payload(e), CounselDraft.model_json_schema())
        parsed = resolve_table_writer({'blocks': [], 'category': 'franchise', 'table_choice_ids': [0, 1]}, choices)
        blocks.append(CounselDraft.model_validate(parsed).blocks[0].model_copy(update={'block_id': n}))
        evidence[e.metadata.chunk_id] = e
    return blocks, evidence


@pytest.mark.parametrize('name', ['로열티', '구독료'])
def test_recurring_payment_is_readable_only_after_review_without_mutating_facts(name):
    from app.generation.answer import compose
    from tests.support import assessment
    blocks, evidence = recurring_payment_rows(name)
    original = [b.model_dump() for b in blocks]
    before = compose(blocks, assessment(), 'franchise', evidence=evidence)
    result = compose(blocks, assessment(), 'franchise', complete_ids={'item-1'}, evidence=evidence)
    assert f'{name}: 매월 275천원' in result.answer
    assert result.answer.count('익월 5일 내 납부') == 1
    assert '가맹본부' in result.answer and '부가가치세 포함' in result.answer and '소멸성' in result.answer
    assert 'PDF 24쪽' in result.answer and 'PDF 54쪽' in result.answer
    assert result.status == before.status and result.used_chunk_ids == before.used_chunk_ids
    assert result.cited_units == before.cited_units
    assert [b.model_dump() for b in blocks] == original
    assert '매월 275천원' not in before.answer


def test_conflicting_periodic_amounts_keep_original_display():
    from app.generation.table_evidence import table_display_rows
    blocks, evidence = recurring_payment_rows(extra_amount='300')
    assert table_display_rows(blocks, evidence) == {}


def test_counts_in_currency_tables_are_not_rendered_as_payment_amounts():
    from app.generation.table_evidence import table_display_rows
    blocks, evidence = recurring_payment_rows('매장 수')
    assert table_display_rows(blocks, evidence) == {}


def test_unrecognized_prose_is_never_merged_into_payment_rows():
    from app.generation.table_evidence import table_display_rows
    blocks, evidence = recurring_payment_rows()
    blocks[0] = blocks[0].model_copy(update={'text': blocks[0].text + ' 별도 계약 조건입니다.'})
    display = table_display_rows(blocks, evidence)
    assert blocks[0].block_id not in display


def repeated_table_rows(*, second_unit='(단위: 천원, 부가세 포함)', second_value='3,300',
                        second_deadline='3일 이내'):
    from dataclasses import replace
    from app.generation.model import CounselDraft
    from app.generation.table_evidence import prepare_table_writer, resolve_table_writer
    from tests.support import source, writer_payload
    evidence, blocks = {}, []
    for n, unit, value, deadline in [(0, '(단위: 천원, 부가세 포함)', '3,300', '3일 이내'),
                                     (1, second_unit, second_value, second_deadline)]:
        title = '비용 기준'
        e = source(f'{title}\n{unit}\n- 교육비: 금액 {value} · 지급기한 {deadline}', f'table-{n}')
        e = replace(e, metadata=e.metadata.model_copy(update={'page': 19 if n == 0 else 54}),
            table={'schema': 'reviewed-cells-v1', 'reviewed': True, 'title': title, 'unit': unit,
                   'columns': ['구분', '금액', '지급기한'], 'rows': [['교육비', value, deadline]]})
        _, _, choices = prepare_table_writer(writer_payload(e), CounselDraft.model_json_schema())
        parsed = resolve_table_writer({'blocks': [], 'category': 'franchise', 'table_choice_ids': [0, 1]}, choices)
        blocks.append(CounselDraft.model_validate(parsed).blocks[0].model_copy(update={'block_id': n}))
        evidence[e.metadata.chunk_id] = e
    return blocks, evidence


def test_final_display_shares_literal_row_without_removing_blocks_or_citations():
    from app.generation.answer import compose
    from tests.support import assessment
    blocks, evidence = repeated_table_rows()
    before = [b.model_dump() for b in blocks]
    result = compose(blocks, assessment(), 'franchise', complete_ids={'item-1'}, evidence=evidence)
    assert result.answer.count('교육비 —') == 1
    assert result.answer.count('3,300') == 1
    assert result.status == 'answered'
    assert len(result.blocks) == len(result.cited_units) == 2
    assert result.used_chunk_ids == ['table-0', 'table-1']
    assert [b.model_dump() for b in blocks] == before
    # Before verification, both statements must still be visible to the judge.
    assert compose(blocks, assessment(), 'franchise', evidence=evidence).answer.count('교육비 —') == 2


def test_different_table_contexts_are_explicitly_kept_under_one_row():
    from app.generation.answer import compose
    from tests.support import assessment
    blocks, evidence = repeated_table_rows(second_unit='(단위:천원 부가가치세 포함, 즉시 소멸성)')
    result = compose(blocks, assessment(), 'franchise', complete_ids={'item-1'}, evidence=evidence)
    assert result.answer.count('교육비 —') == result.answer.count('3,300') == 1
    assert 'PDF 19쪽' in result.answer and 'PDF 54쪽' in result.answer
    assert '(단위: 천원, 부가세 포함)' in result.answer
    assert '(단위:천원 부가가치세 포함, 즉시 소멸성)' in result.answer


@pytest.mark.parametrize('difference', ['amount', 'unit', 'deadline', 'period', 'prose'])
def test_different_values_scopes_or_unbound_prose_are_not_grouped(difference):
    from datetime import timedelta
    from dataclasses import replace
    from app.generation.table_evidence import table_display_rows
    options = {'amount': {'second_value': '4,400'}, 'unit': {'second_unit': '(단위: 원, 부가세 포함)'},
               'deadline': {'second_deadline': '5일 이내'}}
    blocks, evidence = repeated_table_rows(**options.get(difference, {}))
    if difference == 'period':
        e = evidence['table-1']
        evidence['table-1'] = replace(e, metadata=e.metadata.model_copy(update={
            'effective_from': e.metadata.effective_from + timedelta(days=1)}))
    elif difference == 'prose':
        blocks[1] = blocks[1].model_copy(update={'text': blocks[1].text + ' 별도 설명.'})
    assert table_display_rows(blocks, evidence) == {}


def test_same_literal_fact_can_cover_multiple_items_without_repeating_display():
    from app.generation.answer import compose
    from tests.support import assessment, item
    blocks, evidence = repeated_table_rows()
    blocks[1] = blocks[1].model_copy(update={'item_id': 'other-item'})
    result = compose(blocks, assessment(items=[item(), item('other-item')]), 'franchise',
                     complete_ids={'item-1', 'other-item'}, evidence=evidence)
    assert result.status == 'answered' and result.answer.count('교육비 —') == 1
    assert len(result.blocks) == len(result.cited_units) == 2
