"""A's immutable FAISS artifacts; exact scope filtering precedes similarity ranking."""
import asyncio
import hashlib
import json
from collections import OrderedDict
from pathlib import Path
from threading import RLock

import faiss
import numpy as np

from ..errors import ServiceError, unavailable, upstream_error
from ..schemas import AllowedVersion, IndexedChunkMetadata
from .base import Evidence, EvidenceBatch, is_allowed, validate_batch


class FaissRetriever:
    def __init__(self, index_root: Path, embedder, *, top_k=5, cache_size=8):
        self.root, self.embedder = Path(index_root), embedder
        self.top_k, self.cache_size = top_k, cache_size
        self.cache = OrderedDict()
        self.lock = RLock()

    @property
    def ready(self):
        with self.lock:
            for directory in (self.root / "revisions").glob("idx-*"):
                try:
                    build = json.loads((directory / "build-manifest.json").read_text())
                    self._load(AllowedVersion(**{k: build[k] for k in (
                        "document_id", "version_id", "index_revision",
                    )}))
                    return True
                except (ServiceError, OSError, ValueError, KeyError):
                    continue
        return False

    def _load(self, version):
        key = (version.document_id, version.version_id, version.index_revision)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        directory = (self.root / "revisions" / version.index_revision).resolve()
        if not directory.is_relative_to((self.root / "revisions").resolve()):
            raise upstream_error()
        if not directory.is_dir():
            raise unavailable()
        try:
            build = json.loads((directory / "build-manifest.json").read_text())
            if (build["document_id"], build["version_id"], build["index_revision"]) != key:
                raise ValueError("artifact identity")
            if (build["embedding_model"] != self.embedder.model or build["dimension"] != self.embedder.dim
                    or build["metric"] != "inner_product_on_l2_normalized"):
                raise unavailable()
            index = faiss.read_index(str(directory / "index.faiss"))
            if not isinstance(index, faiss.IndexFlatIP) or index.d != self.embedder.dim:
                raise ValueError("index type or dimension")
            records = [json.loads(line) for line in (directory / "chunks.jsonl").read_text().splitlines()]
            if not (len(records) == index.ntotal == build["chunk_count"] > 0):
                raise ValueError("artifact count")
            quality = json.loads((directory / "quality-report.json").read_text())
            items = []
            for rec in records:
                meta = IndexedChunkMetadata.model_validate(rec["metadata"])
                text = rec["text"]
                review = rec.get("review", {})
                flags = review.get("quality_flags", [])
                if not isinstance(flags, list) or any(not isinstance(flag, str) for flag in flags):
                    raise ValueError("artifact review flags")
                if ((meta.document_id, meta.version_id, meta.index_revision) != key
                        or meta.embedding_model != self.embedder.model
                        or not isinstance(text, str) or not 0 < len(text) <= 12000
                        or hashlib.sha256(text.encode()).hexdigest() != meta.content_sha256):
                    raise ValueError("artifact metadata or content")
                items.append(Evidence(text, meta, legal_temporal_unreviewed=(
                    meta.material_type == "official" and review.get(
                        "legal_temporal_reviewed", quality.get("legal_temporal_reviewed"),
                    ) is not True
                ), quality_flags=tuple(flags)))
            ids = [e.metadata.chunk_id for e in items]
            if ids != build["chunk_ids"] or len(ids) != len(set(ids)):
                raise ValueError("artifact mapping")
            vectors = index.reconstruct_n(0, index.ntotal)
            if not np.isfinite(vectors).all() or not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-4):
                raise ValueError("artifact vectors")
        except ServiceError:
            raise
        except Exception:
            raise upstream_error() from None
        loaded = (tuple(items), vectors)
        self.cache[key] = loaded
        while len(self.cache) > self.cache_size:
            self.cache.popitem(last=False)
        return loaded

    def _allowed(self, context):
        with self.lock:
            return self._allowed_locked(context)

    def _allowed_locked(self, context):
        items, vectors, seen = [], [], set()
        for version in context.allowed_versions:
            records, matrix = self._load(version)
            for pos, item in enumerate(records):
                if not is_allowed(item, context):
                    continue
                if item.metadata.chunk_id in seen:
                    raise upstream_error()
                seen.add(item.metadata.chunk_id)
                items.append(item)
                vectors.append(matrix[pos])
        return items, vectors

    async def search(self, query, context):
        try:
            return await asyncio.to_thread(self._search, query, context)
        except ServiceError:
            raise
        except Exception:
            raise upstream_error() from None

    def _search(self, query, context):
        items, vectors = self._allowed(context)
        if not items:
            return EvidenceBatch()
        if hasattr(self.embedder, "embed_with_usage"):
            query_vector, count = self.embedder.embed_with_usage([query])
        else:  # Injected offline test adapter.
            query_vector, count = self.embedder.embed([query]), 0
        query_vector = np.asarray(query_vector, dtype="float32")
        if (query_vector.shape != (1, self.embedder.dim) or not np.isfinite(query_vector).all()
                or np.linalg.norm(query_vector) == 0):
            raise upstream_error()
        faiss.normalize_L2(query_vector)
        # Only permitted rows enter this index, including temporal filtering.
        restricted = faiss.IndexFlatIP(self.embedder.dim)
        restricted.add(np.asarray(vectors, dtype="float32"))
        _, positions = restricted.search(query_vector, min(self.top_k, len(items)))
        selected, size = [], 0
        for pos in positions[0]:
            item = items[int(pos)]
            if size + len(item.text) <= 12000:
                selected.append(item)
                size += len(item.text)
        return validate_batch(EvidenceBatch(tuple(selected), count, self.embedder.model), context)

    async def read_sections(self, chunk_ids, context):
        # Chunk-granular sections: do not widen to similarly named unverified sections.
        items, _ = await asyncio.to_thread(self._allowed, context)
        selected = tuple(item for item in items if item.metadata.chunk_id in set(chunk_ids))
        if {e.metadata.chunk_id for e in selected} != set(chunk_ids):
            raise upstream_error()
        return validate_batch(EvidenceBatch(selected), context)

    async def recipe_candidates(self, context, document_id):
        def read():
            result = []
            with self.lock:
                for version in context.allowed_versions:
                    if version.document_id != document_id:
                        continue
                    items, _ = self._load(version)
                    permitted = tuple(e for e in items if is_allowed(e, context))
                    build = json.loads((self.root / "revisions" / version.index_revision / "build-manifest.json").read_text())
                    result.append((build["source_sha256"], permitted))
            return result
        return await asyncio.to_thread(read)
