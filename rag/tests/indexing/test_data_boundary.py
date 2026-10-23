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
