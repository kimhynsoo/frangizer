import hashlib
from dataclasses import dataclass
from typing import Protocol

from ..errors import unavailable, upstream_error
from ..schemas import IndexedChunkMetadata, InternalQuery


@dataclass(frozen=True)
class Evidence:
    text: str
    metadata: IndexedChunkMetadata
    truncated: bool = False
    legal_temporal_unreviewed: bool = False
    quality_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceBatch:
    items: tuple[Evidence, ...] = ()
    embedding_tokens: int = 0
    embedding_model: str = "not-used"


def is_allowed(item: Evidence, context: InternalQuery) -> bool:
    m = item.metadata
    roles = {"prospect": 0, "owner": 1, "hq": 2}
    return (
        m.brand_id == context.principal.brand_id
        and roles[context.principal.role] >= roles[m.visibility]
        and any((m.document_id, m.version_id, m.index_revision) ==
                (v.document_id, v.version_id, v.index_revision) for v in context.allowed_versions)
        and m.effective_from <= context.as_of
        and (m.effective_until is None or context.as_of < m.effective_until)
    )


def validate_batch(batch: EvidenceBatch, context: InternalQuery) -> EvidenceBatch:
    # The real adapter must filter BEFORE ranking. This is a second boundary check.
    if batch.embedding_tokens < 0 or len(batch.items) > 20 or sum(len(e.text) for e in batch.items) > 12000:
        raise upstream_error()
    seen: set[str] = set()
    for item in batch.items:
        if not is_allowed(item, context):
            raise upstream_error()
        if not item.text.strip() or len(item.text) > 12000:
            raise upstream_error()
        if hashlib.sha256(item.text.encode()).hexdigest() != item.metadata.content_sha256:
            raise upstream_error()
        if item.metadata.chunk_id in seen:
            raise upstream_error()
        seen.add(item.metadata.chunk_id)
    return batch


class Retriever(Protocol):
    ready: bool

    async def search(self, query: str, context: InternalQuery) -> EvidenceBatch: ...

    async def read_sections(self, chunk_ids: tuple[str, ...], context: InternalQuery) -> EvidenceBatch: ...


class UnavailableRetriever:
    ready = False

    async def search(self, query, context):
        raise unavailable()

    async def read_sections(self, chunk_ids, context):
        raise unavailable()
