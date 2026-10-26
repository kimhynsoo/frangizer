"""A's immutable FAISS artifacts; exact scope filtering precedes similarity ranking."""
import asyncio
import hashlib
import json
import logging
import re
import time
from collections import OrderedDict
from pathlib import Path
from threading import RLock
from dataclasses import replace

import faiss
import numpy as np

from ..errors import ServiceError, unavailable, upstream_error
from ..schemas import AllowedVersion, IndexedChunkMetadata
from .base import Evidence, EvidenceBatch, is_allowed, validate_batch, parent_identity, context_related, reference_targets, delegation_definition
from .hybrid import rank_hybrid, text_terms
from ..structure import infer_hierarchy

log = logging.getLogger('uvicorn.error')


class FaissRetriever:
    def __init__(self, index_root: Path, embedder, *, top_k=5, cache_size=16, prefetch_k=30):
        self.root, self.embedder = Path(index_root), embedder
        self.top_k, self.cache_size = top_k, cache_size
        self.cache = OrderedDict()
        self.lock = RLock()
        self.prefetch_k = prefetch_k

    def warmup(self):
        """Prepare local tokenization/cache before accepting the first user request."""
        text_terms('')
        with self.lock:
            for path in (self.root / 'revisions').glob('idx-*/build-manifest.json'):
                try:
                    build = json.loads(path.read_text())
                    version = AllowedVersion(**{k: build[k] for k in ('document_id', 'version_id', 'index_revision')})
                    items, _ = self._load(version)
                    for e in self._with_context(items):
                        text_terms(e.metadata.title + '\n' + e.search_text)
                except (ServiceError, OSError, ValueError, KeyError):
                    log.warning('retrieval_warmup_skipped revision=%s', path.parent.name)

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
                if build.get('chunker_version') == 'chunker-v6-context':
                    if (not isinstance(rec.get('search_text'), str)
                            or hashlib.sha256(rec['search_text'].encode()).hexdigest() != rec.get('search_sha256')):
                        raise ValueError('artifact search text')
                section = rec.get('section', {})
                parent = section.get('parent_section_key', meta.section_key)
                part = section.get('part', 1)
                if (not isinstance(parent, str) or not (meta.section_key == parent or meta.section_key.startswith(parent + '.'))
                        or not isinstance(part, int) or part < 1):
                    raise ValueError('artifact parent section')
                table = rec.get('table')
                if table is not None and not isinstance(table, dict):
                    raise ValueError('artifact table')
                items.append(Evidence(text, meta, legal_temporal_unreviewed=(
                    meta.material_type == "official" and review.get(
                        "legal_temporal_reviewed", quality.get("legal_temporal_reviewed"),
                    ) is not True
                ), quality_flags=tuple(flags), parent_section_key=parent, section_part=part, table=table))
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
        return self._with_context(items), vectors

    def _with_context(self, items):
        rows = [{'metadata': e.metadata.model_dump(mode='json'), 'text': e.text,
                 'review': {'quality_flags': list(e.quality_flags)}, 'truncated': e.truncated,
                 'section': {'parent_section_key': e.parent_section_key or e.metadata.section_key,
                             'part': e.section_part}} for e in items]
        hierarchy = infer_hierarchy(rows)
        return [replace(e, hierarchy=hierarchy[e.metadata.chunk_id]) for e in items]

    async def search(self, query, context):
        try:
            return await asyncio.to_thread(self._search, query, context)
        except ServiceError:
            raise
        except Exception:
            raise upstream_error() from None

    def _search(self, query, context):
        started = time.monotonic()
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
        similarities, positions = restricted.search(query_vector, len(items))
        # Both retrieval methods use the same resolved/decomposed query.
        ranked = rank_hybrid(query, items, positions[0], similarities[0],
                             prefetch_k=self.prefetch_k)[:self.prefetch_k]
        candidates = [items[pos] for pos in ranked]
        notes = ()
        selected, size = [], 0
        for pos in ranked:
            if len(selected) == self.top_k:
                break
            item = items[int(pos)]
            if size + len(item.text) <= 12000:
                selected.append(item)
                size += len(item.text)
        primary_ids = tuple(e.metadata.chunk_id for e in selected)
        by_id = {e.metadata.chunk_id: e for e in items}
        extra = []
        for anchor in tuple(selected):
            extra.extend(by_id[i] for i in (anchor.hierarchy or {}).get('source_chunk_ids', ()) if i in by_id)
            extra.extend(e for e in items if parent_identity(e) == parent_identity(anchor) and e != anchor)
        present = set(primary_ids)
        omitted = False
        for item in extra:
            if item.metadata.chunk_id in present or {'table_needs_review', 'table_pairing_mismatch', 'table_detection_failed'}.intersection(item.quality_flags):
                continue
            if len(selected) >= 20 or size + len(item.text) > 12000:
                omitted = True
                continue
            selected.append(item)
            present.add(item.metadata.chunk_id)
            size += len(item.text)
        if omitted:
            notes += ('상위 문맥·같은 조항의 일부를 크기 제한으로 생략했습니다. 확인하지 않은 범위를 단정하지 마세요.',)
        log.info('retrieval question_id=%s candidates=%d selected=%s elapsed_ms=%d',
                 context.question_id, len(candidates), [e.metadata.chunk_id for e in selected],
                 int((time.monotonic()-started)*1000))
        return validate_batch(EvidenceBatch(tuple(selected), count, self.embedder.model, notes,
                                           tuple(candidates)), context)

    async def read_sections(self, chunk_ids, context):
        items, _ = await asyncio.to_thread(self._allowed, context)
        by_id = {item.metadata.chunk_id: item for item in items}
        if any(cid not in by_id for cid in chunk_ids):
            raise upstream_error()
        anchors = [by_id[cid] for cid in dict.fromkeys(chunk_ids)]
        selected, size, omitted_ids = [], 0, []
        for item in anchors:
            if len(selected) >= 20 or size + len(item.text) > 12000:
                omitted_ids.append(item.metadata.chunk_id)
                continue
            selected.append(item)
            size += len(item.text)
        # Expand only anchors that actually fit. Omitted requests remain unread.
        family = [e for e in items if e not in anchors and any(context_related(a, e) for a in selected)]
        blocked_flags = {'table_needs_review', 'table_pairing_mismatch', 'table_detection_failed'}
        blocked = [e for e in family if blocked_flags.intersection(e.quality_flags)]
        siblings = sorted((e for e in family if e not in blocked), key=lambda e: (not any(delegation_definition(a, e) for a in anchors), e.section_part))
        for item in siblings:
            if len(selected) >= 20 or size + len(item.text) > 12000:
                omitted_ids.append(item.metadata.chunk_id)
                continue
            selected.append(item)
            size += len(item.text)
        selected.sort(key=lambda e: (e.metadata.document_id, e.parent_section_key or e.metadata.section_key,
                                     e.section_part, e.metadata.section_key))
        notes = []
        if omitted_ids:
            notes.append('요청한 조각·연결 문맥 일부가 응답 크기 제한으로 생략되었습니다. '
                         '확인하지 않은 조건은 단정하지 마세요. 미확인 조각: ' + ', '.join(omitted_ids))
        if blocked:
            notes.append('같은 부모 섹션의 미검수 표는 제공하지 않았습니다. 누락된 조건·수치는 추측하지 마세요.')
        available = {(re.sub(r'\s+', '', e.metadata.title), re.sub(r'\s+', '', e.metadata.article or '')) for e in selected}
        missing = [(t, a) for anchor in anchors for t, a in reference_targets(anchor)
                   if (re.sub(r'\s+', '', t), a) not in available]
        if missing:
            notes.append('허용된 조회 결과에서 확인하지 못한 참조: ' + ' / '.join(f'{t} {a}' for t, a in dict.fromkeys(missing)))
        return validate_batch(EvidenceBatch(tuple(selected), notes=tuple(notes)), context)

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
