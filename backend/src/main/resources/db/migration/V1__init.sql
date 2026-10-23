-- 시각 컬럼은 모두 UTC로 저장한다. KST 변환은 애플리케이션에서 한다.

CREATE TABLE regions (
    code VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100) NOT NULL
);

CREATE TABLE stores (
    store_id    VARCHAR(100) PRIMARY KEY,
    brand_id    VARCHAR(100) NOT NULL,
    name        VARCHAR(200) NOT NULL,
    region_code VARCHAR(50)  NOT NULL,
    active      BOOLEAN      NOT NULL DEFAULT TRUE
);

CREATE TABLE users (
    id            VARCHAR(100) PRIMARY KEY,
    username      VARCHAR(50)  NOT NULL UNIQUE,
    password_hash VARCHAR(100),
    role          VARCHAR(20)  NOT NULL,
    brand_id      VARCHAR(100) NOT NULL,
    store_id      VARCHAR(100),
    permissions   VARCHAR(500) NOT NULL -- 쉼표 구분
);

CREATE TABLE topics (
    id       VARCHAR(100) PRIMARY KEY,
    label    VARCHAR(200) NOT NULL,
    category VARCHAR(20)  NOT NULL,
    active   BOOLEAN      NOT NULL DEFAULT TRUE
);

CREATE TABLE documents (
    document_id VARCHAR(100) PRIMARY KEY,
    brand_id    VARCHAR(100) NOT NULL,
    created_at  DATETIME(6)  NOT NULL
);

-- 판본은 변경 불가. 제목·분류·공개 범위도 판본 단위로 보존한다.
CREATE TABLE document_versions (
    version_id      VARCHAR(100) PRIMARY KEY,
    document_id     VARCHAR(100) NOT NULL,
    version         VARCHAR(50)  NOT NULL,
    title           VARCHAR(200) NOT NULL,
    category        VARCHAR(20)  NOT NULL,
    material_type   VARCHAR(30)  NOT NULL,
    is_demo         BOOLEAN      NOT NULL,
    visibility      VARCHAR(20)  NOT NULL,
    effective_from  DATETIME(6)  NOT NULL,
    effective_until DATETIME(6),
    lifecycle       VARCHAR(20)  NOT NULL,
    index_status    VARCHAR(20)  NOT NULL,
    index_revision  VARCHAR(100),
    change_summary  TEXT         NOT NULL,
    original_source TEXT         NOT NULL,
    indexing_policy TEXT         NOT NULL,
    file_name       VARCHAR(255) NOT NULL,
    media_type      VARCHAR(50)  NOT NULL,
    file_size_bytes BIGINT       NOT NULL,
    sha256          CHAR(64)     NOT NULL,
    page_count      INT,
    storage_key     VARCHAR(500) NOT NULL,
    created_by      VARCHAR(100) NOT NULL,
    created_at      DATETIME(6)  NOT NULL,
    review_note     TEXT,
    approved_by     VARCHAR(100),
    approved_at     DATETIME(6),
    published_by    VARCHAR(100),
    published_at    DATETIME(6),
    CONSTRAINT uq_document_version UNIQUE (document_id, version),
    CONSTRAINT fk_version_document FOREIGN KEY (document_id) REFERENCES documents (document_id)
);

CREATE TABLE index_jobs (
    job_id         VARCHAR(100) PRIMARY KEY,
    version_id     VARCHAR(100) NOT NULL,
    status         VARCHAR(20)  NOT NULL,
    chunk_count    INT          NOT NULL DEFAULT 0,
    index_revision VARCHAR(100),
    error_code     VARCHAR(100),
    error_message  TEXT,
    created_at     DATETIME(6)  NOT NULL,
    updated_at     DATETIME(6)  NOT NULL
);

-- 점포·지역은 접수 당시 스냅샷. 질문 원문은 저장하지 않는다.
CREATE TABLE question_events (
    question_id            VARCHAR(100) PRIMARY KEY,
    request_id             VARCHAR(100) NOT NULL UNIQUE,
    conversation_id        VARCHAR(100) NOT NULL,
    brand_id               VARCHAR(100) NOT NULL,
    user_id                VARCHAR(100) NOT NULL,
    user_role              VARCHAR(20)  NOT NULL,
    store_id               VARCHAR(100),
    store_name             VARCHAR(200),
    region_code            VARCHAR(50),
    region_name            VARCHAR(100),
    created_at             DATETIME(6)  NOT NULL,
    updated_at             DATETIME(6)  NOT NULL,
    question_redacted      TEXT         NOT NULL,
    answer_status          VARCHAR(20)  NOT NULL,
    resolution_status      VARCHAR(20)  NOT NULL DEFAULT 'unknown',
    category               VARCHAR(20)  NOT NULL DEFAULT 'other',
    topic_id               VARCHAR(100),
    classification_status  VARCHAR(20)  NOT NULL DEFAULT 'pending',
    classification_version VARCHAR(50)  NOT NULL DEFAULT 'none',
    result                 LONGTEXT,
    error_code             VARCHAR(50),
    latency_ms             INT,
    attempt_count          INT          NOT NULL DEFAULT 1,
    revision               INT          NOT NULL DEFAULT 1,
    rag_meta               LONGTEXT, -- usage·retrieval 운영 기록
    review_note            TEXT,
    updated_by             VARCHAR(100)
);
CREATE INDEX ix_question_brand_created ON question_events (brand_id, created_at);
CREATE INDEX ix_question_conversation ON question_events (conversation_id);

CREATE TABLE question_sources (
    question_id     VARCHAR(100) NOT NULL,
    chunk_id        VARCHAR(100) NOT NULL,
    document_id     VARCHAR(100) NOT NULL,
    version_id      VARCHAR(100) NOT NULL,
    page_or_article VARCHAR(100),
    PRIMARY KEY (question_id, chunk_id)
);

CREATE TABLE question_feedback (
    question_id  VARCHAR(100) PRIMARY KEY,
    helpful      BOOLEAN      NOT NULL,
    submitted_at DATETIME(6)  NOT NULL
);

CREATE TABLE notices (
    notice_id    VARCHAR(100) PRIMARY KEY,
    brand_id     VARCHAR(100) NOT NULL,
    document_id  VARCHAR(100) NOT NULL,
    version_id   VARCHAR(100) NOT NULL,
    title        VARCHAR(200) NOT NULL,
    published_at DATETIME(6)  NOT NULL
);

-- 배포 시점에 고정한 대상 점포
CREATE TABLE notice_targets (
    notice_id VARCHAR(100) NOT NULL,
    store_id  VARCHAR(100) NOT NULL,
    PRIMARY KEY (notice_id, store_id)
);

CREATE TABLE notice_acknowledgements (
    notice_id       VARCHAR(100) NOT NULL,
    store_id        VARCHAR(100) NOT NULL,
    acknowledged_by VARCHAR(100) NOT NULL,
    acknowledged_at DATETIME(6)  NOT NULL,
    PRIMARY KEY (notice_id, store_id)
);

CREATE TABLE idempotency_keys (
    user_id      VARCHAR(100) NOT NULL,
    operation    VARCHAR(250) NOT NULL,
    idem_key     VARCHAR(100) NOT NULL,
    request_hash CHAR(64)     NOT NULL,
    resource_id  VARCHAR(100),
    status_code  INT,
    response     LONGTEXT, -- NULL이면 처리 중
    created_at   DATETIME(6)  NOT NULL,
    PRIMARY KEY (user_id, operation, idem_key)
);
