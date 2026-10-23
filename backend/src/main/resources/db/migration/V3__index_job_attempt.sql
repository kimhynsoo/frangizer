-- RAG 재시작(INTERRUPTED) 시 같은 작업을 새 RAG job_id({job_id}-r{attempt})로 다시 제출한 횟수.
ALTER TABLE index_jobs ADD COLUMN attempt INT NOT NULL DEFAULT 0;
