import json
import os

import pytest
from pydantic import ValidationError

from app.indexing.job import JobConflict, run_index_job

from .conftest import FakeEmbedder


def run(req, roots, embedder):
    doc_root, index_root = roots
    return run_index_job(req, document_root=doc_root, index_root=index_root, embedder=embedder)


def revisions(roots):
    d = roots[1] / "revisions"
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def read_chunks(roots, rev):
    lines = (roots[1] / "revisions" / rev / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(l) for l in lines]


def test_success_writes_revision_matching_contract(make_request, roots, embedder, contract):
    req = make_request()
    contract("IndexJobRequest", req)
    job = run(req, roots, embedder)
    contract("IndexJob", job)
    assert job["status"] == "succeeded" and job["error_code"] is None
    rev = job["index_revision"]
    assert revisions(roots) == [rev]
    files = {p.name for p in (roots[1] / "revisions" / rev).iterdir()}
    assert files == {"index.faiss", "chunks.jsonl", "build-manifest.json", "quality-report.json"}
    chunks = read_chunks(roots, rev)
    assert len(chunks) == job["chunk_count"] > 0
    for c in chunks:
        contract("IndexedChunkMetadata", c["metadata"])
        assert c["metadata"]["index_revision"] == rev
        assert c["metadata"]["page"] not in (1, 2)  # 제외 쪽은 들어가지 않음
    articles = {c["metadata"]["article"] for c in chunks}
    assert "제17조" in articles  # 청약철회 조문이 조 단위로 남음


def test_same_job_same_input_returns_existing(make_request, roots, embedder):
    req = make_request()
    first = run(req, roots, embedder)
    again = run(req, roots, FakeEmbedder(fail=True))  # 다시 실행하지 않으므로 임베딩 실패와 무관
    assert again == first


def test_same_job_different_input_conflicts(make_request, roots, embedder):
    run(make_request(), roots, embedder)
    with pytest.raises(JobConflict):
        run(make_request(title="다른 제목"), roots, embedder)


def test_same_input_new_job_reuses_revision_without_overwrite(make_request, roots, embedder):
    a = run(make_request(), roots, embedder)
    path = roots[1] / "revisions" / a["index_revision"] / "index.faiss"
    mtime = path.stat().st_mtime_ns
    b = run(make_request(job_id="job-test-002"), roots, embedder)
    assert b["index_revision"] == a["index_revision"]
    assert path.stat().st_mtime_ns == mtime


def test_changed_policy_creates_new_revision(make_request, roots, embedder):
    a = run(make_request(), roots, embedder)
    policy = {"exclude_pages": [1, 2, 6], "legal_temporal_reviewed": False}
    b = run(make_request(job_id="job-test-002", indexing_policy=policy), roots, embedder)
    assert b["index_revision"] != a["index_revision"]
    assert len(revisions(roots)) == 2


def test_checksum_mismatch_fails_without_artifacts(make_request, roots, embedder):
    req = make_request()
    req["source"]["sha256"] = "0" * 64
    job = run(req, roots, embedder)
    assert (job["status"], job["error_code"]) == ("failed", "CHECKSUM_MISMATCH")
    assert revisions(roots) == []


@pytest.mark.parametrize("key", ["documents/../escape.pdf", "documents/a/./b.pdf", "documents//x.pdf"])
def test_storage_key_cannot_leave_root(make_request, roots, embedder, key):
    req = make_request()
    (roots[0] / "escape.pdf").write_bytes(b"%PDF-1.4")
    req["source"]["storage_key"] = key
    job = run(req, roots, embedder)
    assert job["error_code"] == "INVALID_STORAGE_KEY"


def test_symlink_outside_root_rejected(make_request, roots, embedder, tmp_path):
    req = make_request()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes((roots[0] / req["source"]["storage_key"]).read_bytes())
    link = roots[0] / "documents" / "link.pdf"
    os.symlink(outside, link)
    req["source"]["storage_key"] = "documents/link.pdf"
    assert run(req, roots, embedder)["error_code"] == "INVALID_STORAGE_KEY"


def test_missing_file(make_request, roots, embedder):
    req = make_request()
    req["source"]["storage_key"] = "documents/none.pdf"
    assert run(req, roots, embedder)["error_code"] == "SOURCE_NOT_FOUND"


@pytest.mark.parametrize("policy", [
    {"exclude_pages": [7], "legal_temporal_reviewed": False},                       # 범위 밖
    {"include_pages": [3, 4], "exclude_pages": [4], "legal_temporal_reviewed": False},  # 충돌
    {"exclude_pages": [1, 2, 3, 4, 5, 6], "legal_temporal_reviewed": False},       # 남는 쪽 없음
])
def test_page_policy_errors(make_request, roots, embedder, policy):
    job = run(make_request(indexing_policy=policy), roots, embedder)
    assert job["error_code"] == "PAGE_POLICY_INVALID"
    assert revisions(roots) == []


def test_embedding_not_configured_fails(make_request, roots, monkeypatch):
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    job = run(make_request(), roots, None)
    assert job["error_code"] == "EMBEDDING_NOT_CONFIGURED"
    assert revisions(roots) == []


def test_failed_build_keeps_existing_revision_and_leaves_no_temp(make_request, roots, embedder):
    ok = run(make_request(), roots, embedder)
    policy = {"exclude_pages": [1, 2, 6], "legal_temporal_reviewed": False}
    bad = run(make_request(job_id="job-test-002", indexing_policy=policy), roots, FakeEmbedder(fail=True))
    assert (bad["status"], bad["error_code"]) == ("failed", "EMBEDDING_FAILED")
    assert revisions(roots) == [ok["index_revision"]]  # 임시 디렉터리도 남지 않음


@pytest.mark.parametrize("change", [
    {"effective_from": "2026-07-21T00:00:00"},  # 시간대 없음
    {"unknown_field": 1},                        # 계약 밖 필드
    {"material_type": "adapted_reference"},      # 계약 밖 열거값
    {"indexing_policy": {"include_pages": None, "exclude_pages": [], "legal_temporal_reviewed": False}},  # 선택 필드 null
])
def test_request_schema_is_strict(make_request, roots, embedder, change):
    with pytest.raises(ValidationError):
        run(make_request(**change), roots, embedder)


def test_future_article_versions_get_separate_periods(make_request, roots, embedder):
    req = make_request(src_name="식품위생법_v2026-10-08.pdf", key="documents/food/v1.pdf",
                       category="hygiene", effective_from="2026-10-08T00:00:00+09:00",
                       indexing_policy={"exclude_pages": [1, 2, 3, 4, 5, 6], "legal_temporal_reviewed": False})
    job = run(req, roots, embedder)
    assert job["status"] == "succeeded", job
    chunks = [c["metadata"] for c in read_chunks(roots, job["index_revision"])]
    art = [c for c in chunks if c["article"] == "제12조의2"]
    current = [c for c in art if c["effective_until"]]
    future = [c for c in art if c["effective_from"].startswith("2026-12-31")]
    assert current and future
    assert current[0]["effective_until"] == future[0]["effective_from"] == "2026-12-31T00:00:00+09:00"
    assert current[0]["effective_from"] == "2026-10-08T00:00:00+09:00"
    assert len({c["chunk_id"] for c in chunks}) == len(chunks)


def test_restart_marks_running_jobs_interrupted(make_request, roots, embedder, contract):
    from app.indexing.job import JobStore
    store = JobStore(roots[1])
    done = run(make_request(), roots, embedder)
    stuck = dict(done, job_id="job-stuck", status="running", index_revision=None, chunk_count=0)
    store.put("job-stuck", {"request_hash": "x", "job": stuck})
    tmp = roots[1] / "revisions" / ".tmp-idx-left-over"
    tmp.mkdir()
    assert store.recover_interrupted() == ["job-stuck"]
    job = store.get("job-stuck")["job"]
    contract("IndexJob", job)
    assert (job["status"], job["error_code"]) == ("failed", "INTERRUPTED")
    assert store.get(done["job_id"])["job"] == done  # 끝난 작업은 그대로
    assert not tmp.exists()


def test_chunk_id_never_exceeds_contract_limit():
    from app.indexing.job import chunk_id
    rev = "idx-" + "a" * 20
    long_a, long_b = "p1." + "x" * 120, "p1." + "x" * 119 + "y"
    a, b = chunk_id(rev, long_a), chunk_id(rev, long_b)
    assert len(a) <= 100 and len(b) <= 100 and a != b
    assert chunk_id(rev, "art-17") == "chk-aaaaaaaaaaaa-art-17"


def test_background_returns_queued_then_resubmit_reports_progress(make_request, roots, embedder, contract):
    """백엔드(C)는 같은 job_id로 다시 보내 상태를 받는다. 첫 응답은 바로 queued."""
    pending = []
    req = make_request()
    first = run_index_job(req, document_root=roots[0], index_root=roots[1], embedder=embedder,
                          background=lambda fn, *a: pending.append((fn, a)))
    contract("IndexJob", first)
    assert first["status"] == "queued" and revisions(roots) == []
    assert run(req, roots, embedder)["status"] == "queued"  # 재제출: 같은 내용 → 현재 상태
    fn, args = pending.pop()
    fn(*args)  # 백그라운드 실행
    done = run(req, roots, embedder)
    assert done["status"] == "succeeded" and done["created_at"] == first["created_at"]
    assert revisions(roots) == [done["index_revision"]]


def test_backend_style_request_is_accepted(make_request, roots, embedder, contract):
    """C 백엔드 syncJob이 보내는 형태: include_pages 생략, url null, effective_from +09:00."""
    req = make_request()
    req["brand_id"] = "brand-demo-a"
    req["original_source"]["url"] = None
    assert "include_pages" not in req["indexing_policy"]
    contract("IndexJobRequest", req)
    assert run(req, roots, embedder)["status"] == "succeeded"


def test_search_context_is_embedded_separately_from_citation_text(make_request, roots):
    import hashlib
    from app.indexing.job import validate_revision, IndexJobFailed
    from app.indexing.models import IndexJobRequest
    class Capture(FakeEmbedder):
        def embed(self, texts):
            self.inputs = list(texts)
            return super().embed(texts)
    req = make_request()
    model = Capture()
    job = run(req, roots, model)
    records = read_chunks(roots, job['index_revision'])
    assert model.inputs == [r['search_text'] for r in records]
    assert any(r['search_text'] != r['text'] for r in records)
    assert all(hashlib.sha256(r['text'].encode()).hexdigest() == r['metadata']['content_sha256'] for r in records)
    assert all(hashlib.sha256(r['search_text'].encode()).hexdigest() == r['search_sha256'] for r in records)
    directory = roots[1] / 'revisions' / job['index_revision']
    records[0]['search_text'] += '허위 검색 문맥'
    (directory/'chunks.jsonl').write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in records)+'\n')
    with pytest.raises(IndexJobFailed):
        validate_revision(directory, IndexJobRequest.model_validate(req), job['index_revision'])
