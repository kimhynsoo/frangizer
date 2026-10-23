"""seed_documents.py를 계약(public.openapi.yaml) 형태의 가짜 백엔드에 돌려 본다. 네트워크 밖으로 나가지 않는다."""
import email.parser
import email.policy
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("seed_documents", ROOT / "scripts" / "seed_documents.py")
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)

MANIFEST = json.loads((ROOT / "data" / "manifest.json").read_text(encoding="utf-8"))
SOURCE = [d for d in MANIFEST["documents"] if d["repo_status"] == "source"]
DEFAULT = [d for d in SOURCE if d["local_key"] not in seed.OPTIONAL]


class FakeBackend:
    """계약의 등록·승인·배포 흐름만 흉내 낸다. 상태: versions[(doc, ver)] = DocumentVersion 일부."""

    def __init__(self):
        self.versions = {}
        self.jobs = {}
        self.idem = {}
        self.calls = []
        self.uploads = []
        self.override_version_id = None
        self.fail_index_for = set()

    def handle(self, method, path, headers, body):
        self.calls.append((method, path))
        if path == "/api/v1/login":
            cred = json.loads(body)
            if cred != {"username": "demo-hq-01", "password": "pw"}:
                return 401, {"error": {"code": "AUTH_FAILED", "message": "실패"}}
            return 200, {"accessToken": "tok", "token_type": "Bearer", "expires_in": 3600,
                         "user": {"role": "hq"}, "request_id": "r"}
        if headers.get("Authorization") != "Bearer tok":
            return 401, {"error": {"code": "AUTH_REQUIRED", "message": "로그인"}}
        key = headers.get("Idempotency-Key")
        if method == "POST":
            assert key and 8 <= len(key) <= 100, key
            if key in self.idem:
                return self.idem[key]
        parts = path.removeprefix("/api/v1/").split("?")[0].split("/")
        res = self._route(method, parts, headers, body)
        if method == "POST" and res[0] < 300:
            self.idem[key] = res
        return res

    def _route(self, method, parts, headers, body):
        if method == "GET" and parts[0] == "documents" and parts[2] == "versions" and len(parts) == 3:
            items = [v for (d, _), v in self.versions.items() if d == parts[1]]
            if not items:
                return 404, {"error": {"code": "RESOURCE_NOT_FOUND", "message": "없음"}}
            return 200, {"data": items, "meta": {}, "request_id": "r"}
        if method == "POST" and parts == ["documents"]:
            msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
                b"Content-Type: " + headers["Content-Type"].encode() + b"\r\n\r\n" + body)
            fields = {p.get_param("name", header="content-disposition"): p for p in msg.iter_parts()}
            # 브라우저 FormData처럼 문자셋 표기 없는 UTF-8 필드. Spring Boot 기본(UTF-8 강제)과 같이 읽는다.
            meta = json.loads(fields["metadata"].get_payload(decode=True).decode("utf-8"))
            data = fields["file"].get_payload(decode=True)
            self.uploads.append((meta, fields["file"].get_filename(), len(data)))
            doc = meta["document_id"]
            ver = self.override_version_id or f"{doc}-{meta['version']}"
            import hashlib
            self.versions[(doc, ver)] = {"document_id": doc, "version_id": ver, "version": meta["version"],
                                         "lifecycle": "draft", "index_status": "not_started",
                                         "sha256": hashlib.sha256(data).hexdigest()}
            return 201, {"data": {"document_id": doc, "version_id": ver, "version": meta["version"],
                                  "lifecycle": "draft", "index_status": "not_started"}, "request_id": "r"}
        if method == "POST" and parts[-1] in ("approve", "publish"):
            v = self.versions.get((parts[1], parts[3]))
            if v is None:
                return 404, {"error": {"code": "RESOURCE_NOT_FOUND", "message": "없음"}}
            if parts[-1] == "approve":
                if v["lifecycle"] != "draft":
                    return 409, {"error": {"code": "INVALID_STATE", "message": "상태"}}
                job_id = f"job-{len(self.jobs) + 1}"
                failed = v["version_id"] in self.fail_index_for
                self.jobs[job_id] = {"job_id": job_id, "status": "failed" if failed else "succeeded",
                                     "error_code": "EXTRACTION_FAILED" if failed else None, "error_message": None}
                v.update(lifecycle="approved", index_status="failed" if failed else "ready")
                return 202, {"data": {"document_id": v["document_id"], "version_id": v["version_id"],
                                      "lifecycle": "approved", "index_status": "queued", "job_id": job_id},
                             "request_id": "r"}
            if v["lifecycle"] != "approved" or v["index_status"] != "ready":
                return 409, {"error": {"code": "INDEX_NOT_READY", "message": "색인"}}
            assert json.loads(body)["notice_title"]
            v["lifecycle"] = "published"
            return 200, {"data": {**v, "notice_id": "n"}, "request_id": "r"}
        if method == "GET" and parts[0] == "index-jobs":
            return 200, {"data": self.jobs[parts[1]], "request_id": "r"}
        return 404, {"error": {"code": "RESOURCE_NOT_FOUND", "message": f"{method} {parts}"}}


@pytest.fixture
def backend():
    fake = FakeBackend()

    class H(BaseHTTPRequestHandler):
        def _do(self):
            n = int(self.headers.get("Content-Length") or 0)
            status, payload = fake.handle(self.command, self.path, dict(self.headers), self.rfile.read(n))
            raw = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = do_POST = _do

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    fake.url = f"http://127.0.0.1:{srv.server_port}/api/v1"
    yield fake
    srv.shutdown()


def run(backend, *extra, password="pw", monkeypatch=None):
    if monkeypatch:
        monkeypatch.setenv("SEED_HQ_PASSWORD", password)
    return seed.main(["--apply", "--base-url", backend.url, "--poll-interval", "0", *extra])


def test_dry_run_sends_nothing(capsys):
    assert seed.main(["--base-url", "http://127.0.0.1:9/api/v1"]) == 0  # 닫힌 포트: 요청하면 실패했을 것
    out = capsys.readouterr().out
    assert "미리보기" in out and "staged" in out and "demo-cafe-recipes-1.1" in out


def test_apply_seeds_source_and_skips_staged_and_optional(backend, monkeypatch):
    assert run(backend, monkeypatch=monkeypatch) == 0
    expected = {d["server"]["version_id"] for d in DEFAULT}
    assert {v for (_, v) in backend.versions} == expected
    assert all(v["lifecycle"] == "published" for v in backend.versions.values())
    assert "demo-cafe-recipes-1.1" not in expected
    meta, name, size = next(u for u in backend.uploads if u[0]["document_id"] == "food-sanitation-act")
    want = next(d for d in DEFAULT if d["local_key"] == "food-sanitation-act")
    assert meta == {"document_id": "food-sanitation-act", **want["upload_metadata"]}
    assert name == "식품위생법_v2026-10-08.pdf"  # 한글 원래 파일명 → 서버 file_name
    assert size == (ROOT / "data" / want["path"]).stat().st_size


def test_rerun_changes_nothing(backend, monkeypatch, capsys):
    run(backend, monkeypatch=monkeypatch)
    posts_before = sum(1 for m, _ in backend.calls if m == "POST")
    assert run(backend, monkeypatch=monkeypatch) == 0
    posts_after = sum(1 for m, _ in backend.calls if m == "POST")
    assert posts_after - posts_before == 1  # 로그인만
    assert capsys.readouterr().out.count("이미 완료") == len(DEFAULT)


def test_resumes_after_interruption(backend, monkeypatch):
    run(backend, "--no-publish", monkeypatch=monkeypatch)
    assert all(v["lifecycle"] == "approved" for v in backend.versions.values())
    assert run(backend, monkeypatch=monkeypatch) == 0
    assert all(v["lifecycle"] == "published" for v in backend.versions.values())
    assert sum(1 for m, p in backend.calls if p == "/api/v1/documents") == len(DEFAULT)  # 다시 올리지 않음


def test_include_optional(backend, monkeypatch):
    assert run(backend, "--include-optional", "--only", "demo-franchise-contract", monkeypatch=monkeypatch) == 0
    assert list(backend.versions) == [("demo-franchise-contract", "demo-franchise-contract-1.0")]


def test_stops_when_server_ids_differ(backend, monkeypatch, capsys):
    backend.override_version_id = "ver-0001"
    assert run(backend, "--only", "consumer-basic-act", monkeypatch=monkeypatch) == 1
    assert "합의 규칙과 다름" in capsys.readouterr().out
    assert not any(p.endswith("/approve") for _, p in backend.calls)


def test_stops_when_server_has_different_file(backend, monkeypatch, capsys):
    backend.versions[("consumer-basic-act", "consumer-basic-act-2026-09-11")] = {
        "document_id": "consumer-basic-act", "version_id": "consumer-basic-act-2026-09-11",
        "lifecycle": "draft", "index_status": "not_started", "sha256": "0" * 64}
    assert run(backend, "--only", "consumer-basic-act", monkeypatch=monkeypatch) == 1
    assert "다른 파일" in capsys.readouterr().out


def test_index_failure_is_reported_and_others_continue(backend, monkeypatch, capsys):
    backend.fail_index_for = {"consumer-basic-act-2026-09-11"}
    assert run(backend, "--only", "consumer-basic-act", "--only", "franchise-act", monkeypatch=monkeypatch) == 1
    out = capsys.readouterr().out
    assert "색인 실패" in out and "완료 1건, 실패 1건" in out
    assert backend.versions[("franchise-act", "franchise-act-2026-10-02")]["lifecycle"] == "published"


def test_requires_password(backend, monkeypatch):
    monkeypatch.delenv("SEED_HQ_PASSWORD", raising=False)
    assert seed.main(["--apply", "--base-url", backend.url]) == 2
    assert backend.calls == []


def test_wrong_password(backend, monkeypatch):
    assert run(backend, password="nope", monkeypatch=monkeypatch) == 1
    assert backend.calls == [("POST", "/api/v1/login")]
