"""Blend dense similarity with literal Korean term matches inside the permitted scope."""
import math
import re


def query_terms(query):
    terms = set()
    for word in re.findall(r'[가-힣]+|[a-zA-Z][a-zA-Z0-9_-]+|\d+(?:[,.]\d+)*', query.casefold()):
        if re.fullmatch(r'[가-힣]+', word):
            # Particles and compound nouns make whitespace tokenization too brittle.
            for width in range(3, min(6, len(word)) + 1):
                terms.update(word[i:i+width] for i in range(len(word)-width+1))
        else:
            terms.add(word)
    return sorted(terms)


def rank_hybrid(query, items, dense_positions, dense_values):
    """Caller MUST filter permissions/versions before supplying items and similarities."""
    texts = [''.join((e.metadata.title + '\n' + e.text).casefold().split()) for e in items]
    n = len(texts)
    average = sum(map(len, texts)) / max(1, n)
    lexical = [0.0] * n
    for term in query_terms(query):
        counts = [text.count(term) for text in texts]
        df = sum(c > 0 for c in counts)
        if not df:
            continue
        idf = math.log1p((n-df+0.5)/(df+0.5))
        for i, count in enumerate(counts):
            if count:
                denominator = count + 1.2 * (0.25 + 0.75 * len(texts[i]) / max(1, average))
                lexical[i] += idf * count * 2.2 / denominator
    maximum = max(lexical, default=0)
    if not maximum:
        return list(dense_positions)
    dense = {int(pos): float(value) for pos, value in zip(dense_positions, dense_values)}
    def score(i):
        cosine = max(-1.0, min(1.0, dense[i]))
        return 0.65 * (cosine + 1) / 2 + 0.35 * lexical[i] / maximum
    return sorted(range(n), key=lambda i: (score(i), dense[i]), reverse=True)
