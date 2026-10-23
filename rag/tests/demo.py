"""Explicit test-only HTTP server; not a live RAG or an LLM evaluation."""
import argparse
import json
import os
import secrets
from pathlib import Path

from app.auth import AuthSettings
from app.generation.agent import CounselAgent
from app.main import create_app
from .support import FixtureRetriever, ScriptedModel, query_payload, user_token


def create_demo_app():
    return create_app(
        auth=AuthSettings.from_env(), agent=CounselAgent(ScriptedModel(), FixtureRetriever()),
        state_db=os.environ.get("RAG_STATE_DB", ".demo/query-results.sqlite3"), demo=True,
    )


def prepare():
    directory = Path(".demo")
    directory.mkdir(mode=0o700, exist_ok=True)
    auth = AuthSettings(secrets.token_urlsafe(32), secrets.token_urlsafe(48), "frangizer-test", "frangizer-rag")
    values = {"INTERNAL_TOKEN": auth.internal_token, "JWT_SECRET": auth.jwt_secret, "JWT_ISSUER": auth.issuer,
              "JWT_AUDIENCE": auth.audience, "JWT_ALGORITHM": auth.algorithm,
              "RAG_STATE_DB": ".demo/query-results.sqlite3", "DEMO_USER_TOKEN": user_token(auth)}
    env = directory / "credentials.env"
    env.touch(mode=0o600, exist_ok=True)
    env.chmod(0o600)
    env.write_text("\n".join(f"{k}='{v}'" for k, v in values.items()) + "\n")
    (directory / "request.json").write_text(json.dumps(query_payload(), ensure_ascii=False, indent=2) + "\n")
    print("테스트 전용 인증값과 요청을 .demo/에 생성했습니다. 실제 문서·모델 검증이 아닙니다.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare"])
    parser.parse_args()
    prepare()
