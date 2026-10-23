"""색인 작업 입출력 모델. docs/api/internal-rag.openapi.yaml과 필드·제약을 맞춘다.

B가 rag/app/schemas.py를 만들면 IndexJobRequest·IndexedChunkMetadata는 그쪽으로 옮기고
여기서는 import만 한다. 계약과 다르면 계약(docs/api)이 기준이다.
"""
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, AnyUrl, BaseModel, ConfigDict, Field, StringConstraints, field_validator

Id = Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
Category = Literal["franchise", "recipe", "hygiene", "consumer", "other"]
MaterialType = Literal["official", "public_disclosure", "synthetic"]
Role = Literal["prospect", "owner", "hq"]
JobStatus = Literal["queued", "running", "succeeded", "failed"]


def _require_tz(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("시간대가 필요하다 (RFC 3339)")
    return value


Timestamp = Annotated[datetime, AfterValidator(_require_tz)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OriginalSource(Strict):
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    url: AnyUrl | None
    edition: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    publisher: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    acquired_on: date
    note: Annotated[str, StringConstraints(max_length=2000)]


class IndexingPolicy(Strict):
    # 계약상 선택 필드: 생략은 허용, null은 불허. 출력할 때는 exclude_unset으로 생략을 유지한다.
    include_pages: Annotated[list[Annotated[int, Field(ge=1)]], Field(min_length=1)] | None = None
    exclude_pages: list[Annotated[int, Field(ge=1)]]
    legal_temporal_reviewed: bool

    @field_validator("include_pages", mode="before")
    @classmethod
    def _no_explicit_null(cls, v):
        if v is None:
            raise ValueError("include_pages는 생략하거나 1개 이상의 쪽 목록이어야 한다")
        return v


class SourceFile(Strict):
    storage_key: Annotated[str, StringConstraints(pattern=r"^documents/[A-Za-z0-9._/-]+$")]
    sha256: Sha256
    media_type: Literal["application/pdf", "text/plain", "text/markdown"]


class IndexJobRequest(Strict):
    job_id: Id
    brand_id: Id
    document_id: Id
    version_id: Id
    title: str
    version: str
    category: Category
    material_type: MaterialType
    is_demo: bool
    visibility: Role
    effective_from: Timestamp
    original_source: OriginalSource
    indexing_policy: IndexingPolicy
    source: SourceFile


class IndexedChunkMetadata(Strict):
    chunk_id: Id
    brand_id: Id
    document_id: Id
    version_id: Id
    index_revision: Id
    title: str
    version: str
    visibility: Role
    material_type: MaterialType
    is_demo: bool
    page: Annotated[int, Field(ge=1)] | None
    article: str | None
    section_key: Id
    effective_from: Timestamp
    effective_until: Timestamp | None
    original_source: OriginalSource
    embedding_model: str
    content_sha256: Sha256


class IndexJob(Strict):
    job_id: Id
    document_id: Id
    version_id: Id
    status: JobStatus
    chunk_count: Annotated[int, Field(ge=0)]
    index_revision: Id | None
    error_code: str | None
    error_message: str | None
    created_at: Timestamp
    updated_at: Timestamp
