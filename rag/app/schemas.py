"""Implemented query contracts; the OpenAPI documents remain the source of truth."""
from datetime import date
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Id = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
Role = Literal["prospect", "owner", "hq"]
Category = Literal["franchise", "recipe", "hygiene", "consumer", "other"]
Status = Literal["answered", "needs_review", "handoff"]
ClassificationStatus = Literal["pending", "classified", "failed", "corrected"]
MaterialType = Literal["official", "public_disclosure", "synthetic"]
Basis = Literal["past", "current", "future"]
Count = Annotated[int, Field(ge=0, strict=True)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Principal(ContractModel):
    user_id: Id
    role: Role
    brand_id: Id
    store_id: Id | None
    permissions: tuple[str, ...]


class AllowedVersion(ContractModel):
    document_id: Id
    version_id: Id
    index_revision: Id


class HistoryTurn(ContractModel):
    question: Annotated[str, Field(max_length=2000)]
    answer: Annotated[str, Field(max_length=6000)]


class InternalQuery(ContractModel):
    question_id: Id
    attempt_id: Id
    conversation_id: Id
    question: Annotated[str, Field(min_length=1, max_length=2000, pattern=r"\S")]
    principal: Principal
    as_of: AwareDatetime
    basis: Basis
    scope_revision: Id
    allowed_versions: tuple[AllowedVersion, ...]
    history: Annotated[tuple[HistoryTurn, ...], Field(max_length=6)]
    deadline_ms: Annotated[int, Field(ge=1000, le=30000, strict=True)]

    @model_validator(mode="after")
    def unique_versions(self):
        if len(set(self.allowed_versions)) != len(self.allowed_versions):
            raise ValueError("allowed_versions must be unique")
        return self


class OriginalSource(ContractModel):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    url: str | None
    edition: Annotated[str, Field(min_length=1, max_length=100)]
    publisher: Annotated[str, Field(min_length=1, max_length=100)]
    acquired_on: date
    note: Annotated[str, Field(max_length=2000)]


class IndexedChunkMetadata(ContractModel):
    chunk_id: Id
    brand_id: Id
    document_id: Id
    version_id: Id
    index_revision: Id
    title: str
    version: str
    visibility: Role
    material_type: MaterialType
    is_demo: Annotated[bool, Field(strict=True)]
    page: Annotated[int, Field(ge=1, strict=True)] | None
    article: str | None
    section_key: Id
    effective_from: AwareDatetime
    effective_until: AwareDatetime | None
    original_source: OriginalSource
    embedding_model: str
    content_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]

    @model_validator(mode="after")
    def valid_period(self):
        if self.effective_until is not None and self.effective_until <= self.effective_from:
            raise ValueError("effective_until must follow effective_from")
        if self.material_type == "synthetic" and not self.is_demo:
            raise ValueError("synthetic materials must be marked is_demo")
        return self


class Source(ContractModel):
    chunk_id: Id
    document_id: Id
    version_id: Id
    title: str
    version: str
    location: str
    page: Annotated[int, Field(ge=1, strict=True)] | None
    article: str | None
    effective_from: AwareDatetime
    effective_until: AwareDatetime | None
    material_type: MaterialType
    is_demo: Annotated[bool, Field(strict=True)]
    excerpt: Annotated[str, Field(max_length=1500)]
    detail_url: str
    file_url: str


class RagAnswer(ContractModel):
    question_id: Id
    conversation_id: Id
    answer: Annotated[str, Field(min_length=1)]
    steps: tuple[str, ...]
    status: Status
    needs_review: bool
    handoff: bool
    sources: tuple[Source, ...]
    follow_up_questions: tuple[str, ...]
    handoff_reason: str | None
    category: Category
    topic_id: Id | None
    classification_status: ClassificationStatus
    as_of: AwareDatetime
    basis: Basis
    latency_ms: Count

    @model_validator(mode="after")
    def valid_status(self):
        expected = {"answered": (False, False), "needs_review": (True, False), "handoff": (True, True)}
        if (self.needs_review, self.handoff) != expected[self.status]:
            raise ValueError("inconsistent answer status")
        if self.status == "answered" and (not self.sources or self.handoff_reason is not None):
            raise ValueError("answered requires sources and no handoff_reason")
        if self.status == "needs_review" and not (self.follow_up_questions or self.handoff_reason):
            raise ValueError("needs_review requires a reason or a follow-up question")
        if self.status == "handoff" and not self.handoff_reason:
            raise ValueError("handoff requires a reason")
        return self


class Usage(ContractModel):
    input_tokens: Count
    output_tokens: Count
    embedding_tokens: Count
    model: str
    embedding_model: str
    estimated_cost_usd: Annotated[float, Field(ge=0)] | None


class Classification(ContractModel):
    category: Category
    topic_id: Id | None
    status: ClassificationStatus
    version: str


class Retrieval(ContractModel):
    candidate_count: Count
    used_chunk_ids: tuple[Id, ...]
    llm_called: bool


class InternalAnswer(ContractModel):
    result: RagAnswer
    scope_revision: Id
    classification: Classification
    usage: Usage
    retrieval: Retrieval


class AnswerEnvelope(ContractModel):
    data: InternalAnswer
    request_id: Id
