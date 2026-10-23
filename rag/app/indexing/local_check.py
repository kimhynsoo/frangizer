"""로컬 검수: manifest의 data/source/ 문서를 추출·분할까지만 돌려 품질 보고서를 만든다.

임베딩·외부 호출·인덱스 생성은 하지 않는다. data/staged/는 적재 경계 밖이라 건너뛴다.
보고서에는 원문 일부(조각 미리보기)가 들어가므로 저장소 밖에 쓴다.

  cd rag && python3 -m app.indexing.local_check [--out DIR] [--key LOCAL_KEY ...]
"""
import argparse
import json
import tempfile
from pathlib import Path

from .chunking import CHUNKER_VERSION
from .job import IndexJobFailed, build_chunks
from .models import IndexJobRequest

ROOT = Path(__file__).resolve().parents[3]


def request_from_manifest(doc):
    """manifest 항목 → IndexJobRequest. ID·저장 키는 검수용 임시값(서버 발급 아님)."""
    meta = doc["upload_metadata"]
    vid = f"{doc['local_key']}-{meta['version']}"
    return IndexJobRequest.model_validate({
        "job_id": f"local-{vid}", "brand_id": "local-check", "document_id": doc["local_key"], "version_id": vid,
        "title": meta["title"], "version": meta["version"], "category": meta["category"],
        "material_type": meta["material_type"], "is_demo": meta["is_demo"], "visibility": meta["visibility"],
        "effective_from": meta["effective_from"] or "1970-01-01T00:00:00+09:00",
        "original_source": meta["original_source"], "indexing_policy": meta["indexing_policy"],
        "source": {"storage_key": f"documents/{vid}.pdf", "sha256": doc["file"]["sha256"],
                   "media_type": doc["file"]["media_type"]},
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()) / "frangizer-quality")
    ap.add_argument("--key", action="append", help="local_key로 일부 문서만")
    args = ap.parse_args()
    manifest = json.loads((ROOT / "data" / "manifest.json").read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=True)

    summary = []
    for doc in manifest["documents"]:
        if args.key and doc["local_key"] not in args.key:
            continue
        row = {"path": doc["path"], "local_key": doc["local_key"]}
        if doc["repo_status"] != "source":
            row["result"] = "skipped_staged"
            summary.append(row)
            continue
        req = request_from_manifest(doc)
        try:
            chunks, report = build_chunks(req, ROOT / "data" / doc["path"])
        except IndexJobFailed as e:
            row.update(result="failed", error_code=e.code, error=str(e))
            summary.append(row)
            continue
        if doc["upload_metadata"]["effective_from"] is None:
            report["warnings"].append({"code": "EFFECTIVE_FROM_MISSING", "message": "manifest에 적용일 없음(검수용 임시값 사용)"})
        report["chunks_preview"] = [
            {"section_key": c.section_key, "page": c.page, "article": c.article, "chars": len(c.text),
             "head": c.text[:120]} for c in chunks]
        name = f"{req.version_id}.quality-report.json"
        (args.out / name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        row.update(result="ok", mode=report["chunking"]["mode"], chunks=len(chunks),
                   articles=sum(1 for c in chunks if c.article), pages=len(report["processed_pages"]),
                   max_chars=report["chunking"]["max_chars"],
                   warnings={w["code"]: len(w.get("pages") or w.get("items") or w.get("sections") or [1])
                             for w in report["warnings"]})
        summary.append(row)

    out = {"chunker_version": CHUNKER_VERSION, "documents": summary}
    (args.out / "summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in summary:
        print(json.dumps(r, ensure_ascii=False))
    print(f"보고서: {args.out}")


if __name__ == "__main__":
    main()
