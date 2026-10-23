# 가맹파트너 AI — API 명세서 v1

- 기준 문서: 가맹파트너 AI 서비스기획서 7장(2026-10-23 개정안), 본사 기능·대시보드 설계 8장
- 기계 판독 계약: [`public.openapi.yaml`](public.openapi.yaml) (공개 `/api/v1`), [`internal-rag.openapi.yaml`](internal-rag.openapi.yaml) (내부 RAG)
- 요청·응답 예시: [`examples/contract-cases.json`](examples/contract-cases.json), [`examples/upload-metadata.json`](examples/upload-metadata.json)
- 담당별 바로 시작: 백엔드 → [11.1](#111-백엔드--java-인증업무-api-8080), 프론트엔드 → [11.2](#112-프론트엔드--vue), RAG → [11.3](#113-rag--fastapi-8001), 통합 테스트 → [11.4](#114-통합-체크리스트계약-테스트)
- 상태: **구현 계약**이다. 서버 구현 완료를 뜻하지 않는다. 이 문서와 OpenAPI가 다르면 이 문서의 규칙이 우선하며, 발견 즉시 둘을 함께 고친다.

## 0. 서비스 구성과 호출 경로

```
Vue(Nginx) ──HTTPS /api/v1/* + Bearer JWT──▶ Java 인증·업무 API :8080
                                              ├─▶ 회원·업무 DB
                                              ├─▶ FastAPI RAG :8001   POST /rag-query, /internal/v1/index-jobs
                                              └─▶ FastAPI 음성 :8008  (선택, STT)
```

- 브라우저가 호출하는 것은 **`/api/v1/*`뿐**이다. RAG·음성·DB 포트는 외부에 열지 않는다.
- 교재의 `/login`은 게이트웨이에서 `/api/v1/login`으로, 교재의 FastAPI `/rag-query`는 내부 경로로 유지한다.
- 역할·브랜드·점포·승인 상태는 **서버가 JWT와 DB로 판정**한다. 요청 본문으로 받지 않는다.

## 1. 공통 규칙

| 항목 | 규칙 |
| --- | --- |
| Base URL | 개발 `http://localhost:8080/api/v1`, 배포 `/api/v1`(Nginx 동일 출처) |
| 전송 | 기본 `application/json; charset=utf-8`. 문서 등록·음성은 `multipart/form-data` |
| 인증 | `POST /login`, `GET /health`를 제외하고 `Authorization: Bearer <accessToken>` 필수 |
| 식별자 | 서버 발급 불투명 문자열, 1~100자, `^[A-Za-z0-9][A-Za-z0-9._-]*$` |
| 시각 | 시간대 포함 RFC 3339. 예: `2026-10-22T09:00:00+09:00`. DB는 UTC 저장, 집계 경계만 KST(Asia/Seoul) |
| 성공 응답 | `{ "data": ..., "request_id": "..." }`. 목록은 `meta` 추가. 예외: 로그인(교재 호환 `accessToken`), `/health`(최소 상태) |
| 목록 | `page`(기본 1), `page_size`(기본 20, 최대 100). `meta = {page, page_size, total, total_pages}` |
| 멱등성 | 아래 표의 `Idempotency-Key` 필수 API는 8~100자 키를 보낸다. 같은 사용자·경로·키·본문 → 기존 결과 반환, 같은 키·다른 본문 → `409 IDEMPOTENCY_CONFLICT`. 키는 24시간 보존 |
| 사용 제한 | 계정별 요청 수·파일 크기 제한. 초과 시 `429`/`413`, 재시도 가능하면 `Retry-After` 헤더 |
| 개인정보 | 질문 원문 대신 마스킹본(`question_redacted`)을 저장. 전화번호·고객명·주문번호 마스킹. 일반 로그·외부 모델 로그에 원문 금지 |

### 1.1 역할과 권한

| 역할 | 의미 | 기본 권한 |
| --- | --- | --- |
| `prospect` | 예비 가맹점주 | `rag:ask` — 공개 가맹 자료만 검색 |
| `owner` | 운영 가맹점주 | `rag:ask` — 소속 브랜드·점포의 운영 자료 검색, 공지 확인 |
| `hq` | 본사 담당자 | 아래 관리 권한을 계정별로 부여 |

관리 권한: `document:write`(등록·작업 조회), `document:approve`, `document:publish`, `analytics:read`, `analytics:write`.

문서 `visibility`는 **최소 허용 역할**이다: `prospect → prospect/owner/hq`, `owner → owner/hq`, `hq → hq`. 브랜드·점포 범위는 별도로 검증하며 `visibility`만으로 점포 접근을 넓히지 않는다.

### 1.2 공통 열거형

| 이름 | 값 |
| --- | --- |
| `Category` | `franchise`, `recipe`, `hygiene`, `consumer`, `other` |
| `AnswerStatus` | `answered`, `needs_review`, `handoff` |
| `EventAnswerStatus` | `processing`, `answered`, `needs_review`, `handoff`, `system_error` |
| `ResolutionStatus` | `unknown`, `open`, `in_progress`, `closed` |
| `ClassificationStatus` | `pending`, `classified`, `failed`, `corrected` |
| `MaterialType` | `official`(법령·고시), `public_disclosure`(정보공개서 원본), `synthetic`(합성 레시피) |
| `Lifecycle` | `draft → approved → published` |
| `IndexStatus` | `not_started → queued → running → ready / failed` |
| `JobStatus` | `queued`, `running`, `succeeded`, `failed` |

## 2. 엔드포인트 목록

경로 앞에는 모두 `/api/v1`이 붙는다. `doc_id`는 문서 계열, `ver_id`는 변경 불가 개별 판본 ID다. 단계: **MVP** 필수, **선택** 확장.

| # | 메서드·경로 | 목적 | 역할 / 권한 | 성공 | 멱등키 | 단계 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `POST /login` | 계정 인증·JWT 발급 | 비로그인 | 200 | | MVP |
| 2 | `GET /me` | 본인 역할·브랜드·점포·권한 | 인증 사용자 | 200 | | MVP |
| 3 | `POST /rag-query` | 근거 답변·확인 필요 상태 | 전체 / `rag:ask` | 200 | 필수 | MVP |
| 4 | `GET /conversations/{conversation_id}` | 본인 대화의 마스킹 기록 | 본인 | 200 | | MVP |
| 5 | `PUT /questions/{question_id}/feedback` | 답변 도움 여부 기록 | 본인 | 200 | | MVP |
| 6 | `GET /documents` | 허용된 문서 계열·대표 판본 | 인증 사용자 | 200 | | MVP |
| 7 | `POST /documents` | 문서 또는 새 판본 등록 | `hq` / `document:write` | 201 | 필수 | MVP |
| 8 | `GET /documents/{doc_id}/versions` | 허용된 판본 이력 | 판본 열람 권한 | 200 | | MVP |
| 9 | `GET /documents/{doc_id}/versions/{ver_id}` | 판본 메타·본문 조각·출처 | 판본 열람 권한 | 200 | | MVP |
| 10 | `GET /documents/{doc_id}/versions/{ver_id}/file` | 원본 파일 다운로드 | 판본 열람 권한 | 200 | | MVP |
| 11 | `POST /documents/{doc_id}/versions/{ver_id}/approve` | 검수 승인·비동기 색인 시작 | `hq` / `document:approve` | 202 | 필수 | MVP |
| 12 | `GET /index-jobs/{job_id}` | 색인 작업 상태 | `hq` / `document:write` | 200 | | MVP |
| 13 | `POST /documents/{doc_id}/versions/{ver_id}/publish` | 판본 배포·공지 생성 | `hq` / `document:publish` | 200 | 필수 | MVP |
| 14 | `GET /notices` | 본인 대상 변경 공지 | `owner`, `hq` | 200 | | MVP |
| 15 | `PUT /notices/{notice_id}/ack` | 소속 점포의 공지 확인 | 대상 점포 `owner` | 200 | | MVP |
| 16 | `GET /notices/{notice_id}/acknowledgements` | 대상 점포 확인·미확인 현황 | `hq` / `analytics:read` | 200 | | MVP |
| 17 | `GET /regions` | 지역 필터 목록 | `hq` / `analytics:read` | 200 | | MVP |
| 18 | `GET /stores` | 점포 필터 목록 | `hq` / `analytics:read` | 200 | | MVP |
| 19 | `GET /topics` | 통제된 질문 주제 목록 | `hq` / `analytics:read` | 200 | | MVP |
| 20 | `GET /analytics/questions` | 주간·지역·주제별 문의 집계 | `hq` / `analytics:read` | 200 | | MVP |
| 21 | `GET /analytics/question-events` | 마스킹 문의 목록·대표 질문 | `hq` / `analytics:read` | 200 | | MVP |
| 22 | `GET /analytics/question-events/{question_id}` | 문의 한 건 상세 | `hq` / `analytics:read` | 200 | | MVP |
| 23 | `PATCH /analytics/question-events/{question_id}` | 분류·처리 상태 수정 | `hq` / `analytics:write` | 200 | | MVP |
| 24 | `POST /stt` | 음성 → 질문 텍스트 | 전체 / `rag:ask` | 200 | | 선택 |
| 25 | `GET /health` | 최소 준비 상태 | 공개 | 200 / 503 | | MVP |

범위 밖: 회원가입, 비밀번호 복구, 토큰 갱신(만료 시 재로그인), 자유 SQL 조회, 외부 메시지 발송. `POST /analytics/weekly-insights`(AI 주간 요약)는 MVP 이후 별도 명세로 추가한다.

---

## 3. 인증

### 3.1 `POST /login`

요청

```json
{ "username": "demo-owner-a001", "password": "demo-password" }
```

| 필드 | 필수 | 검증 |
| --- | --- | --- |
| `username` | ✓ | 3~50자 |
| `password` | ✓ | 1~128자 |

응답 `200` — 교재 호환을 위해 `data`로 감싸지 않는다.

```json
{
  "accessToken": "<JWT>",
  "token_type": "Bearer",
  "expires_in": 3600,
  "user": {
    "id": "user-demo-01",
    "username": "demo-owner-a001",
    "role": "owner",
    "brand_id": "brand-demo-a",
    "store_id": "demo-store-01",
    "region_code": "SEOUL",
    "permissions": ["rag:ask"]
  },
  "request_id": "req-login-001"
}
```

- 계정 존재 여부를 구분하지 않고 실패는 `401 AUTH_FAILED`.
- `hq`·`prospect`는 `store_id`, `region_code`가 `null`일 수 있다.
- JWT는 Java가 발급하고 Java·FastAPI가 서명·만료·`iss`·`aud`를 검증한다. 서명키는 서버 환경변수로만 관리한다.

### 3.2 `GET /me`

응답 `200`: `{ "data": <User>, "request_id": "..." }`. `User`는 3.1의 `user`와 같다.

---

## 4. 질의(RAG)

### 4.1 `POST /rag-query`

헤더: `Authorization`, `Idempotency-Key`(재시도는 같은 키 → 같은 `question_id`, 중복 집계 방지).

요청

```json
{
  "question": "아이스 바닐라라테 제조 순서 알려주세요.",
  "conversation_id": null,
  "as_of": "2026-10-23T10:00:00+09:00"
}
```

| 필드 | 필수 | 검증·처리 |
| --- | --- | --- |
| `question` | ✓ | 공백 제거 후 1~2,000자. 음성 질문도 사용자가 확인·수정한 텍스트로 보낸다 |
| `conversation_id` | | 신규는 생략·`null`. 기존 ID는 소유자 확인, 타인 것이면 `403` |
| `as_of` | | 생략 시 서버 현재 시각. 해당 시점에 유효한 **배포·색인 완료** 판본만 검색 |

`role`, `brand_id`, `store_id`, `source`, `top_k` 등 범위 관련 값은 본문으로 받지 않는다(받으면 `422`).

처리 순서(Gateway)

1. 질문 사건(`question_id`, 점포·지역 스냅샷, 접수 시각, `processing`)을 먼저 기록
2. JWT·역할·점포로 허용 판본 집합(`allowed_versions`) 산출
3. 내부 `POST /rag-query` 호출(8장) → 결과·출처·지연으로 사건 갱신
4. 분류(`category`, `topic_id`)는 비동기 가능. 분류 실패가 답변을 실패시키지 않는다

응답 `200`

```json
{
  "data": {
    "question_id": "q-demo-0001",
    "conversation_id": "conv-demo-01",
    "answer": "등록된 합성 카페 표준 레시피 v1.0을 기준으로 안내합니다. 실제 브랜드 제조법이 아닌 시연 기준입니다.",
    "steps": [
      "바닐라 시럽 20 mL와 에스프레소 60 mL를 별도 용기에서 섞습니다.",
      "컵에 우유 170 mL와 얼음 180 g을 넣은 뒤 혼합액을 붓습니다."
    ],
    "status": "answered",
    "needs_review": false,
    "handoff": false,
    "sources": [
      {
        "chunk_id": "chk-recipe-10-p5-01",
        "document_id": "demo-cafe-recipes",
        "version_id": "demo-cafe-recipes-1.0",
        "title": "합성 카페 표준 레시피",
        "version": "1.0",
        "location": "PDF 5쪽 / 바닐라라테 ICED / 제조 순서",
        "page": 5,
        "article": null,
        "effective_from": "2026-10-19T00:00:00+09:00",
        "effective_until": "2026-10-26T00:00:00+09:00",
        "material_type": "synthetic",
        "is_demo": true,
        "excerpt": "바닐라 시럽 20 mL와 에스프레소 60 mL를 별도 용기에서 섞는다.",
        "detail_url": "/api/v1/documents/demo-cafe-recipes/versions/demo-cafe-recipes-1.0",
        "file_url": "/api/v1/documents/demo-cafe-recipes/versions/demo-cafe-recipes-1.0/file"
      }
    ],
    "follow_up_questions": [],
    "handoff_reason": null,
    "category": "recipe",
    "topic_id": "vanilla_syrup",
    "classification_status": "classified",
    "as_of": "2026-10-23T10:00:00+09:00",
    "basis": "current",
    "latency_ms": 2100
  },
  "request_id": "req-rag-001"
}
```

| 응답 필드 | 계약 |
| --- | --- |
| `answer`, `steps` | 핵심 설명과 확인·처리 순서. 숫자·단위는 문서 근거로만 제공 |
| `sources[]` | 실제 검색에 쓴 **허용 문서만**. `excerpt` 최대 1,500자. `detail_url`·`file_url`도 권한 재검증 |
| `status` / `needs_review` / `handoff` | 조합 고정: `answered → false/false`, `needs_review → true/false`, `handoff → true/true` |
| `follow_up_questions`, `handoff_reason` | `needs_review`는 둘 중 하나 이상 필수, `handoff`는 `handoff_reason` 필수, `answered`는 `handoff_reason=null` |
| `basis` | `past` / `current` / `future`. 현재가 아니면 답변에 해당 시점 기준임을 명시 |
| `latency_ms` | 처리 지연. 토큰·비용 내역은 응답에 넣지 않고 운영 로그에서 관리 |

규칙

- `answered`는 `sources` 1개 이상. 근거가 전혀 없으면 `sources=[]`이며 `answered`가 될 수 없다.
- 근거 부족·담당자 판단 필요도 정상 업무 응답이므로 **HTTP 200**. 모델·색인 장애는 근거 부족으로 숨기지 않고 `502/503/504`.
- 미래 적용일 판본은 현재 질문의 근거에서 제외. 사용자가 미래 시점을 `as_of`로 명시한 경우에만 `basis=future`로 사용.
- `handoff`는 "담당자 검토 안내" 상태다. 상담 접수·처리 완료를 뜻하지 않는다.

### 4.2 `GET /conversations/{conversation_id}`

본인 대화만. `data = { id, created_at, updated_at, messages[] }`, `messages[] = { question_id, question_redacted, created_at, answer_status(EventAnswerStatus), result(4.1 data | null), error_code }`. 타인 대화는 `403`.

### 4.3 `PUT /questions/{question_id}/feedback`

요청 `{ "helpful": true }` → 응답 `data = { question_id, helpful, submitted_at }`. 같은 질문에 다시 보내면 덮어쓴다. 아직 `processing`이거나 `system_error`인 질문은 `409 INVALID_STATE`. "도움 됐어요"는 `resolution_status`(종결 여부)와 별개다.

---

## 5. 문서 관리

### 5.1 `GET /documents`

| 쿼리 | 설명 |
| --- | --- |
| `category` | `Category` 필터 |
| `as_of` | 해당 시점 기준 대표 판본 선택(기본 현재) |
| `management` | `true`면 초안·승인 판본 포함(`hq` 관리 권한 필요, 없으면 `403`) |
| `page`, `page_size` | 공통 |

`data[] = { document_id, title, category, material_type, is_demo, visibility, selected_version_id, selected_version, effective_from, lifecycle, index_status }`

### 5.2 `POST /documents` — 등록

`multipart/form-data`, 헤더 `Idempotency-Key`.

| 파트 | 필수 | 내용 |
| --- | --- | --- |
| `file` | ✓ | PDF·TXT·Markdown, 파일당 최대 10MB. 서버가 실제 형식·추출 가능 여부 검사 |
| `metadata` | ✓ | 아래 `UploadMetadata` JSON 문자열 |

`UploadMetadata` (예: [`examples/upload-metadata.json`](examples/upload-metadata.json))

| 필드 | 필수 | 검증·의미 |
| --- | --- | --- |
| `title` | ✓ | 1~200자 |
| `version` | ✓ | 판본 이름 1~50자. 같은 문서에 이미 있으면 `409 VERSION_CONFLICT` |
| `document_id` | | 기존 문서에 새 판본 추가 시 지정. 생략하면 새 문서 |
| `category` | ✓ | `Category` |
| `material_type`, `is_demo` | ✓ | 레시피 `synthetic/true`, 정보공개서 `public_disclosure/false`, 법령 `official/false` |
| `visibility` | ✓ | 최소 허용 역할. 공개 가맹 자료 `prospect`, 레시피 `owner` |
| `effective_from` | ✓ | 내용 적용 시각. 업로드 날짜를 법적 적용일로 쓰지 않는다 |
| `original_source` | ✓ | `{ title, url\|null, edition, publisher, acquired_on(date), note }` |
| `change_summary` | ✓ | 1~2,000자 |
| `indexing_policy` | ✓ | `{ include_pages?, exclude_pages, legal_temporal_reviewed }` — 표지 제외 등 |

응답 `201` + `Location: /api/v1/documents/{doc_id}/versions/{ver_id}`

```json
{
  "data": {
    "document_id": "demo-cafe-recipes",
    "version_id": "demo-cafe-recipes-1.1",
    "version": "1.1",
    "lifecycle": "draft",
    "index_status": "not_started"
  },
  "request_id": "req-upload-001"
}
```

새 파일은 항상 새 판본으로 보존하며 배포된 판본을 덮어쓰지 않는다. 등록만으로는 점주에게 공개되지 않는다.

### 5.3 `GET /documents/{doc_id}/versions`

허용된 판본 이력. `data[] = DocumentVersion`(5.4), `meta` 포함.

### 5.4 `GET /documents/{doc_id}/versions/{ver_id}`

쿼리 `page`, `page_size`는 본문 조각(`sections`) 페이지다.

`data = { version: DocumentVersion, sections: Section[], sections_meta: PageMeta }`

- `DocumentVersion`: `document_id, version_id, brand_id, title, version, category, material_type, is_demo, visibility, effective_from, lifecycle, index_status, created_at, approved_at, published_at, change_summary, original_source, indexing_policy, file_name, media_type, file_size_bytes, sha256, page_count, file_url, index_revision`
- `Section`: `chunk_id, page, article, heading, text, effective_from, effective_until`
- `draft`·`approved` 판본은 본사 관리 권한만 열람. 내부 저장 경로·비밀키는 노출하지 않는다.

### 5.5 `GET /documents/{doc_id}/versions/{ver_id}/file`

권한 검증 후 원본 바이너리. `Content-Type`은 등록 시 `media_type`.

### 5.6 `POST /documents/{doc_id}/versions/{ver_id}/approve` — 승인

헤더 `Idempotency-Key`. 요청 `{ "review_note": "계량·적용일·출처 검수 완료" }`(1~2,000자).

응답 `202`: `data = { document_id, version_id, lifecycle: "approved", index_status: "queued"|"running", job_id }`

`draft`가 아니면 `409 INVALID_STATE`. 승인 후 내용·권한·적용일 변경은 새 판본으로 등록한다.

### 5.7 `GET /index-jobs/{job_id}`

`data = { job_id, document_id, version_id, status(JobStatus), chunk_count, index_revision, error_code, error_message, created_at, updated_at }`

성공 시 판본 `index_status=ready`, 실패 시 `failed`. 작업 실패는 기존 배포 판본의 답변에 영향을 주지 않는다.

### 5.8 `POST /documents/{doc_id}/versions/{ver_id}/publish` — 배포

헤더 `Idempotency-Key`. 요청 `{ "notice_title": "합성 레시피 v1.1 적용 안내" }`(1~200자).

응답 `200`

```json
{
  "data": {
    "document_id": "demo-cafe-recipes",
    "version_id": "demo-cafe-recipes-1.1",
    "lifecycle": "published",
    "index_status": "ready",
    "effective_from": "2026-10-26T00:00:00+09:00",
    "published_at": "2026-10-23T11:00:00+09:00",
    "notice_id": "notice-demo-002"
  },
  "request_id": "req-publish-001"
}
```

- `approved + ready`만 배포 가능. 색인 미완료 `409 INDEX_NOT_READY`, 그 외 상태 `409 INVALID_STATE`.
- 같은 문서·적용 범위에서 적용 시각이 겹치면 `409 EFFECTIVE_DATE_CONFLICT`.
- 공지 대상 점포는 문서 접근 범위에서 서버가 산출해 **배포 시점에 고정**한다. 판본 배포와 공지 생성은 하나의 트랜잭션.
- 미래 적용 판본의 배포는 현재 기준을 즉시 대체하지 않는다. 이전 판본·이력은 보존한다.
- 검색 대상 조건: `published` ∧ `ready` ∧ 허용 범위 ∧ `effective_from ≤ as_of < effective_until`.

---

## 6. 공지

### 6.1 `GET /notices`

쿼리 `unread`(true면 미확인만), `page`, `page_size`.
`data[] = { notice_id, document_id, version_id, title, change_summary, effective_from, published_at, acknowledged_at|null, detail_url }`

### 6.2 `PUT /notices/{notice_id}/ack`

본문 `{}`. 점포는 인증 정보에서 결정하며 다른 점포 대신 확인할 수 없다(`403`).
`data = { notice_id, store_id, acknowledged_by, acknowledged_at }`. 재요청 시 기존 기록을 그대로 반환(멱등). 확인 기록은 현장 이행 완료를 뜻하지 않는다.

### 6.3 `GET /notices/{notice_id}/acknowledgements`

쿼리 `acknowledged`(true/false), `region_code`, `page`, `page_size`.

```json
{
  "data": [
    { "store_id": "demo-store-07", "store_name": "시연 7호점", "region_code": "CHUNGCHEONG",
      "acknowledged_at": null, "acknowledged_by": null }
  ],
  "summary": {
    "notice_id": "notice-demo-002",
    "as_of": "2026-10-23T12:00:00+09:00",
    "target_count": 12,
    "acknowledged_count": 9,
    "unacknowledged_count": 3,
    "acknowledgement_rate": 0.75
  },
  "meta": { "page": 1, "page_size": 20, "total": 12, "total_pages": 1 },
  "request_id": "req-ack-001"
}
```

`summary`는 필터(`acknowledged`, `region_code`)와 무관하게 공지 전체 대상 기준이고, `data`·`meta`만 필터·페이지를 따른다.

확인율 = 확인 점포 수 / 배포 시 대상 점포 수(사용자 클릭 수 아님). 분모 0이면 `null`.

---

## 7. 본사 문의 분석

조회 기간·필터 공통 쿼리

| 쿼리 | 설명 |
| --- | --- |
| `week_start` | KST 월요일 날짜(`YYYY-MM-DD`). 월요일이 아니면 `422`. `from`/`to`와 동시 사용 불가. 이번 주(진행 중)도 이것으로 요청 |
| `from`, `to` | RFC 3339 시각, 둘 다 필수(쌍). `from` 포함·`to` 미포함, 최대 31일, `to`는 미래 금지 |
| `region_code`, `store_id`, `category`, `topic_id` | 필터. 소속 브랜드 범위 밖이면 `403` |
| `user_role` | `owner`(기본) 또는 `prospect`. 운영 문의와 가맹 상담을 섞지 않음 |

기본값: **지난 완료 주**(월 00:00 이상 ~ 다음 월 00:00 미만, KST), 전체 지역, `owner`.

### 7.1 `GET /regions`, `GET /stores`, `GET /topics`

필터 선택지. `Region = {code, name}`, `Store = {id, name, region_code, active}`(쿼리 `region_code`), `Topic = {id, label, category, active}`(쿼리 `category`). 모두 페이지 목록.

### 7.2 `GET /analytics/questions`

```json
{
  "data": {
    "period": {
      "start": "2026-10-12T00:00:00+09:00",
      "end_exclusive": "2026-10-19T00:00:00+09:00",
      "timezone": "Asia/Seoul",
      "is_partial": false,
      "comparison_start": "2026-10-05T00:00:00+09:00",
      "comparison_end_exclusive": "2026-10-12T00:00:00+09:00"
    },
    "filters": { "region_code": null, "store_id": null, "category": null, "user_role": "owner", "topic_id": null },
    "generated_at": "2026-10-23T12:00:00+09:00",
    "metrics": {
      "total": 132, "processing_count": 0, "answered_count": 103,
      "review_required_count": 24, "needs_review_count": 18, "handoff_count": 6,
      "system_error_count": 5, "completed_count": 127, "active_store_count": 12,
      "classification_pending_count": 0,
      "guidance_rate": 0.811, "review_required_rate": 0.189, "questions_per_active_store": 11.0
    },
    "previous_period": { "total": 88, "...": "같은 MetricCounts 구조" },
    "total_change": 44,
    "total_change_rate": 0.5,
    "change_kind": "increase",
    "top_topics": [ { "topic_id": "vanilla_syrup", "label": "바닐라라테 시럽 계량", "count": 42, "review_required_count": 3 } ],
    "by_category": [ { "category": "recipe", "count": 90 } ],
    "by_region": [ { "region_code": "SEOUL", "region_name": "서울", "metrics": { "...": "MetricCounts" }, "small_sample": false } ]
  },
  "request_id": "req-analytics-001"
}
```

(수치는 합성 시연 데이터 예시. `needs_review_count`/`handoff_count` 분할은 형식 설명용)

지표 계산 규칙 — 숫자는 **DB에서 계산**하고 LLM에게 세게 하지 않는다.

| 지표 | 정의 |
| --- | --- |
| `total` | 서로 다른 `question_id` 수. 시스템 오류 포함 |
| `review_required_count` | `needs_review + handoff`. 실제 종결 여부와 별개 |
| `completed_count` | `answered + needs_review + handoff` |
| `guidance_rate` | `answered / completed_count`. 시스템 오류는 분모 제외. 분모 0이면 `null` |
| `review_required_rate` | `review_required_count / completed_count` |
| `questions_per_active_store` | `total / active_store_count` |
| `total_change_rate` | `(이번 - 직전) / 직전`. 직전 0건이면 `null`, `change_kind="new"` |
| 비교 기간 | 완료 주는 직전 완료 주. 진행 중 주(`is_partial=true`)는 직전 주의 같은 요일·시각까지 |

결과가 없으면 0건·비율 `null`·빈 배열. **집계 실패를 0건으로 바꾸지 않는다**(`500`). 지역은 질문 접수 당시 스냅샷 기준이며 매핑 없는 지역은 `UNASSIGNED`(미분류).

### 7.3 `GET /analytics/question-events`

7장 공통 쿼리 + `answer_status`, `resolution_status`, `classification_status`, `page`, `page_size`.

`data[] = QuestionEvent`

| 필드 | 설명 |
| --- | --- |
| `question_id`, `conversation_id` | 식별자 |
| `store_id`, `store_name`, `region_code`, `region_name` | 접수 당시 스냅샷 |
| `user_role`, `created_at`, `updated_at` | |
| `question_redacted` | 마스킹된 질문 |
| `answer_status` | `EventAnswerStatus`(원본, 수정 불가) |
| `resolution_status` | 담당자 처리 상태 |
| `category`, `topic_id`, `classification_status`, `classification_version` | 분류 |
| `result` | 4.1 응답 `data` 또는 `null` |
| `error_code`, `attempt_count`, `revision` | `revision`은 낙관적 잠금용 |

### 7.4 `GET /analytics/question-events/{question_id}`

한 건 상세. `data = QuestionEvent`.

### 7.5 `PATCH /analytics/question-events/{question_id}`

```json
{ "expected_revision": 3, "category": "recipe", "topic_id": "vanilla_syrup",
  "resolution_status": "in_progress", "review_note": "교육자료 보완 대상" }
```

- `expected_revision` 필수. 현재 값과 다르면 `409 INVALID_STATE`(다른 담당자가 먼저 수정).
- 분류를 바꾸면 `classification_status=corrected`. `answer_status` 원본은 바꾸지 않는다. 수정자·수정 시각을 기록.
- 응답 `data = QuestionEvent`(갱신된 `revision`).

---

## 8. 내부 RAG 서비스 (FastAPI :8001)

브라우저에서 직접 호출하지 않는다. 인증: `X-Internal-Token`(서비스 간 비밀) **+** 사용자 `Authorization: Bearer <JWT>`(재검증). 상세 스키마는 [`internal-rag.openapi.yaml`](internal-rag.openapi.yaml).

| 메서드·경로 | 호출자 | 목적 | 성공 |
| --- | --- | --- | --- |
| `POST /rag-query` | Java Gateway | 허용 판본에서만 검색·답변 | 200 |
| `POST /internal/v1/index-jobs` | Java Gateway | 승인 판본 색인 준비 | 202 |
| `GET /internal/v1/index-jobs/{job_id}` | Java Gateway | 색인 작업 조회 | 200 |
| `GET /health` | 운영 | RAG 준비 상태 | 200 / 503 |

`POST /rag-query` 요청(`InternalQuery`)

| 필드 | 설명 |
| --- | --- |
| `question_id`, `attempt_id` | Gateway 발급. 같은 쌍의 중복 호출은 기존 실행·결과를 반환 |
| `conversation_id`, `question` | 공개 요청 값(질문 1~2,000자) |
| `principal` | `{ user_id, role, brand_id, store_id\|null, permissions[] }` — JWT·DB와 일치해야 함, 불일치 `403` |
| `as_of`, `basis` | Gateway가 확정한 기준 시각과 `past/current/future` |
| `scope_revision` | 허용 범위 계산 버전. 응답에 그대로 돌려줌 |
| `allowed_versions[]` | `{ document_id, version_id, index_revision }`. **이 목록 밖의 조각은 검색·인용 금지** |
| `history[]` | 직전 대화 최대 6턴 `{ question(≤2,000), answer(≤6,000) }` |
| `deadline_ms` | 1,000~30,000. 초과 시 `504` |

응답 `200` (`InternalAnswer`)

```json
{
  "data": {
    "result": { "...": "4.1 응답 data와 동일(RagAnswer)" },
    "scope_revision": "scope-20261023-01",
    "classification": { "category": "recipe", "topic_id": "vanilla_syrup", "status": "classified", "version": "rules-v1" },
    "usage": { "input_tokens": 1800, "output_tokens": 220, "embedding_tokens": 24,
               "model": "<LLM 모델명>", "embedding_model": "<임베딩 모델명>", "estimated_cost_usd": 0.0031 },
    "retrieval": { "candidate_count": 8, "used_chunk_ids": ["chk-recipe-10-p5-01"], "llm_called": true }
  },
  "request_id": "req-rag-internal-001"
}
```

Gateway는 `result`만 공개 응답의 `data`로 내보내고, `usage`·`retrieval`·`classification`은 질문 사건·운영 로그에 저장한다.

`POST /internal/v1/index-jobs` 요청(`IndexJobRequest`): `job_id, brand_id, document_id, version_id, title, version, category, material_type, is_demo, visibility, effective_from, original_source, indexing_policy, source{storage_key(^documents/…), sha256, media_type(pdf|plain|markdown)}`. 응답 `202`, `data = IndexJob`(5.7과 동일). `X-Internal-Token`만 필요(사용자 JWT 불필요).

- `job_id`가 멱등키다. 같은 `job_id`·같은 내용은 기존 작업 반환, 다른 내용은 `409`.
- 승인 여부 확인은 Gateway 책임이다. **인덱스 생성은 배포가 아니다** — `ready`가 되어도 publish 전에는 검색 대상이 아니다.
- 새 인덱스 준비·검증이 끝나기 전에는 기존 서비스 인덱스를 유지한다(교체형 재생성 금지).
- 질문·문서에 동일한 임베딩 모델·설정을 쓴다. 모델을 바꾸면 전체 재색인.

---

## 9. 선택 기능·상태 확인

### 9.1 `POST /stt` (선택)

`multipart/form-data`, 파트 `audio`: WebM·WAV, 10MB 이하, 60초 이하.
응답 `data = { text, duration_ms }`. 인식 텍스트는 화면에서 수정한 뒤 별도로 `/rag-query`에 보낸다(자동 질의 금지). 음성 원본은 저장하지 않는다.

내부 연결(`voice/`, compose `--profile voice`): Java가 `POST http://voice:8008/internal/v1/stt`(`X-Internal-Token`, multipart `audio`)로 넘기고 `{"data":{text,duration_ms}}`를 받는다. 음성 서비스의 413·415·422(60초 초과)·503(모델 준비 중)은 같은 공개 오류로, 연결 실패는 503, 시간 초과(60초)는 504로 바꾼다. 음성 서비스를 띄우지 않으면 `/stt`는 503이고 화면은 텍스트 질문으로 안내한다.

### 9.2 `GET /health`

인증 없음. `{"status":"ready"}`(200) 또는 `{"status":"not_ready"}`(503)만 반환. 주소·키·문서 수 등 상세는 노출하지 않는다.

---

## 10. 오류

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "질문을 입력해 주세요.",
    "details": [ { "field": "question", "code": "too_short", "message": "1자 이상 입력" } ],
    "question_id": null,
    "retryable": false
  },
  "request_id": "req-error-001"
}
```

- `message`는 사용자에게 그대로 보여줄 수 있는 한국어 문장. 스택트레이스·내부 경로 금지.
- `question_id`: `/rag-query`에서 질문 사건이 이미 기록된 뒤 실패한 경우 채운다(5xx). 그 외 `null`.
- `retryable`: 같은 `Idempotency-Key`로 재시도해도 되는지. 429·502·503·504는 보통 `true`.
- 모든 응답에 `X-Request-ID` 헤더(= `request_id`)를 넣는다.

| HTTP | 코드 | 의미 · 화면 동작 |
| --- | --- | --- |
| 400 | `MALFORMED_REQUEST` | JSON·요청 형식 오류 |
| 401 | `AUTH_FAILED` | 로그인 실패(계정 존재 여부 비공개) |
| 401 | `AUTH_REQUIRED`, `TOKEN_EXPIRED` | 로그인 화면으로, 입력 내용 보존 |
| 403 | `FORBIDDEN` | 역할·브랜드·점포·문서 접근 제한. 로그인 복귀와 구분해 안내 |
| 404 | `RESOURCE_NOT_FOUND` | 문서·판본·공지·작업·질문 없음 |
| 409 | `INVALID_STATE`, `VERSION_CONFLICT`, `INDEX_NOT_READY`, `EFFECTIVE_DATE_CONFLICT`, `IDEMPOTENCY_CONFLICT` | 상태·판본·적용일·중복 요청 충돌 |
| 413 | `FILE_TOO_LARGE` | 파일 크기 초과 |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | 허용되지 않는 파일 형식 |
| 422 | `VALIDATION_ERROR` | 필수·길이·날짜·허용되지 않은 필드. `details`에 필드명 |
| 429 | `RATE_LIMITED` | 사용량 제한, `Retry-After` |
| 500 | `INTERNAL_ERROR` | 추적 ID만 안내, 상세는 서버 로그 |
| 502 | `UPSTREAM_ERROR` | 외부 LLM·임베딩 오류 |
| 503 | `SERVICE_UNAVAILABLE` | 내부 서비스·색인 준비 안 됨 |
| 504 | `UPSTREAM_TIMEOUT` | 외부 모델 지연 |

---

## 11. 역할별 구현 가이드

| 담당 | 먼저 읽을 곳 | 이 가이드 |
| --- | --- | --- |
| 백엔드(Java 인증·업무 API) | 1, 2, 3~7, 10장 | 11.1 |
| 프론트엔드(Vue) | 1, 2, 3, 4.1, 6, 7.2, 10장 | 11.2 |
| RAG(FastAPI) | 4.1, 8장 | 11.3 |

### 11.1 백엔드 — Java 인증·업무 API (:8080)

**담당 엔드포인트:** 2장의 1~25 전부(STT·RAG는 내부 서비스로 프록시). 내부 RAG는 클라이언트로 호출만 한다.

**구현 순서(Day 1 → 2)**

1. `POST /login`, `GET /me`, JWT 필터(`iss`·`aud`·`exp` 검증, `401`/`403` 분리), 공통 응답·오류 래퍼, `X-Request-ID`
2. `POST /rag-query` → 내부 RAG 호출 → 질문 사건 기록
3. 문서 등록 → 승인(내부 index-job 생성) → 작업 조회 → 배포+공지
4. 공지 목록·확인·현황
5. `/analytics/*`, `/regions`, `/stores`, `/topics`

**필요 테이블(최소)**

| 테이블 | 핵심 컬럼 / 제약 |
| --- | --- |
| `users` | id, username(unique), password_hash, role, brand_id, store_id, permissions |
| `stores` | store_id, brand_id, name, region_code, region_name, active_from, active_until |
| `documents` | document_id, brand_id, title, category, material_type, is_demo, visibility |
| `document_versions` | version_id, document_id, version(**unique with document_id**), effective_from, effective_until, lifecycle, index_status, index_revision, sha256, storage_key, original_source(JSON), indexing_policy(JSON), change_summary, approved_at, published_at |
| `index_jobs` | job_id, version_id, status, chunk_count, error_code, created_at, updated_at |
| `question_events` | question_id, request_id(unique), conversation_id, brand_id, user_id, user_role, store_id, region_snapshot, created_at, question_redacted, answer_status, resolution_status, category, topic_id, classification_status, classification_version, result(JSON), error_code, latency_ms, attempt_count, revision |
| `question_sources` | question_id, document_id, version_id, chunk_id, page_or_article |
| `question_feedback` | question_id(PK), helpful, submitted_at |
| `notices` / `notice_targets` | notice_id, version_id / notice_id + target_store_id(배포 시 고정) |
| `notice_acknowledgements` | **unique(notice_id, store_id)**, acknowledged_by, acknowledged_at |
| `idempotency_keys` | **unique(user_id, operation, key)**, request_hash, response(JSON), created_at(24h 만료) |

**`/rag-query` 처리 의사코드**

```text
idem = lookup(user, "rag-query", Idempotency-Key)
if idem and idem.hash != hash(body): 409 IDEMPOTENCY_CONFLICT
if idem and idem.done: return idem.response
q = idem?.question_id ?: insert question_events(status=processing, store/region snapshot)
allowed = select versions where published and index_status=ready
          and brand=user.brand and visibility allows user.role
          and effective_from <= as_of < effective_until(null=∞)
call RAG POST /rag-query (X-Internal-Token + 원 JWT, deadline_ms=20000)
  ok   → update question_events(result, answer_status, category, topic, latency), insert question_sources
  fail → update answer_status=system_error, error_code; return 502/503/504 with error.question_id=q
return { data: result, request_id }
```

**상태 전이 검증**

| 동작 | 허용 전(前) 상태 | 실패 코드 |
| --- | --- | --- |
| approve | `draft` | `409 INVALID_STATE` |
| publish | `approved` ∧ `index_status=ready` | `409 INDEX_NOT_READY` / `409 INVALID_STATE` |
| publish | 같은 문서·범위의 적용 시각 겹침 없음 | `409 EFFECTIVE_DATE_CONFLICT` |

publish 트랜잭션: 판본 `published` + 직전 판본 `effective_until` 설정 + `notices` + `notice_targets` 스냅샷을 **한 트랜잭션**으로.

**주의:** 요청 본문의 `role/brand_id/store_id`는 무시하지 말고 `422`로 거절. 응답·로그에 JWT·API 키·내부 경로 금지. 집계는 SQL로(UTC 저장, KST 경계 변환).

### 11.2 프론트엔드 — Vue

**화면 ↔ API 매핑**

| 화면 | 역할 | 호출 API |
| --- | --- | --- |
| 로그인 | 전체 | `POST /login` → 토큰 저장 → `GET /me` |
| 질문·답변 | prospect, owner | `POST /rag-query`, `GET /conversations/{id}`, `PUT /questions/{id}/feedback`, (선택) `POST /stt` |
| 출처 패널 | 전체 | `sources[].detail_url` → `GET /documents/.../versions/{ver}`, `file_url` 다운로드 |
| 공지함 | owner | `GET /notices?unread=true`, `PUT /notices/{id}/ack` |
| 문서 관리 | hq | `GET /documents?management=true`, `POST /documents`, `.../approve`, `GET /index-jobs/{id}`(폴링), `.../publish` |
| 주간 대시보드 | hq | `GET /regions`, `/stores`, `/topics`(필터), `GET /analytics/questions`, `GET /analytics/question-events`, `PATCH /analytics/question-events/{id}` |
| 공지 확인 현황 | hq | `GET /notices/{id}/acknowledgements` |

**공통 HTTP 클라이언트(axios 인터셉터 등)**

- 요청: `Authorization: Bearer <accessToken>`. 토큰은 메모리 + `sessionStorage`(새로고침 대비). 갱신 API 없음 → 만료 시 재로그인.
- `/rag-query`·`POST /documents`·`approve`·`publish`: 사용자 동작마다 `crypto.randomUUID()`로 `Idempotency-Key` 생성. **재시도 시 같은 키 재사용**.
- `401` → 입력 중인 질문을 보존하고 로그인 화면으로. `403` → "접근 권한 없음" 안내(로그인 이동 아님).
- `422` → `error.details[].field`를 폼 필드 오류로 표시. `429` → `Retry-After` 초 후 재시도 안내.
- `retryable=true`인 5xx → "다시 시도" 버튼(같은 키). 그 외 `error.message` 표시 + `request_id`를 작게 노출.

**답변 렌더링 규칙**

| `status` | 표시 |
| --- | --- |
| `answered` | 핵심 답변 → `steps` 번호 목록 → 출처 카드 |
| `needs_review` | "추가 확인 필요" 배지 + 확인된 부분 + `follow_up_questions` 목록 |
| `handoff` | "담당자 확인 필요" 배지 + `handoff_reason` + 복사 가능한 문의 요약. **"접수됨/해결됨" 문구 금지** |

- 출처 카드: 제목·판본·`location`·`excerpt`. `material_type=synthetic` 또는 `is_demo=true`면 **"합성·시연 자료"** 배지 필수. `public_disclosure`는 "정보공개서 원본(수집본)", `official`은 "법령·고시".
- `basis`가 `past`/`future`면 "○○ 기준 답변" 배너.
- 색인 작업은 2초 간격 폴링, `succeeded`/`failed`에서 중단.
- 대시보드 비율이 `null`이면 `N/A`. `is_partial=true`면 "진행 중" 표시. `small_sample=true` 지역은 "표본 적음" 표시.

**환경 변수:** `VITE_API_BASE_URL`(개발 `http://localhost:8080/api/v1`, 배포 `/api/v1`). 모델·JWT 키는 프론트에 절대 두지 않는다.

### 11.3 RAG — FastAPI (:8001)

**담당 엔드포인트:** 8장의 4개. 공개 경로를 직접 노출하지 않는다.

**요청 검증 순서 (`POST /rag-query`)**

1. `X-Internal-Token` 일치(상수 시간 비교) → 아니면 `401`
2. 사용자 JWT 서명·만료·`iss`·`aud` 재검증 → `401`
3. `principal` ↔ JWT `sub`/역할/브랜드 일치 → `403`
4. Pydantic 스키마 검증(`extra="forbid"`) → `422`
5. `(question_id, attempt_id)` 중복이면 저장된 결과 반환

**검색 규칙**

- 검색 **전에** 메타데이터 필터: `(document_id, version_id, index_revision) ∈ allowed_versions` ∧ 조각의 `effective_from ≤ as_of < effective_until`(조문 단위 시행일). FAISS 결과를 넉넉히 뽑은 뒤 필터하는 경우 필터 후 개수가 부족하지 않게 `k`를 키운다.
- 조각 메타데이터(`IndexedChunkMetadata`): `chunk_id, brand_id, document_id, version_id, index_revision, title, version, visibility, material_type, is_demo, page, article, section_key, effective_from, effective_until, …`. 원문 페이지·조문 번호를 반드시 보존.
- 질문·문서 임베딩 모델 동일. 모델명은 `usage.embedding_model`에 기록.

**응답 생성 규칙**

| 상황 | `status` | 요구 사항 |
| --- | --- | --- |
| 근거 충분 | `answered` | `sources` ≥ 1, 수치·단위는 근거 원문 그대로 |
| 근거 없음·부족·상충 | `needs_review` | `follow_up_questions` 또는 `handoff_reason` ≥ 1. 근거 없으면 `sources=[]`, LLM 미호출 가능(`llm_called=false`) |
| 환불·보상·식품안전·법적 판단 요구 | `handoff` | `handoff_reason` 필수. 위법·환불 결론을 만들지 않음 |

- `needs_review`/`handoff` 불리언은 `status`에서 기계적으로 산출(1.2 조합 고정).
- LLM이 인용한 조각만 `sources`에 넣는다(`retrieval.used_chunk_ids`와 일치). 합성 자료가 근거면 답변 첫 문장에 "합성 시연 레시피 기준"을 명시.
- `category`/`topic_id` 분류: 문서 메타·메뉴 사전 규칙 우선, 모호하면 LLM 후보, 불확실하면 `pending`. 분류 실패가 답변을 실패시키지 않는다.
- LLM·임베딩 장애는 `502`/`504`로 반환. **근거 부족 응답으로 위장 금지.**

**색인 작업 (`POST /internal/v1/index-jobs`)**

1. `source.storage_key`에서 파일 로드 → `sha256` 대조(불일치 `failed`, `error_code=CHECKSUM_MISMATCH`)
2. `indexing_policy` 적용(표지 등 `exclude_pages` 제외)
3. 텍스트·표 추출 → 분할(법령: 조·항·호 / 정보공개서: 표 행+단위+주석 / 레시피: 메뉴 단위)
4. 임베딩 → **새 `index_revision`으로 별도 인덱스 생성** → 검증 → 작업 `succeeded` + `index_revision`, `chunk_count` 기록
5. 기존 인덱스를 덮어쓰지 않는다(템플릿의 FAISS 교체 로직 수정 필요). 실패해도 기존 배포 판본 검색은 그대로.

**환경 변수:** `INTERNAL_TOKEN`, `JWT_SECRET`(또는 공개키), `JWT_ISSUER`, `JWT_AUDIENCE`, `LLM_API_KEY`, `LLM_MODEL`, `EMBEDDING_MODEL`, `FAISS_DIR`, `DOCUMENT_STORE_DIR`.

### 11.4 통합 체크리스트(계약 테스트)

[`examples/contract-cases.json`](examples/contract-cases.json)의 요청·응답 쌍을 각 서버의 계약 테스트로 사용한다. 최소 확인 항목:

- [ ] owner로 Q01(바닐라라테 시럽) → `answered`, 출처에 `is_demo=true`, 5쪽
- [ ] prospect로 같은 질문 → 레시피 출처 노출 없음(`needs_review` 또는 권한 밖 자료 미인용)
- [ ] 미등록 메뉴 질문 → `needs_review`, `sources=[]`
- [ ] v1.1 approve → job `succeeded` → publish 전 질문은 여전히 v1.0 답변
- [ ] publish 후 `as_of=2026-10-26T00:00:00+09:00` 질문 → 시럽 15 mL, `basis=future`
- [ ] 같은 `Idempotency-Key` 재전송 → 같은 `question_id`, 집계 1건
- [ ] 다른 점포 공지 ack → `403`, 같은 점포 재ack → 기존 기록 반환
- [ ] `/analytics/questions?week_start=2026-10-12` → total 132, 직전 88

## 12. 데모 계정·데이터 가정

- 계정은 시연용으로 미리 준비한다(`demo-owner-*`, `demo-prospect-*`, `demo-hq-*`). 실제 비밀번호는 저장소에 올리지 않고 `.env`/시드 스크립트로 주입한다.
- 점포·권역·문의 사건은 모두 합성 데이터다(가상 12점포·4권역).
- 레시피는 팀이 만든 합성 자료(`synthetic`, `is_demo=true`)이며 실제 브랜드 제조법으로 안내하지 않는다. 정보공개서는 원본(`public_disclosure`)을 이름·수치 변경 없이 사용한다.

## 13. 변경 이력

| 날짜 | 버전 | 내용 |
| --- | --- | --- |
| 2026-10-23 | v1.0 | 서비스기획서 7장·본사 대시보드 설계 8장 기반 최초 작성. OpenAPI 초안과 정합화 |
