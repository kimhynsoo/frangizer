"""조문 단위 시점 검수(data/structured/legal_reviews.json)."""
import json

from app.indexing.chunking import Chunk
from app.indexing.job import run_index_job
from app.indexing.legal_review import check_article, reviewed_articles

from .conftest import REPO

REVIEWS = REPO / "data" / "structured" / "legal_reviews.json"


def chunk(key, article, text="본문", **kw):
    return Chunk(section_key=key, page=1, article=article, heading=article or key, text=text, **kw)


def test_committed_reviews_pass(make_request, roots, embedder, monkeypatch):
    monkeypatch.setenv("LEGAL_REVIEW_FILE", str(REVIEWS))
    req = make_request(src_name="가맹사업법_v2026-10-02.pdf", key="documents/fa/v1.pdf", category="franchise",
                       effective_from="2026-10-02T00:00:00+09:00",
                       indexing_policy={"exclude_pages": [1, 2, 3], "legal_temporal_reviewed": False})
    job = run_index_job(req, document_root=roots[0], index_root=roots[1], embedder=embedder)
    assert job["status"] == "succeeded", job
    recs = [json.loads(l) for l in (roots[1] / "revisions" / job["index_revision"] / "chunks.jsonl").read_text().splitlines()]
    reviewed = {r["metadata"]["article"] for r in recs if r["review"]["legal_temporal_reviewed"]}
    assert reviewed == {"제12조의2"}
    r = next(r for r in recs if r["metadata"]["article"] == "제12조의2")
    assert r["review"]["legal_temporal_scope"] == "article"
    other = next(r for r in recs if r["metadata"]["article"] == "제12조")
    assert other["review"]["legal_temporal_reviewed"] is False and other["review"]["legal_temporal_scope"] is None


def test_future_version_needs_confirmation():
    chunks = [chunk("art-5", "제5조", effective_until="2027-01-01"),
              chunk("art-5-from20270101", "제5조", effective_from="2027-01-01")]
    assert check_article(chunks, {"article": "제5조"})[0] is False
    assert check_article(chunks, {"article": "제5조", "future_versions_confirmed": True})[0] is True


def test_unresolved_temporal_note_is_rejected():
    chunks = [chunk("art-7", "제7조", temporal_note="시행일 2027-01-01 표기(제7조제2항)")]
    ok, ev = check_article(chunks, {"article": "제7조", "future_versions_confirmed": True})
    assert not ok and "자동 판단 못 한 시행일 표기가 있음" in ev["problems"]


def test_addenda_mention_needs_check_and_ignores_other_articles():
    chunks = [chunk("art-3", "제3조"), chunk("art-3-2", "제3조의2"),
              chunk("add1-art-2", "부칙1 제2조", text="제3조의2의 개정규정은 2027년부터 적용한다.")]
    assert check_article(chunks, {"article": "제3조"})[0] is True          # '제3조의2' 언급은 제3조가 아님
    ok, ev = check_article(chunks, {"article": "제3조의2"})
    assert not ok and ev["addenda_mentions"] == ["add1-art-2"]
    assert check_article(chunks, {"article": "제3조의2", "addenda_checked": True})[0] is True


def test_missing_article_rejected():
    ok, summary = reviewed_articles([chunk("art-1", "제1조")], {"articles": [{"article": "제99조"}]})
    assert ok == set() and summary["rejected"][0]["problems"] == ["조문이 없음"]
