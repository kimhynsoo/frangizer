"""Korean morphological BM25 and rank fusion, over already-authorized rows only."""
import math
import re
from collections import Counter
from functools import lru_cache
from threading import Lock

TOKENIZER_LOCK = Lock()
STOP_WORDS = frozenset({'것', '수', '등', '경우', '문서', '자료', '질문', '답변', '확인', '무엇'})


@lru_cache(maxsize=1)
def _tokenizer():
    from kiwipiepy import Kiwi
    return Kiwi(num_workers=1)


@lru_cache(maxsize=8192)
def text_terms(text):
    with TOKENIZER_LOCK:
        tokens = _tokenizer().tokenize(text.casefold())
    terms = [t.form.casefold() for t in tokens
             if t.tag in {'NNG', 'NNP', 'SL', 'SN', 'XR'} and t.form not in STOP_WORDS]
    terms.extend(re.findall(r'제\d+조(?:의\d+)?|[a-z][a-z0-9_-]*(?:\.\d+)+', text.casefold()))
    return tuple(terms)


def query_terms(query):
    terms = set(text_terms(query))
    # Everyday verbs often disappear from noun-based BM25, while manuals use
    # nominal headings. Expand query vocabulary only, never source facts.
    for phrase, equivalent in (('끝나', '종료'), ('돌려받', '반환'), ('돌려줘', '반환')):
        if phrase in query:
            terms.add(equivalent)
    return sorted(terms)


@lru_cache(maxsize=8192)
def _frequencies(text):
    return Counter(text_terms(text))


def bm25_scores(query, items):
    documents = [_frequencies(e.metadata.title + '\n' + e.search_text) for e in items]
    lengths = [sum(d.values()) for d in documents]
    n = len(items)
    average = sum(lengths) / max(1, n)
    scores = [0.0] * n
    for term in query_terms(query):
        counts = [d[term] for d in documents]
        df = sum(c > 0 for c in counts)
        if not df:
            continue
        idf = math.log1p((n - df + 0.5) / (df + 0.5))
        for i, tf in enumerate(counts):
            if tf:
                scores[i] += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * lengths[i] / max(1, average)))
    return scores


def reciprocal_rank_fusion(rankings, *, k=60):
    scores = {}
    for ranking in rankings:
        for rank, pos in enumerate(dict.fromkeys(ranking), 1):
            scores[pos] = scores.get(pos, 0.0) + 1 / (k + rank)
    return sorted(scores, key=lambda pos: scores[pos], reverse=True)


def rank_hybrid(query, items, dense_positions, dense_values, *, prefetch_k=30):
    dense = [int(p) for p in dense_positions[:prefetch_k]]
    lexical = bm25_scores(query, items)
    keywords = sorted((i for i, score in enumerate(lexical) if score > 0),
                      key=lambda i: lexical[i], reverse=True)[:prefetch_k]
    return reciprocal_rank_fusion([dense, keywords]) if keywords else dense
