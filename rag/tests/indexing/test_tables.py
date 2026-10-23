"""소비자분쟁해결기준 별표의 3칸 표(분쟁유형 | 해결기준 | 비고)를 행 짝으로 추출하는지 확인."""
import json
from collections import Counter

from app.indexing.extract import PAIR_MARK, extract_pdf
from app.indexing.chunking import chunk_document
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


def test_disclosure_prose_is_separate_from_unreviewed_cells():
    (page,), _ = extract_pdf(SOURCE / '빽다방_정보공개서_v2025-11-21.pdf', {19})
    assert [r.kind for r in page.regions] == ['prose', 'table', 'table']
    # Source text remains unmodified; serialized table rows may repeat merged cells.
    assert '3,300' in page.text
    chunks, _ = chunk_document([page], 'franchise', 'public_disclosure')
    prose = next(c for c in chunks if c.section_key == 'p19.prose')
    assert '임대관련 비용' in prose.text
    assert '3,300' not in prose.text
    assert 'table_needs_review' not in prose.quality_flags
    tables = [c for c in chunks if '.table' in c.section_key]
    assert len(tables) >= 2
    assert all('table_needs_review' in c.quality_flags for c in tables)
    assert any('3,300' in c.text for c in tables)


def test_region_flags_survive_index_artifacts(make_request, roots, embedder):
    req = make_request(src_name='빽다방_정보공개서_v2025-11-21.pdf', category='franchise',
                       material_type='public_disclosure',
                       indexing_policy={'include_pages': [19], 'exclude_pages': [], 'legal_temporal_reviewed': False})
    job = run_index_job(req, document_root=roots[0], index_root=roots[1], embedder=embedder)
    assert job['status'] == 'succeeded', job
    records = [json.loads(line) for line in (roots[1] / 'revisions' / job['index_revision'] / 'chunks.jsonl').read_text().splitlines()]
    assert len({r['metadata']['chunk_id'] for r in records}) == len(records)
    for r in records:
        assert r['metadata']['page'] == 19
        assert ('table_needs_review' in r['review']['quality_flags']) == ('.table' in r['metadata']['section_key'])


def test_uncertain_table_boundaries_keep_page_warning(monkeypatch):
    from app.indexing import extract
    monkeypatch.setattr(extract, '_layout_lines', lambda _: [(5, 15, '경계를 가로지르는 줄', 25)])
    assert extract.separate_table_regions(None, ['경계를 가로지르는 줄'], [(10, 10, 20, 20)]) == []
    monkeypatch.setattr(extract, '_layout_lines', lambda _: [(12, 15, '일부만 추출', 18)])
    assert extract.separate_table_regions(None, ['일부만 추출', '누락된 원문'], [(10, 10, 20, 20)]) == []


def test_review_binds_source_and_cells_and_changes_revision_input(tmp_path, monkeypatch):
    from app.indexing.tables import extraction_signature, table_digest, reviews_for
    path = tmp_path / 'reviews.json'
    monkeypatch.setenv('TABLE_REVIEW_FILE', str(path))
    table = {'source_sha256': 'a'*64, 'page': 19, 'bbox': [1,2,3,4], 'engine': 'test',
             'rows': [['항목','금액'], ['교육비','3,300']], 'parser_version': 'test'}
    sha = table_digest(table)
    before = extraction_signature(table['source_sha256'])
    path.write_text(json.dumps({'reviews': [{'source_sha256': 'a'*64, 'table_sha256': sha,
                                            'reviewed': True, 'reviewed_by': 'test', 'reviewed_at': '2026-10-23'}]}))
    assert reviews_for('b'*64) == []
    assert extraction_signature('a'*64) != before
    table['rows'][1][1] = '9,999'
    assert table_digest(table) != sha
