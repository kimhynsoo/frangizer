"""data/ 자료와 적재 경계 점검. 파일·manifest가 바뀌면 여기서 먼저 깨진다."""
import hashlib
import json
import subprocess
import sys

from app.indexing.local_check import request_from_manifest

from .conftest import REPO

MANIFEST = json.loads((REPO / "data" / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_matches_files():
    listed = set()
    for d in MANIFEST["documents"]:
        path = REPO / "data" / d["path"]
        listed.add(path)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == d["file"]["sha256"], d["path"]
        assert d["path"].startswith(d["repo_status"] + "/")
    on_disk = {p for p in (REPO / "data").rglob("*.pdf")}
    assert on_disk == listed


def test_staged_and_structured_not_in_source():
    for d in MANIFEST["documents"]:
        if d["repo_status"] == "staged":
            assert not d["path"].startswith("source/")
    assert not any((REPO / "data" / "source").rglob("*.json"))


def test_source_documents_build_valid_requests(contract):
    for d in MANIFEST["documents"]:
        if d["repo_status"] == "source":
            contract("IndexJobRequest", request_from_manifest(d).model_dump(mode="json", exclude_unset=True))


def test_recipe_structured_matches_source_pdf():
    script = REPO / "rag" / "app" / "indexing" / "recipe_structure.py"
    r = subprocess.run([sys.executable, str(script), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_all_source_chunk_ids_fit_contract():
    from app.indexing.job import build_chunks, chunk_id
    for d in MANIFEST["documents"]:
        if d["repo_status"] != "source":
            continue
        req = request_from_manifest(d)
        chunks, _ = build_chunks(req, REPO / "data" / d["path"])
        ids = [chunk_id("idx-" + "0" * 20, c.section_key) for c in chunks]
        assert len(ids) == len(set(ids)), d["path"]
        assert max(map(len, ids)) <= 100, d["path"]


def test_server_ids_follow_backend_rule():
    for d in MANIFEST["documents"]:
        s, v = d["server"], d["upload_metadata"]["version"]
        assert s["document_id"] == d["local_key"]
        assert s["version_id"] == f"{s['document_id']}-{v}"
        assert s["storage_key"] == f"documents/{s['document_id']}/{s['version_id']}.pdf"
