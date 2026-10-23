import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.indexing.embedding import EmbeddingError, OpenAIEmbedder, load_embedder


@pytest.fixture
def api():
    state = {"calls": [], "fail": [], "dim": 1536, "shuffle": False}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["calls"].append((self.headers["Authorization"], body))
            if state["fail"]:
                self.send_response(state["fail"].pop(0)); self.end_headers(); return
            data = [{"index": i, "embedding": [float(len(t))] + [0.0] * (state["dim"] - 1)}
                    for i, t in enumerate(body["input"])]
            if state["shuffle"]:
                data.reverse()
            raw = json.dumps({"data": data}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(raw))); self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{srv.server_port}/v1"
    yield state
    srv.shutdown()


def embedder(api):
    return OpenAIEmbedder("text-embedding-3-small", "sk-test-secret", api["url"], sleep=lambda s: None)


def test_batches_and_keeps_order(api):
    api["shuffle"] = True
    texts = ["가" * (i % 7 + 1) for i in range(130)]
    v = embedder(api).embed(texts)
    assert v.shape == (130, 1536)
    assert [int(x) for x in v[:, 0]] == [len(t) for t in texts]  # 응답 순서가 섞여도 index로 맞춤
    assert [len(b["input"]) for _, b in api["calls"]] == [64, 64, 2]
    assert all(a == "Bearer sk-test-secret" and b["model"] == "text-embedding-3-small" for a, b in api["calls"])


def test_retries_rate_limit_then_succeeds(api):
    api["fail"] = [429, 503]
    assert embedder(api).embed(["a"]).shape == (1, 1536)
    assert len(api["calls"]) == 3


def test_auth_error_not_retried_and_key_not_leaked(api):
    api["fail"] = [401]
    with pytest.raises(EmbeddingError) as e:
        embedder(api).embed(["a"])
    assert e.value.code == "EMBEDDING_FAILED" and "401" in str(e.value)
    assert "sk-test-secret" not in str(e.value)
    assert len(api["calls"]) == 1


def test_dimension_mismatch_fails(api):
    api["dim"] = 8
    with pytest.raises(EmbeddingError, match="차원"):
        embedder(api).embed(["a"])


@pytest.mark.parametrize("env", [{}, {"EMBEDDING_MODEL": "unknown-model", "LLM_API_KEY": "k"},
                                 {"EMBEDDING_MODEL": "text-embedding-3-small"}])
def test_load_embedder_requires_config(monkeypatch, env):
    for k in ("EMBEDDING_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(EmbeddingError) as e:
        load_embedder()
    assert e.value.code == "EMBEDDING_NOT_CONFIGURED"


def test_load_embedder_from_env(monkeypatch, api):
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("EMBEDDING_BASE_URL", api["url"])
    e = load_embedder()
    assert (e.model, e.dim) == ("text-embedding-3-small", 1536)
