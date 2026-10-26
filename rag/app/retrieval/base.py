import hashlib
import re
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
    parent_section_key: str | None = None
    section_part: int = 1
    hierarchy: dict | None = None
    table: dict | None = None

    @property
    def search_text(self):
        from ..structure import search_text
        return search_text(self.text, self.hierarchy or {})


def parent_identity(item):
    m = item.metadata
    return (m.document_id, m.version_id, m.index_revision,
            item.parent_section_key or m.section_key, m.effective_from, m.effective_until)


ARTICLE = r'제\s*\d+\s*조(?:\s*의\s*\d+)?'
REFERENCE = re.compile(r'「(?P<title>[^」]+)」|(?:(?<![가-힣])(?P<relative>같은\s*법|같은\s*영|같은\s*규칙|법|영|규칙)\s*)?(?P<article>' + ARTICLE + ')')


def reference_targets(item, *, text=None):
    """One-hop, explicit references only; never guess what a delegated rule says."""
    body = item.text if text is None else text
    title = item.metadata.title
    base = re.sub(r'\s*시행(?:령|규칙)$', '', title)
    titles = {'법': base, '영': base + ' 시행령', '규칙': base + ' 시행규칙'}
    targets = []
    antecedent, title_end, previous_end = None, None, 0
    for match in REFERENCE.finditer(body):
        if re.search(r'\n\s*\n|[①-⑳]', body[previous_end:match.start()]):
            antecedent, title_end = None, None
        previous_end = match.end()
        if match['title']:
            antecedent, title_end = match['title'], match.end()
            continue
        relative = re.sub(r'\s+', '', match['relative'] or '')
        if relative.startswith('같은'):
            # A demonstrative needs a named antecedent of the matching kind.
            # Never substitute the current document when that antecedent is absent.
            kind = ('규칙' if antecedent and antecedent.endswith('시행규칙') else
                    '영' if antecedent and antecedent.endswith('시행령') else
                    '법' if antecedent and re.search(r'법(?:률)?$', antecedent) else None)
            if kind != relative[2:]:
                continue
            target_title = antecedent
        elif relative:
            target_title = antecedent = titles[relative]
        elif title_end is not None and not body[title_end:match.start()].strip():
            target_title = antecedent
        elif re.search(r'(?:법(?:률)?|령|규칙)\s*$', body[max(0, match.start() - 60):match.start()]):
            antecedent, title_end = None, None
            continue  # An unparsed external title is not a reference to this document.
        else:
            target_title = title
        targets.append((target_title, re.sub(r'\s+', '', match['article'])))
    return tuple(dict.fromkeys(targets))


DELEGATION = re.compile(r'(대통령령|총리령|[가-힣]+부령)으로\s*정하는')


def has_delegation(item):
    return bool(DELEGATION.search(item.text))


def delegation_definition(anchor, item, *, anchor_text=None):
    """Match an explicit delegated-term definition in the same legal family.

    A reverse citation alone is insufficient: the child must quote a delegated
    phrase from the anchor and explicitly define it, not merely mention it.
    """
    compact = lambda text: re.sub(r'\s+', '', text)
    anchor_text = anchor.text if anchor_text is None else anchor_text
    title = anchor.metadata.title
    base = re.sub(r'\s*시행(?:령|규칙)$', '', title)
    if (not DELEGATION.search(anchor_text)
            or compact(item.metadata.title) not in {compact(base + ' 시행령'), compact(base + ' 시행규칙')}
            or compact(title) == compact(item.metadata.title)):
        return False
    target = (compact(title), compact(anchor.metadata.article or ''))
    if not target[1] or target not in {(compact(t), compact(a)) for t, a in reference_targets(item)}:
        return False
    phrases = re.findall(r'[“「"]([^”」"]{1,200})[”」"]\s*(?:이란|란)', item.text)
    for phrase in phrases:
        match = DELEGATION.search(phrase)
        if match:
            kind = '시행령' if match[1] == '대통령령' else '시행규칙'
            if compact(item.metadata.title) == compact(base + ' ' + kind) and compact(phrase) in compact(anchor_text):
                return True
    return False


def context_related(anchor, item):
    if parent_identity(anchor) == parent_identity(item):
        return True
    article = re.sub(r'\s+', '', item.metadata.article or '')
    title = re.sub(r'\s+', '', item.metadata.title)
    return (any(re.sub(r'\s+', '', t) == title and a == article for t, a in reference_targets(anchor))
            or delegation_definition(anchor, item))


@dataclass(frozen=True)
class EvidenceBatch:
    items: tuple[Evidence, ...] = ()
    embedding_tokens: int = 0
    embedding_model: str = "not-used"
    notes: tuple[str, ...] = ()
    candidates: tuple[Evidence, ...] = ()


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
    if (batch.embedding_tokens < 0 or len(batch.items) > 20 or len(batch.candidates) > 30
            or sum(len(e.text) for e in batch.items) > 12000):
        raise upstream_error()
    seen = {}
    for item in (*batch.items, *batch.candidates):
        if not is_allowed(item, context):
            raise upstream_error()
        if not item.text.strip() or len(item.text) > 12000:
            raise upstream_error()
        if hashlib.sha256(item.text.encode()).hexdigest() != item.metadata.content_sha256:
            raise upstream_error()
        if item.metadata.chunk_id in seen and seen[item.metadata.chunk_id] != item:
            raise upstream_error()
        seen[item.metadata.chunk_id] = item
    if (len({e.metadata.chunk_id for e in batch.items}) != len(batch.items)
            or len({e.metadata.chunk_id for e in batch.candidates}) != len(batch.candidates)):
        raise upstream_error()
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
