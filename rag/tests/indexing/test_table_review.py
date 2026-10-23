"""표 검수 기록(data/structured/table_reviews.json) — QA-03."""
import copy
import json

import pymupdf
import pytest

from app.indexing.extract import extract_pdf
from app.indexing.job import run_index_job
from app.indexing.table_review import REVIEW_MARK, apply_reviews, load_reviews, validate

from .conftest import REPO, SOURCE

PAIK = SOURCE / "빽다방_정보공개서_v2025-11-21.pdf"
REVIEWS = REPO / "data" / "structured" / "table_reviews.json"


@pytest.fixture(scope="module")
def raw():
    return {i + 1: p.get_text() for i, p in enumerate(pymupdf.open(PAIK))}


@pytest.fixture
def review():
    import hashlib
    return copy.deepcopy(load_reviews(hashlib.sha256(PAIK.read_bytes()).hexdigest(), REVIEWS))


def table(review, title):
    return next(t for t in review["tables"] if t["title"] == title)


def test_all_committed_reviews_match_source(review, raw):
    assert review is not None, "정보공개서 sha256에 맞는 검수 기록이 없음 — 파일이 바뀌었으면 다시 검수"
    for t in review["tables"]:
        assert validate(t, raw) == [], t["title"]


def test_changed_number_is_rejected(review, raw):
    t = table(review, "4)기타")
    t["rows"][0][2] = "16,270"  # 원문은 16,720
    assert any("원문에 없는 값" in e for e in validate(t, raw))


def test_added_text_in_display_value_is_rejected(review, raw):
    t = table(review, "2. 영업 중의 부담")
    cell = next(c for row in t["rows"] for c in row if isinstance(c, dict))
    cell["text"] += " 본사 부담"  # 원문 조각에 없는 글자
    assert any("표시 값이 원문 조각과 다름" in e for e in validate(t, raw))


def test_sum_check(review, raw):
    t = table(review, "4)기타")
    t["rows"][1][2] = "4,167"  # 원문에 있는 숫자(3.3㎡당)지만 합계가 맞지 않음
    assert any("합계 불일치" in e for e in validate(t, raw))


def test_apply_marks_only_covered_pages(review, raw):
    for t in review["tables"]:          # 29쪽을 표시 해제 쪽에서 빼면(그 쪽 표를 다 옮기지 못한 경우) 그대로 남는다
        t["covers"] = [n for n in t.get("covers", t["pages"]) if n != 29]
    pages, _ = extract_pdf(PAIK, {19, 20, 21, 29, 30})
    summary = apply_reviews(pages, raw, review)
    flags = {p.page: p.flags for p in pages}
    assert "table_reviewed" in flags[20] and "table_needs_review" not in flags[20]
    assert "table_needs_review" in flags[29]
    p20 = next(p for p in pages if p.page == 20)
    assert p20.lines[0].startswith(REVIEW_MARK)
    assert "- 인테리어 (점포 내장공사): 지급대상자 협력업체 · 금액 16,720" in "\n".join(p20.lines)
    assert summary["rejected"] == []


def test_rejected_review_keeps_flag(review, raw):
    table(review, "4)기타")["rows"][0][2] = "99,999"
    pages, _ = extract_pdf(PAIK, {20, 21})
    summary = apply_reviews(pages, raw, review)
    assert [r["title"] for r in summary["rejected"]] == ["4)기타"]
    assert all("table_needs_review" in p.flags for p in pages)


def test_other_file_gets_no_review():
    assert load_reviews("0" * 64, REVIEWS) is None


def test_index_job_uses_review_and_revision_tracks_it(make_request, roots, embedder, tmp_path, monkeypatch):
    req = make_request(src_name=PAIK.name, key="documents/paik/v1.pdf", category="franchise",
                       material_type="public_disclosure",
                       indexing_policy={"include_pages": [20, 21, 48], "exclude_pages": [],
                                        "legal_temporal_reviewed": False})
    monkeypatch.setenv("TABLE_REVIEW_FILE", str(REVIEWS))
    a = run_index_job(req, document_root=roots[0], index_root=roots[1], embedder=embedder)
    assert a["status"] == "succeeded", a
    recs = [json.loads(l) for l in (roots[1] / "revisions" / a["index_revision"] / "chunks.jsonl").read_text().splitlines()]
    assert all("table_needs_review" not in r["review"]["quality_flags"] for r in recs)
    assert any(r["text"].startswith(REVIEW_MARK) for r in recs)

    # 검수 기록이 바뀌면 같은 파일이라도 새 revision
    changed = json.loads(REVIEWS.read_text(encoding="utf-8"))
    changed["documents"][0]["reviewed_on"] = "2026-10-24"
    other = tmp_path / "reviews.json"
    other.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("TABLE_REVIEW_FILE", str(other))
    b = run_index_job(dict(req, job_id="job-test-002"), document_root=roots[0], index_root=roots[1], embedder=embedder)
    assert b["status"] == "succeeded" and b["index_revision"] != a["index_revision"]


def test_every_table_renders(review):
    from app.indexing.table_review import render
    for t in review["tables"]:
        lines = render(t)
        assert (lines == []) == (not t.get("rows")), t["title"]
        assert all(isinstance(l, str) and l for l in lines)


def test_key_disclosure_tables_present(review):
    """시연·평가에 자주 나올 표가 행 단위로 들어 있다."""
    text = "\n".join("\n".join(__import__("app.indexing.table_review", fromlist=["render"]).render(t))
                     for t in review["tables"])
    assert "연도 2023 · 연초 1228 · 신규개점 241" in text or "- 2023: 연초 1228 · 신규개점 241" in text  # 가맹점 수
    assert "매출액 388,087,962" in text                                       # 재무 현황
    assert "- 전체: 2024년말가맹점수 1712 · 연간 평균매출액 324,486" in text      # 평균 매출액
    assert "권장가격 3,700" in text                                            # 메뉴 권장가격
    assert "- 매월: 로열티 275 · 비고 익월 5일 내 납부" in text                    # 로열티(별첨 1-3)


def test_all_documents_reviews_valid():
    """정보공개서 외 문서(분쟁 별표·계약서·법령의 본문 줄 상자) 기록도 원문과 맞는다."""
    data = json.loads(REVIEWS.read_text(encoding="utf-8"))
    for doc in data["documents"]:
        pdf = pymupdf.open(REPO / "data" / doc["path"])
        assert hashlib_sha(REPO / "data" / doc["path"]) == doc["sha256"], doc["path"]
        raw = {i + 1: p.get_text() for i, p in enumerate(pdf)}
        for t in doc["tables"]:
            assert validate(t, raw) == [], (doc["local_key"], t["pages"])


def hashlib_sha(path):
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()
