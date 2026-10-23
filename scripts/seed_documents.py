"""data/manifest.json의 원본 자료를 백엔드에 등록 → 승인(색인)한다. 배포는 --publish일 때만.

  # 미리보기(기본): 서버에 아무것도 보내지 않는다
  python3 scripts/seed_documents.py

  # 실제 등록: 승인·색인까지
  SEED_HQ_PASSWORD=... python3 scripts/seed_documents.py --apply [--base-url http://localhost:8080/api/v1]

  # 배포까지 (판본마다 점포 공지가 생긴다)
  SEED_HQ_PASSWORD=... python3 scripts/seed_documents.py --apply --publish [--only LOCAL_KEY ...]

- 대상: repo_status=source인 문서. data/staged/(레시피 v1.1)는 시연에서 직접 올리므로 넣지 않는다.
- 합성 가맹계약서처럼 선택 자료(OPTIONAL)는 --include-optional을 줄 때만 넣는다.
- 배포하면 백엔드가 판본마다 공지를 만든다(공지 없는 배포는 없음, C 2026-10-23). 그래서 기본은
  approved+ready까지만 하고, 배포는 --publish로 고른 문서만 한다. 배포 전에는 검색 근거가 아니다.
- 다시 실행해도 된다. 서버 상태를 먼저 보고, 배포된 판본은 건너뛰고 끊긴 단계부터 잇는다.
  멱등 키는 판본·파일 해시로 고정해서 같은 요청이 두 번 처리되지 않는다.
- 서버에 같은 판본이 다른 파일로 있거나, 서버가 준 ID가 합의 규칙(manifest server)과 다르면 멈춘다.
- 비밀번호는 환경변수로만 받고 출력하지 않는다. 표준 라이브러리만 쓴다.

API는 docs/api/public.openapi.yaml 기준이고, C 백엔드(PR #3)의 DocumentController·DocumentService와 대조했다
(version_id={document_id}-{version}, 판본 목록에 sha256, 승인 후 GET /index-jobs가 RAG 재전송·상태 동기화).
"""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "manifest.json"
OPTIONAL = {"demo-franchise-contract"}  # C 회신: 시연 질문에 필요할 때만 등록
REVIEW_NOTE = "초기 시드: manifest 판본·쪽 정책·적용일·sha256 대조 완료 (A)"


class SeedError(Exception):
    pass


class Api:
    def __init__(self, base_url, timeout=30):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.token = None

    def call(self, method, path, *, json_body=None, body=None, content_type=None, key=None, ok=(200,)):
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if key:
            headers["Idempotency-Key"] = key
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode()
            content_type = "application/json; charset=utf-8"
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        except urllib.error.URLError as e:
            raise SeedError(f"서버에 연결할 수 없음: {self.base} ({e.reason})") from e
        payload = json.loads(raw) if raw else {}
        if status not in ok:
            err = payload.get("error", {}) if isinstance(payload, dict) else {}
            raise SeedError(f"{method} {path} → {status} {err.get('code', '')} {err.get('message', '')}".strip())
        return status, payload

    def login(self, username, password):
        _, data = self.call("POST", "/login", json_body={"username": username, "password": password})
        self.token = data.get("accessToken")
        if not self.token:
            raise SeedError("로그인 응답에 accessToken이 없음")
        role = (data.get("user") or {}).get("role")
        if role and role != "hq":
            raise SeedError(f"본사(hq) 계정이 필요함: role={role}")

    def versions(self, doc_id):
        status, data = self.call("GET", f"/documents/{q(doc_id)}/versions?page_size=100", ok=(200, 404))
        return [] if status == 404 else data.get("data", [])


def q(s):
    return urllib.parse.quote(s, safe="")


def multipart(fields, file_field, file_path, file_name, media_type):
    """fields: {이름: JSON 문자열}. 백엔드는 metadata를 @RequestPart String으로 받으므로
    문자셋을 명시해 서버 기본 문자셋과 상관없이 UTF-8로 읽히게 한다."""
    boundary = "----frangizer-seed-" + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n'
                     f"Content-Type: application/json; charset=utf-8\r\n\r\n".encode() + value.encode() + b"\r\n")
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'
                 f"Content-Type: {media_type}\r\n\r\n".encode() + file_path.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def idem(action, server, sha):
    return f"seed-{action}-{server['version_id']}-{sha[:8]}"[:100]


def targets(manifest, only=None, include_optional=False):
    out, skipped = [], []
    for d in manifest["documents"]:
        key = d["local_key"]
        if only and key not in only:
            continue
        if d["repo_status"] != "source":
            skipped.append((d, "staged — 시연에서 직접 등록"))
        elif key in OPTIONAL and not include_optional:
            skipped.append((d, "선택 자료 — --include-optional로 포함"))
        else:
            out.append(d)
    return out, skipped


def check_local(doc):
    path = ROOT / "data" / doc["path"]
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if sha != doc["file"]["sha256"]:
        raise SeedError(f"파일 해시가 manifest와 다름: {doc['path']}")
    if path.stat().st_size > 10 * 1024 * 1024:
        raise SeedError(f"10MB 초과: {doc['path']}")
    return path, sha


def wait_ready(api, doc_id, ver_id, job_id, timeout, interval):
    """색인 작업이 끝날 때까지 기다린다. job_id가 없으면 판본의 index_status를 본다."""
    deadline = time.monotonic() + timeout
    while True:
        if job_id:
            _, data = api.call("GET", f"/index-jobs/{q(job_id)}")
            job = data["data"]
            if job["status"] == "succeeded":
                return
            if job["status"] == "failed":
                raise SeedError(f"색인 실패 job={job_id} {job.get('error_code')}: {job.get('error_message')}")
        else:
            v = next((v for v in api.versions(doc_id) if v["version_id"] == ver_id), None)
            if v and v["index_status"] == "ready":
                return
            if v and v["index_status"] == "failed":
                raise SeedError(f"색인 실패 {ver_id} (재승인은 C의 계약 보완 후 가능)")
        if time.monotonic() > deadline:
            raise SeedError(f"색인 대기 시간 초과 {ver_id}")
        time.sleep(interval)


def seed_one(api, doc, *, publish=False, timeout=600, interval=2.0):
    """한 판본을 approved+ready(publish=True면 published)까지 진행. 한 일 목록을 돌려준다."""
    path, sha = check_local(doc)
    server, meta = doc["server"], doc["upload_metadata"]
    doc_id, ver_id = server["document_id"], server["version_id"]
    done = []

    current = next((v for v in api.versions(doc_id) if v["version_id"] == ver_id), None)
    if current and current.get("sha256") and current["sha256"] != sha:
        raise SeedError(f"서버의 {ver_id}가 다른 파일로 등록돼 있음 — 새 판본으로 올려야 함")

    if current is None:
        metadata = {"document_id": doc_id, **meta}
        body, ctype = multipart({"metadata": json.dumps(metadata, ensure_ascii=False)}, "file", path,
                                Path(doc["path"]).name, doc["file"]["media_type"])
        _, data = api.call("POST", "/documents", body=body, content_type=ctype, key=idem("upload", server, sha),
                           ok=(201,))
        got = data["data"]
        if (got["document_id"], got["version_id"]) != (doc_id, ver_id):
            raise SeedError(f"서버 ID가 합의 규칙과 다름: 기대 {doc_id}/{ver_id}, 받음 {got['document_id']}/{got['version_id']}")
        current = {**got, "lifecycle": got.get("lifecycle", "draft"), "index_status": got.get("index_status", "not_started")}
        done.append("등록")

    if current["lifecycle"] == "draft":
        _, data = api.call("POST", f"/documents/{q(doc_id)}/versions/{q(ver_id)}/approve",
                           json_body={"review_note": REVIEW_NOTE}, key=idem("approve", server, sha), ok=(202,))
        wait_ready(api, doc_id, ver_id, data["data"].get("job_id"), timeout, interval)
        current["lifecycle"], current["index_status"] = "approved", "ready"
        done.append("승인·색인")
    elif current["lifecycle"] == "approved" and current["index_status"] != "ready":
        wait_ready(api, doc_id, ver_id, None, timeout, interval)
        current["index_status"] = "ready"
        done.append("색인 대기")

    if publish and current["lifecycle"] == "approved":
        title = f"[초기 등록] {meta['title']} {meta['version']}"[:200]
        api.call("POST", f"/documents/{q(doc_id)}/versions/{q(ver_id)}/publish",
                 json_body={"notice_title": title}, key=idem("publish", server, sha))
        done.append("배포")
    return done or ["이미 완료"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="실제로 등록한다(없으면 미리보기)")
    ap.add_argument("--base-url", default=os.environ.get("FRANGIZER_API", "http://localhost:8080/api/v1"))
    ap.add_argument("--username", default=os.environ.get("SEED_HQ_USERNAME", "demo-hq-01"))
    ap.add_argument("--only", action="append", help="local_key로 일부만")
    ap.add_argument("--include-optional", action="store_true", help=f"선택 자료 포함: {', '.join(sorted(OPTIONAL))}")
    ap.add_argument("--publish", action="store_true", help="배포까지 한다. 판본마다 점포 공지가 생긴다")
    ap.add_argument("--timeout", type=float, default=600, help="판본당 색인 대기 초")
    ap.add_argument("--poll-interval", type=float, default=2.0)
    args = ap.parse_args(argv)

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    todo, skipped = targets(manifest, set(args.only or []), args.include_optional)
    for d in todo:
        check_local(d)

    print(f"{'실제 등록' if args.apply else '미리보기'} · {args.base_url} · 대상 {len(todo)}건, 제외 {len(skipped)}건")
    for d, why in skipped:
        print(f"  제외  {d['server']['version_id']:<45} {why}")
    if not args.apply:
        for d in todo:
            print(f"  예정  {d['server']['version_id']:<45} 등록 → 승인·색인" + (" → 배포(공지 생성)" if args.publish else ""))
        print("서버에 보내지 않았다. 실제 등록은 --apply")
        return 0

    password = os.environ.get("SEED_HQ_PASSWORD")
    if not password:
        print("SEED_HQ_PASSWORD 환경변수가 필요하다", file=sys.stderr)
        return 2
    api = Api(args.base_url)
    try:
        api.login(args.username, password)
    except SeedError as e:
        print(f"로그인 실패: {e}", file=sys.stderr)
        return 1

    failed = 0
    for d in todo:
        vid = d["server"]["version_id"]
        try:
            done = seed_one(api, d, publish=args.publish, timeout=args.timeout, interval=args.poll_interval)
            print(f"  완료  {vid:<45} {' → '.join(done)}")
        except SeedError as e:
            failed += 1
            print(f"  실패  {vid:<45} {e}")
    print(f"완료 {len(todo) - failed}건, 실패 {failed}건")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
