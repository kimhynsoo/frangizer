"""Single-process bounded scheduler; persisted jobs remain compatible with POST polling."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from ..errors import ServiceError
from .job import JobConflict, JobStore, request_hash, run_index_job
from .models import IndexJobRequest


class IndexService:
    def __init__(self, document_root: Path, index_root: Path, *, embedder=None, max_pending=16):
        self.document_root, self.index_root = document_root, index_root
        self.embedder, self.max_pending = embedder, max_pending
        self.store = JobStore(index_root)
        self.tasks = set()
        self.lock, self.worker = asyncio.Lock(), asyncio.Semaphore(1)

    def recover(self):
        return self.store.recover_interrupted()

    async def submit(self, req: IndexJobRequest) -> dict:
        async with self.lock:
            existing = self.store.get(req.job_id)
            rhash = request_hash(req)
            if existing:
                if existing["request_hash"] != rhash:
                    raise JobConflict()
                return existing["job"]
            if len(self.tasks) >= self.max_pending:
                raise ServiceError(503, "SERVICE_UNAVAILABLE", "색인 대기 작업이 많습니다.", retryable=True)
            return run_index_job(
                req.model_dump(mode="json", exclude_unset=True),
                document_root=self.document_root, index_root=self.index_root,
                embedder=self.embedder, background=self._enqueue,
            )

    def _enqueue(self, function, *args):
        task = asyncio.create_task(self._run(function, args))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _run(self, function, args):
        job_id = args[0].job_id
        async with self.worker:
            try:
                await asyncio.to_thread(function, *args)
            except Exception:
                # Do not persist exception text, paths, provider bodies or credentials.
                record = self.store.get(job_id)
                record["job"].update(status="failed", error_code="INDEXING_FAILED",
                                     error_message="색인 작업을 처리하지 못했습니다",
                                     updated_at=datetime.now(timezone.utc).isoformat())
                self.store.put(job_id, record)

    def get(self, job_id):
        record = self.store.get(job_id)
        if record is None:
            raise ServiceError(404, "RESOURCE_NOT_FOUND", "색인 작업을 찾을 수 없습니다.")
        return record["job"]

    async def close(self):
        # Threads cannot be cancelled safely while writing a revision.
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks))
