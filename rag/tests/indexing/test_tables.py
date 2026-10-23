"""소비자분쟁해결기준 별표의 3칸 표(분쟁유형 | 해결기준 | 비고)를 행 짝으로 추출하는지 확인."""
import json

from app.indexing.extract import PAIR_MARK, extract_pdf
from app.indexing.job import run_index_job

from .conftest import SOURCE

ANNEX = SOURCE / "소비자분쟁해결기준_별표_v2025-12-18.pdf"


def pairs(lines):
    return {(a, b[len(PAIR_MARK):]) for a, b in zip(lines, lines[1:]) if b.startswith(PAIR_MARK)}


def test_rows_paired_as_in_pdf():
    (page,), _ = extract_pdf(ANNEX, {122})
    assert "table_rows_paired" in page.flags
    got = pairs(page.lines)
    # PDF 122쪽(외식업) 같은 높이의 행. 칸 단위로 읽으면 이 짝이 순서로만 남는다.
    assert ("- 약정이용시점부터 1시간 전 이후 계약해제", "ㅇ 예약보증금의 25% 환급") in got
    assert ("- 약정이용시점부터 24시간전 이후~1시간전", "ㅇ 예약보증금의 50% 환급") in got
    assert ("- 예약부도", "ㅇ 예약보증금 전액을 위약금으로 봄") in got
    assert "[비고]" in page.lines  # 비고는 짝과 섞지 않고 따로


def test_chunks_keep_pairs_and_flags(make_request, roots, embedder):
    req = make_request(src_name=ANNEX.name, key="documents/annex/v1.pdf", category="consumer",
                       indexing_policy={"include_pages": [121, 122, 2], "exclude_pages": [],
                                        "legal_temporal_reviewed": False})
    job = run_index_job(req, document_root=roots[0], index_root=roots[1], embedder=embedder)
    assert job["status"] == "succeeded", job
    recs = [json.loads(l) for l in (roots[1] / "revisions" / job["index_revision"] / "chunks.jsonl").read_text().splitlines()]
    for r in recs:
        body = r["text"].split("\n")
        start = 1 if r["section"]["part"] > 1 else 0  # 나눈 조각의 첫 줄은 '(이어서)' 제목
        assert not body[start].startswith(PAIR_MARK), r["metadata"]["section_key"]  # 짝 사이에서 끊지 않음
        assert r["review"]["legal_temporal_reviewed"] is False
    by_page = {r["metadata"]["page"]: r["review"]["quality_flags"] for r in recs}
    assert "table_rows_paired" in by_page[122]
    assert "table_needs_review" in by_page[2]  # 품목 목록 표는 짝을 못 지어 검수 필요로 남음
    parts = [r for r in recs if r["section"]["parent_section_key"] == "p121"]
    assert [r["section"]["part"] for r in parts] == list(range(1, parts[0]["section"]["parts"] + 1))
