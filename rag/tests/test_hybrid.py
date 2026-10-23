from dataclasses import replace

from app.retrieval.hybrid import rank_hybrid
from tests.support import fixture_evidence


def test_literal_compound_noun_overrides_generic_dense_match():
    base = fixture_evidence()
    unrelated = replace(base, text='제54조 과징금의 납부기한 연기 및 분할 납부')
    specific = replace(base, text='이행보증금 금액 지급기한 계약체결일로부터 3일 이내')
    ranked = rank_hybrid('계약이행보증금은 언제까지 납부해야 하나요?', [unrelated, specific], [0, 1], [0.8, 0.5])
    assert ranked[0] == 1


def test_absent_literal_terms_preserve_dense_ranking():
    items = [replace(fixture_evidence(), text='alpha'), replace(fixture_evidence(), text='beta')]
    assert rank_hybrid('없는검색용어', items, [1, 0], [0.8, 0.4]) == [1, 0]
