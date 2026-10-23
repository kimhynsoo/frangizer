"""Persistent completed-result replay plus single-worker in-flight coalescing."""
import asyncio
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from .errors import ServiceError
from .schemas import InternalAnswer, InternalQuery


class ReplayStore:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("""CREATE TABLE IF NOT EXISTS query_results (
            user_id TEXT NOT NULL, question_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL, result TEXT NOT NULL, created REAL NOT NULL,
            PRIMARY KEY(user_id, question_id, attempt_id))""")
        self.lock = asyncio.Lock()
        self.running: dict[tuple, tuple[str, asyncio.Task]] = {}

    async def execute(self, query: InternalQuery, operation):
        key = (query.principal.user_id, query.question_id, query.attempt_id)
        digest = hashlib.sha256(query.model_dump_json().encode()).hexdigest()
        async with self.lock:
            self.connection.execute("DELETE FROM query_results WHERE created < ?", (time.time() - 86400,))
            row = self.connection.execute(
                "SELECT fingerprint, result FROM query_results WHERE user_id=? AND question_id=? AND attempt_id=?", key,
            ).fetchone()
            self.connection.commit()
            if row:
                self._same(row[0], digest)
                record = json.loads(row[1])
                if "error" in record:
                    e = record["error"]
                    raise ServiceError(e["status"], e["code"], e["message"], retryable=e["retryable"])
                return InternalAnswer.model_validate(record["result"])
            if key in self.running:
                fingerprint, task = self.running[key]
                self._same(fingerprint, digest)
            else:
                if len(self.running) >= 32:
                    raise ServiceError(503, "SERVICE_UNAVAILABLE", "상담 요청이 많습니다. 잠시 후 다시 시도해 주세요.", retryable=True)
                task = asyncio.create_task(self._perform(key, digest, operation))
                # Retrieve exceptions even if every HTTP waiter disconnects.
                task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
                self.running[key] = (digest, task)
        # A disconnected waiter must not start a second model execution on retry.
        return await asyncio.shield(task)

    async def _perform(self, key, digest, operation):
        try:
            try:
                result = await operation()
            except ServiceError as exc:
                await self._save(key, digest, {"error": {
                    "status": exc.status, "code": exc.code, "message": exc.message, "retryable": exc.retryable,
                }})
                raise
            await self._save(key, digest, {"result": result.model_dump(mode="json")})
            return result
        finally:
            async with self.lock:
                self.running.pop(key, None)

    async def _save(self, key, digest, record):
        async with self.lock:
            self.connection.execute(
                "INSERT INTO query_results VALUES (?, ?, ?, ?, ?, ?)",
                (*key, digest, json.dumps(record, ensure_ascii=False), time.time()),
            )
            self.connection.commit()

    @staticmethod
    def _same(existing, current):
        if existing != current:
            raise ServiceError(409, "IDEMPOTENCY_CONFLICT", "동일한 실행 ID에 다른 요청을 사용할 수 없습니다.")

    async def close(self):
        tasks = [t for _, t in self.running.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.connection.close()
