from dataclasses import replace

from app.retrieval.hybrid import rank_hybrid, query_terms, reciprocal_rank_fusion, bm25_scores
from tests.support import fixture_evidence


def test_literal_compound_noun_is_retained_for_reranking():
    base = fixture_evidence()
    unrelated = replace(base, text='제54조 과징금의 납부기한 연기 및 분할 납부')
    specific = replace(base, text='이행보증금 금액 지급기한 계약체결일로부터 3일 이내')
    ranked = rank_hybrid('계약이행보증금은 언제까지 납부해야 하나요?', [unrelated, specific], [0, 1], [0.8, 0.5])
    assert set(ranked) == {0, 1}
    assert bm25_scores('계약이행보증금은 언제까지 납부해야 하나요?', [unrelated, specific])[1] > bm25_scores('계약이행보증금은 언제까지 납부해야 하나요?', [unrelated, specific])[0]


def test_absent_literal_terms_preserve_dense_ranking():
    items = [replace(fixture_evidence(), text='alpha'), replace(fixture_evidence(), text='beta')]
    assert rank_hybrid('없는검색용어', items, [1, 0], [0.8, 0.4]) == [1, 0]


def test_short_korean_nouns_and_article_identifiers_survive():
    assert {'우유', '이물', '직원', '위생', '교육', '제41조'} <= set(query_terms('우유에 이물, 직원의 위생교육과 제41조'))


def test_rrf_agreement_and_score_scale_independence():
    assert reciprocal_rank_fusion([[1, 2, 0], [2, 0, 1]])[0] == 2
    items = [replace(fixture_evidence(), text='제품 우유 이물혼입'),
             replace(fixture_evidence(), text='세탁업 배상 기준')]
    a = rank_hybrid('우유 이물', items, [1, 0], [0.8, 0.4])
    b = rank_hybrid('우유 이물', items, [1, 0], [800, 400])
    assert a == b and bm25_scores('우유 이물', items)[0] > bm25_scores('우유 이물', items)[1]
