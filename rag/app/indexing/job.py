"""승인 판본 색인 작업 (POST /internal/v1/index-jobs의 처리 본체).

라우터·인증(X-Internal-Token)·백그라운드 실행은 B가 rag/app/api/index_jobs.py에서 연결한다.
이 모듈은 다음만 책임진다.

1. 입력 검증 → 저장 키를 허용 루트 안의 실제 파일로 해석
2. sha256 대조 (불일치 CHECKSUM_MISMATCH)
3. 쪽 정책 검증 → 추출 → 분할 → 조각 메타데이터
4. 임베딩 → 새 index_revision 디렉터리를 임시 경로에 작성 → 검증 → 원자적 이동
5. 작업 기록(job_id 멱등: 같은 내용은 기존 결과, 다른 내용은 JobConflict)

색인 성공은 배포가 아니다. published·적용일 판단은 Java가 한다.
실패하면 임시 산출물을 지우고 기존 revision은 건드리지 않는다.
"""
import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import faiss
import numpy as np
from pydantic import ValidationError

from .chunking import CHUNKER_VERSION, chunk_document
from .embedding import EmbeddingError, load_embedder
from .extract import ExtractionError, extract_pdf, extract_text
from .models import IndexedChunkMetadata, IndexJob, IndexJobRequest

KST = timezone(timedelta(hours=9))


def _kst_start(day):
    """'YYYY-MM-DD' → 그 날 0시(KST)."""
    return datetime.fromisoformat(day).replace(tzinfo=KST)


class IndexJobFailed(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class JobConflict(Exception):
    """같은 job_id에 다른 내용 → 라우터에서 409."""


def _now():
    return datetime.now(timezone.utc).astimezone()


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def request_hash(req: IndexJobRequest):
    return hashlib.sha256(_canonical(req.model_dump(mode="json", exclude_unset=True)).encode()).hexdigest()


def resolve_storage_key(document_root: Path, storage_key: str) -> Path:
    """저장 키를 document_root 안의 실제 파일로. `..`·절대경로·루트 밖 심볼릭 링크는 거부."""
    parts = storage_key.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise IndexJobFailed("INVALID_STORAGE_KEY", "저장 키에 허용되지 않는 경로 구간이 있다")
    root = document_root.resolve()
    path = (root / storage_key).resolve()
    if not path.is_relative_to(root):
        raise IndexJobFailed("INVALID_STORAGE_KEY", "저장 키가 허용 루트를 벗어난다")
    if not path.is_file():
        raise IndexJobFailed("SOURCE_NOT_FOUND", "저장 키에 해당하는 파일이 없다")
    return path


def select_pages(policy, page_count):
    """쪽 정책 → 처리할 쪽 집합. 범위 밖·포함/제외 충돌·남는 쪽 없음은 실패."""
    include = set(policy.include_pages) if policy.include_pages else None
    exclude = set(policy.exclude_pages)
    if len(exclude) != len(policy.exclude_pages) or (policy.include_pages and len(include) != len(policy.include_pages)):
        raise IndexJobFailed("PAGE_POLICY_INVALID", "쪽 목록에 중복이 있다")
    out_of_range = sorted(p for p in (include or set()) | exclude if p > page_count)
    if out_of_range:
        raise IndexJobFailed("PAGE_POLICY_INVALID", f"문서는 {page_count}쪽인데 범위 밖 쪽이 있다: {out_of_range}")
    if include and include & exclude:
        raise IndexJobFailed("PAGE_POLICY_INVALID", f"포함·제외에 같은 쪽이 있다: {sorted(include & exclude)}")
    pages = (include or set(range(1, page_count + 1))) - exclude
    if not pages:
        raise IndexJobFailed("PAGE_POLICY_INVALID", "제외 후 처리할 쪽이 없다")
    return pages


def revision_id(req: IndexJobRequest, embedding_model, dimension):
    """같은 판본·같은 파일·같은 분할·같은 모델 → 같은 revision. 하나라도 바뀌면 새 revision."""
    basis = _canonical({
        "version_id": req.version_id, "sha256": req.source.sha256,
        "indexing_policy": req.indexing_policy.model_dump(mode="json", exclude_unset=True),
        "chunker": CHUNKER_VERSION, "embedding_model": embedding_model,
        "dimension": dimension, "document_id": req.document_id, "category": req.category,
        "original_source": req.original_source.model_dump(mode="json"),
        "effective_from": req.effective_from.isoformat(),
        "metadata": [req.title, req.version, req.visibility, req.material_type, req.is_demo, req.brand_id],
    })
    return "idx-" + hashlib.sha256(basis.encode()).hexdigest()[:20]


def build_chunks(req: IndexJobRequest, path: Path):
    """추출·분할. 임베딩 전 단계까지(로컬 검수에서도 그대로 쓴다)."""
    report = {"source_sha256": req.source.sha256, "media_type": req.source.media_type,
              "legal_temporal_reviewed": req.indexing_policy.legal_temporal_reviewed, "pages": [], "warnings": []}
    try:
        if req.source.media_type == "application/pdf":
            import pymupdf
            page_count = pymupdf.open(path).page_count
            pages_sel = select_pages(req.indexing_policy, page_count)
            pages, _ = extract_pdf(path, pages_sel)
        else:
            if req.indexing_policy.include_pages or req.indexing_policy.exclude_pages:
                raise IndexJobFailed("PAGE_POLICY_INVALID", "PDF가 아닌 파일에는 쪽 정책을 쓸 수 없다")
            page_count = None
            pages, _ = extract_text(path)
    except ExtractionError as e:
        raise IndexJobFailed("EXTRACTION_FAILED", str(e)) from e

    report["page_count"] = page_count
    report["processed_pages"] = [p.page for p in pages]
    for p in pages:
        report["pages"].append({"page": p.page, "chars": len(p.text), "removed_edge_lines": p.removed_edge_lines,
                                "tables": p.table_count, "flags": p.flags})
    scans = [p.page for p in pages if "low_text_possible_scan" in p.flags]
    if scans:
        report["warnings"].append({"code": "POSSIBLE_SCAN", "pages": scans, "message": "글자가 거의 없음. OCR 필요 여부 검토"})
    tables = [p.page for p in pages if p.table_count]
    if tables:
        report["warnings"].append({"code": "TABLE_NEEDS_REVIEW", "pages": tables,
                                   "message": "표가 있는 쪽. 본문은 줄 단위 텍스트로만 넣었고 표 구조·셀 값은 원문 대조 필요"})
    if len(scans) == len(pages):
        raise IndexJobFailed("EXTRACTION_FAILED", "모든 쪽에서 본문을 추출하지 못했다(스캔본 가능성)")

    chunks, mode = chunk_document(pages, req.category, req.material_type)
    report["chunking"] = {"version": CHUNKER_VERSION, "mode": mode, "count": len(chunks),
                          "max_chars": max((len(c.text) for c in chunks), default=0),
                          "min_chars": min((len(c.text) for c in chunks), default=0)}
    dups = sorted({c.section_key for c in chunks if "-dup" in c.section_key})
    if dups:
        report["warnings"].append({"code": "DUPLICATE_SECTION", "sections": dups,
                                   "message": "같은 조 번호가 다시 나옴. 중복 출력·발췌 여부 확인"})
    doc_day = req.effective_from.astimezone(KST).date().isoformat()
    auto = sorted({f"{c.article}: {c.effective_from}부터" + (" (현행본은 그 전까지)" if any(
        o.article == c.article and o.effective_until == c.effective_from for o in chunks) else " (시행 예정 신설)")
        for c in chunks if c.effective_from})
    if auto:
        report["warnings"].append({"code": "ARTICLE_PERIOD_AUTO_ASSIGNED", "items": auto, "count": len(auto),
                                   "message": "시행 예정 조문을 출력본 꼬리표로 분리해 조각 적용 기간을 나눔. 법적 시점 검수(legal_temporal_reviewed)는 별도"})
    stale = sorted({c.article for c in chunks if c.effective_until and c.effective_until <= doc_day})
    if stale:
        raise IndexJobFailed("ARTICLE_PERIOD_CONFLICT", f"문서 적용일 전에 끝나는 조문이 있다: {stale[:5]}")
    notes = sorted({f"{c.article}: {c.temporal_note}" for c in chunks if c.temporal_note})
    if notes:
        report["warnings"].append({"code": "ARTICLE_EFFECTIVE_DATE_UNREVIEWED", "items": notes, "count": len(notes),
                                   "message": "조문 일부에 시행일 표기가 있어 자동 분리하지 않음. 조각 적용일은 문서 적용일"})
    if not chunks:
        raise IndexJobFailed("EXTRACTION_FAILED", "분할 결과 조각이 없다")
    return chunks, report


def chunk_id(rev, section_key):
    """`chk-{revision 12자}-{section_key}`. 100자(계약 Id 상한)를 넘으면 section_key를 해시로 줄인다.
    자르기만 하면 서로 다른 조각이 같은 ID가 될 수 있어 쓰지 않는다."""
    cid = f"chk-{rev[4:16]}-{section_key}"
    if len(cid) > 100:
        cid = f"chk-{rev[4:16]}-h{hashlib.sha256(section_key.encode()).hexdigest()[:24]}"
    return cid


def _chunk_records(req, chunks, rev, embedding_model):
    records = []
    for c in chunks:
        text = c.text
        meta = IndexedChunkMetadata(
            chunk_id=chunk_id(rev, c.section_key),
            brand_id=req.brand_id, document_id=req.document_id, version_id=req.version_id,
            index_revision=rev, title=req.title, version=req.version, visibility=req.visibility,
            material_type=req.material_type, is_demo=req.is_demo, page=c.page, article=c.article,
            section_key=c.section_key,
            effective_from=max(req.effective_from, _kst_start(c.effective_from)) if c.effective_from else req.effective_from,
            effective_until=_kst_start(c.effective_until) if c.effective_until else None,
            original_source=req.original_source, embedding_model=embedding_model,
            content_sha256=hashlib.sha256(text.encode()).hexdigest(),
        )
        records.append({"metadata": meta.model_dump(mode="json"), "heading": c.heading, "text": text})
    return records


def validate_revision(rev_dir: Path, req: IndexJobRequest, rev: str):
    """인덱스·조각·매핑을 한 묶음으로 검증. 문제가 있으면 IndexJobFailed."""
    build = json.loads((rev_dir / "build-manifest.json").read_text(encoding="utf-8"))
    lines = (rev_dir / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
    index = faiss.read_index(str(rev_dir / "index.faiss"))
    n = build["chunk_count"]
    if not (len(lines) == index.ntotal == n and n > 0):
        raise IndexJobFailed("INDEX_VALIDATION_FAILED", f"건수 불일치: chunks={len(lines)} vectors={index.ntotal} manifest={n}")
    if index.d != build["dimension"]:
        raise IndexJobFailed("INDEX_VALIDATION_FAILED", "벡터 차원 불일치")
    vectors = index.reconstruct_n(0, index.ntotal)
    if not np.isfinite(vectors).all():
        raise IndexJobFailed("INDEX_VALIDATION_FAILED", "유한하지 않은 벡터 값")
    ids = set()
    for line in lines:
        rec = json.loads(line)
        try:
            meta = IndexedChunkMetadata.model_validate(rec["metadata"])
        except ValidationError as e:
            raise IndexJobFailed("INDEX_VALIDATION_FAILED", f"조각 메타데이터 계약 위반: {e.errors()[:1]}") from e
        if meta.chunk_id in ids:
            raise IndexJobFailed("INDEX_VALIDATION_FAILED", f"chunk_id 중복: {meta.chunk_id}")
        ids.add(meta.chunk_id)
        if (meta.document_id, meta.version_id, meta.index_revision) != (req.document_id, req.version_id, rev):
            raise IndexJobFailed("INDEX_VALIDATION_FAILED", "판본 연결 불일치")
        if hashlib.sha256(rec["text"].encode()).hexdigest() != meta.content_sha256:
            raise IndexJobFailed("INDEX_VALIDATION_FAILED", f"본문 해시 불일치: {meta.chunk_id}")
    if build["chunk_ids"] != [json.loads(l)["metadata"]["chunk_id"] for l in lines]:
        raise IndexJobFailed("INDEX_VALIDATION_FAILED", "벡터-조각 순서 매핑 불일치")


def write_revision(index_root: Path, req, chunks, report, embedder, rev):
    revisions = index_root / "revisions"
    revisions.mkdir(parents=True, exist_ok=True)
    final = revisions / rev
    if final.exists():
        validate_revision(final, req, rev)  # 같은 입력으로 이미 만든 revision → 재사용
        return final, json.loads((final / "build-manifest.json").read_text(encoding="utf-8"))["chunk_count"]

    tmp = revisions / f".tmp-{rev}-{uuid.uuid4().hex[:8]}"
    tmp.mkdir()
    try:
        records = _chunk_records(req, chunks, rev, embedder.model)
        try:
            vectors = np.asarray(embedder.embed([r["text"] for r in records]), dtype="float32")
        except EmbeddingError:
            raise
        except Exception as e:
            raise EmbeddingError("EMBEDDING_FAILED", f"임베딩 호출 실패: {type(e).__name__}") from e
        if vectors.shape != (len(records), embedder.dim):
            raise IndexJobFailed("INDEX_VALIDATION_FAILED", f"임베딩 결과 형태 불일치: {vectors.shape}")
        if not np.isfinite(vectors).all() or np.any(np.linalg.norm(vectors, axis=1) == 0):
            raise IndexJobFailed("INDEX_VALIDATION_FAILED", "유효하지 않은 임베딩 벡터")
        faiss.normalize_L2(vectors)
        index = faiss.IndexFlatIP(embedder.dim)
        index.add(vectors)
        faiss.write_index(index, str(tmp / "index.faiss"))
        with open(tmp / "chunks.jsonl", "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        build = {
            "index_revision": rev, "document_id": req.document_id, "version_id": req.version_id,
            "source_sha256": req.source.sha256, "indexing_policy": req.indexing_policy.model_dump(mode="json", exclude_unset=True),
            "chunker_version": CHUNKER_VERSION, "embedding_model": embedder.model, "dimension": embedder.dim,
            "metric": "inner_product_on_l2_normalized", "chunk_count": len(records),
            "chunk_ids": [r["metadata"]["chunk_id"] for r in records], "created_at": _now().isoformat(),
        }
        (tmp / "build-manifest.json").write_text(json.dumps(build, ensure_ascii=False, indent=2), encoding="utf-8")
        (tmp / "quality-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        validate_revision(tmp, req, rev)
        try:
            os.rename(tmp, final)
        except OSError:
            if not final.exists():
                raise
            validate_revision(final, req, rev)  # 동시에 같은 revision이 먼저 완성됨
            shutil.rmtree(tmp, ignore_errors=True)
        return final, len(records)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def default_roots():
    """C 회신(2026-10-23): 볼륨 하나를 backend·rag 모두 /var/lib/frangizer에 마운트.
    저장 키 documents/{document_id}/{version_id}.pdf는 DOCUMENT_STORE_DIR 기준 상대 경로다."""
    return (Path(os.environ.get("DOCUMENT_STORE_DIR") or "/var/lib/frangizer"),
            Path(os.environ.get("FAISS_DIR") or "/var/lib/frangizer/faiss"))


class JobStore:
    """작업 기록: index_root/jobs/{job_id}.json. 재시작 후에도 남는다.
    단일 프로세스 가정. 재시작 시 recover_interrupted()로 running 작업을 실패 처리한다."""

    def __init__(self, index_root: Path):
        self.dir = index_root / "jobs"
        self.dir.mkdir(parents=True, exist_ok=True)

    def get(self, job_id):
        p = self.dir / f"{job_id}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def put(self, job_id, record):
        tmp = self.dir / f".{job_id}.{uuid.uuid4().hex[:8]}.tmp"
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.dir / f"{job_id}.json")

    def recover_interrupted(self):
        """서버 시작 시 호출(B의 startup). running으로 남은 작업 → failed + INTERRUPTED (C 회신).
        재색인은 C가 새 job_id로 다시 요청한다. 남은 임시 revision 디렉터리도 지운다."""
        recovered = []
        for p in self.dir.glob("*.json"):
            rec = json.loads(p.read_text(encoding="utf-8"))
            if rec["job"]["status"] in ("queued", "running"):
                rec["job"].update(status="failed", error_code="INTERRUPTED",
                                  error_message="서버 재시작으로 작업이 중단되었다", updated_at=_now().isoformat())
                self.put(rec["job"]["job_id"], rec)
                recovered.append(rec["job"]["job_id"])
        for tmp in (self.dir.parent / "revisions").glob(".tmp-*"):
            shutil.rmtree(tmp, ignore_errors=True)
        return recovered


def run_index_job(payload: dict, *, document_root: Path | None = None, index_root: Path | None = None,
                  embedder=None, _reserved=False) -> dict:
    """IndexJob(dict, 계약 형식)을 돌려준다. 입력 스키마 오류는 ValidationError(→ 422),
    같은 job_id·다른 내용은 JobConflict(→ 409). 그 밖의 실패는 status=failed로 기록한다."""
    req = IndexJobRequest.model_validate(payload)
    if document_root is None or index_root is None:
        env_doc, env_idx = default_roots()
        document_root, index_root = document_root or env_doc, index_root or env_idx
    store = JobStore(index_root)
    rhash = request_hash(req)
    existing = store.get(req.job_id)
    if existing:
        if existing["request_hash"] != rhash:
            raise JobConflict(f"job_id {req.job_id}에 다른 내용의 작업이 이미 있다")
        if not (_reserved and existing["job"]["status"] == "queued"):
            return existing["job"]

    created = datetime.fromisoformat(existing["job"]["created_at"]) if existing else _now()
    job = IndexJob(job_id=req.job_id, document_id=req.document_id, version_id=req.version_id, status="running",
                   chunk_count=0, index_revision=None, error_code=None, error_message=None,
                   created_at=created, updated_at=_now())
    store.put(req.job_id, {"request_hash": rhash, "job": job.model_dump(mode="json")})
    try:
        path = resolve_storage_key(document_root, req.source.storage_key)
        if _sha256_file(path) != req.source.sha256:
            raise IndexJobFailed("CHECKSUM_MISMATCH", "파일 sha256이 요청과 다르다")
        chunks, report = build_chunks(req, path)
        embedder = embedder or load_embedder()
        rev = revision_id(req, embedder.model, embedder.dim)
        _, count = write_revision(index_root, req, chunks, report, embedder, rev)
        job = job.model_copy(update={"status": "succeeded", "chunk_count": count, "index_revision": rev,
                                     "updated_at": _now()})
    except (IndexJobFailed, EmbeddingError) as e:
        job = job.model_copy(update={"status": "failed", "error_code": e.code, "error_message": str(e),
                                     "updated_at": _now()})
    except Exception as e:  # 예상 못한 오류도 기록하고 내부 경로는 남기지 않는다
        job = job.model_copy(update={"status": "failed", "error_code": "INTERNAL_ERROR",
                                     "error_message": type(e).__name__, "updated_at": _now()})
    result = IndexJob.model_validate(job.model_dump()).model_dump(mode="json")
    store.put(req.job_id, {"request_hash": rhash, "job": result})
    return result
