# 상담 RAG API — 데이터 연결 전 구현

현재 구현은 내부 `POST /rag-query`와 `GET /health`, JWT·서비스 인증, LangGraph 상담 흐름, LangChain 모델 어댑터, 출처·상태 검증입니다. 실제 데이터 검색 어댑터와 색인 API는 아직 연결하지 않았습니다. 실제 RAG 답변 품질과 Java·웹 E2E를 통과한 상태가 아닙니다.

2026-10-23 사용자 확인에 따라 **MVP의 레시피도 기존 `/rag-query`로 처리**합니다. A의 구조화 JSON을 이용해 계량·단계를 저장된 값 그대로 반환하는 내부 빠른 경로를 추가할 계획이며, 레시피 RDB·별도 API·단계 전용 화면은 E2E 이후 확장으로 둡니다. 현재 실제 구조화 자료·인덱스 연결과 빠른 경로는 미구현입니다. 일반 상담은 짧은 핵심 답변, 확인·처리 순서, 적용 조건·예외, 문서 출처를 제공합니다. 조건·예외는 현행 계약의 `answer`, 추가 질문은 `follow_up_questions`에 담습니다.

## 설치와 자동 검사

Python 3.11을 사용합니다. 가상환경·인증값·실행 상태 파일은 Git에서 제외됩니다.

```bash
cd /Users/inwoopark/workspace/ai-service/ai-service-project/frangizer/rag
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

자동 검사는 외부 모델·임베딩 API를 호출하지 않습니다. LangGraph 실행에는 모델 대역을 사용하며, LangChain의 도구 호출·구조화 응답 직렬화도 HTTP 모의 응답으로 검사합니다. 이는 실제 모델이 질문을 이해하고 적절한 도구를 선택하는지에 대한 평가가 아닙니다.

## 터미널에서 테스트 서버 실행

기존 교수님 RAG의 8001 포트와 겹치지 않도록 이 테스트는 **127.0.0.1:8011**에서 실행합니다.

첫 터미널:

```bash
cd /Users/inwoopark/workspace/ai-service/ai-service-project/frangizer/rag
source .venv/bin/activate
python -m tests.demo prepare
set -a
source .demo/credentials.env
set +a
python -m uvicorn tests.demo:create_demo_app --factory --host 127.0.0.1 --port 8011 --workers 1
```

두 번째 터미널:

```bash
cd /Users/inwoopark/workspace/ai-service/ai-service-project/frangizer/rag
source .demo/credentials.env
curl -sS http://127.0.0.1:8011/rag-query \
  -H 'Content-Type: application/json' \
  -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -H "Authorization: Bearer $DEMO_USER_TOKEN" \
  --data-binary @.demo/request.json | .venv/bin/python -m json.tool
```

예상 결과는 HTTP 200, `data.result.status=answered`, 테스트용 항목 A와 합성 자료 출처입니다. 모델은 고정된 대역이고 질문을 바꾸어도 실제 상담을 하지 않습니다. 응답 헤더 `X-Frangizer-Test-Mode: synthetic-fixture`로 테스트 실행임을 표시합니다.

인증 누락 검사:

```bash
curl -i http://127.0.0.1:8011/rag-query \
  -H 'Content-Type: application/json' --data-binary @.demo/request.json
```

예상 결과는 HTTP 401입니다.

준비 상태 검사:

```bash
curl -i http://127.0.0.1:8011/health -H "X-Internal-Token: $INTERNAL_TOKEN"
```

테스트 모드는 실제 검색이 연결되지 않았으므로 HTTP 503과 `{"status":"not_ready"}`를 반환합니다. API 흐름 테스트 성공을 실제 RAG ready로 표시하지 않습니다. 토큰은 생성 시점부터 한 시간 동안 유효하며, 만료 시 `prepare`를 다시 실행하고 두 터미널에서 환경변수를 다시 읽습니다. 테스트 서버는 Ctrl+C로 종료합니다.

## 데이터 담당자 답변 후 연결할 부분

- `app/retrieval/base.py`의 `Retriever`에 실제 구현을 주입합니다. `search(query, context)`와 `read_sections(chunk_ids, context)`를 제공합니다.
- 검색 전 `(document_id, version_id, index_revision)`, 브랜드·역할·적용일로 제한합니다. Java가 점포 범위까지 검증해 전달한 `allowed_versions`를 넓히지 않습니다. 반환된 자료에도 동일한 검사를 적용합니다.
- 본문은 같은 UTF-8 본문의 `content_sha256`과 일치해야 합니다. 기본 한 배치는 최대 20조각·12,000자, 한 조각은 12,000자입니다. 더 긴 자료의 분할·잘림 표시는 A/B가 합의합니다. 잘린 근거로 완전한 답변을 승인하지 않습니다.
- 원문 섹션 조회는 이번 요청에서 확보한 조각과 같은 판본·인덱스·섹션에 한정합니다. 실제 답변에 사용한 조각만 출처에 넣습니다.
- 임베딩 모델·차원·거리 방식·로딩 포맷은 담당자 답변 이후 확정합니다. 아직 특정 FAISS 파일 구조를 요구하지 않습니다.
- 실제 모델 연결은 `LangChainModel.from_env()`를 사용해 `CounselAgent(model, retriever)`에 주입합니다. `LLM_MODEL`과 `LLM_API_KEY`를 명시하며 자동으로 기존 프로젝트의 비밀값을 읽지 않습니다. 이번 작업에서는 실제 모델 호출을 하지 않았습니다.
- `create_app(auth=..., agent=..., state_db=...)`에 완성된 에이전트를 주입합니다. 기본 `app.main:create_app` 팩토리는 인증 설정만 있으면 시작되지만 검색 연결 전 질의·health는 503을 반환합니다.
- 색인 작업과 원문 추출은 데이터 담당 영역입니다. `/internal/v1/index-jobs`는 이번 단계에 구현하지 않았습니다.

## 인증·재시도·제한

JWT는 고정 설정된 HS256으로 서명·`exp`·`iss`·`aud`·`sub`를 검사합니다. JWT의 `role`, `brand_id`, `store_id`(null 포함), `permissions`가 본문 `principal`과 같아야 하고 `rag:ask`가 필요합니다. 기존 교수님 인증 서버의 단순 JWT를 그대로 호환한다고 가정하지 않습니다. Java 담당자와 새 명세의 클레임을 맞춰야 합니다. 서명키는 최소 32바이트로 설정합니다.

C가 전달한 연결 설정은 `JWT_ALGORITHM=HS256`, `JWT_ISSUER=frangizer`, `JWT_AUDIENCE=frangizer-api`입니다. `JWT_SECRET`·`INTERNAL_TOKEN`은 별도 비밀값으로 주입합니다. 실제 JWT 서명키의 UTF-8/Base64 처리 방식과 hq/prospect의 null `store_id` 표현은 C 코드·역할별 테스트 토큰으로 대조해야 합니다. C의 구현 완료 보고를 이번 RAG와의 통합 검증 완료로 보지는 않습니다.

공유 볼륨 제안은 backend·rag 모두 `/var/lib/frangizer`에 마운트하고 `DOCUMENT_STORE_DIR=/var/lib/frangizer`, `FAISS_DIR=/var/lib/frangizer/faiss`를 사용합니다. ASCII 저장 키 `documents/{document_id}/{version_id}.pdf`를 `DOCUMENT_STORE_DIR` 아래에 결합합니다. A·C·E의 실제 코드·Compose에서 경로와 매체별 확장자를 확인한 후 사용합니다.

기본 예산은 모델 호출 최대 4회, 도구 최대 3회, 검색 최대 2회입니다. 마지막 모델 호출을 최종 초안에 남겨둡니다. 요청 `deadline_ms`(1~30초)를 강제하며 장애는 502/503/504로 반환합니다. 본문 초과·권한 위반 등 오류가 원문 입력이나 비밀값을 되돌려주지 않도록 합니다.

같은 사용자·`question_id`·`attempt_id`의 성공 결과와 처리 오류를 SQLite에 24시간 보존합니다. 같은 쌍·다른 요청은 409입니다. 동일 요청의 동시 실행은 한 모델 실행으로 합칩니다. 실패 후 실제 재실행은 새 `attempt_id`를 사용합니다. 저장에는 원문 질문 대신 요청 해시와 결과·안전한 오류를 사용합니다.

**현재 실행 범위는 단일 worker·단일 프로세스입니다.** 여러 worker·복제본에서의 실행 중 중복 방지·중단된 요청 복구는 후속 공유 작업 저장소가 필요합니다. SQLite는 요청 결과 재사용 저장소이며 별도 대화 기억 저장소가 아닙니다. 대화는 Java가 최근 6턴을 전달합니다.

모델 입력과 응답의 인식 가능한 전화번호·이메일·명시적 고객명/주문번호는 마스킹합니다. 이는 모든 비정형 개인정보를 식별한다는 보장이 아니므로 Java의 마스킹과 실제 입력 평가도 필요합니다. 자동 외부 추적은 활성화하지 않았으며 테스트 시에도 `LANGSMITH_TRACING`을 켜지 않습니다.

## 코드 위치

| 위치 | 역할 |
|---|---|
| `app/main.py`, `app/api/` | 서버 팩토리·질의·준비 상태·오류 응답 |
| `app/auth.py`, `app/schemas.py` | 인증과 현행 API 모델 |
| `app/generation/agent.py` | LangGraph의 판단·도구 실행·최종 초안 흐름 |
| `app/generation/model.py` | LangChain 도구·구조화 초안 모델 연결 |
| `app/generation/answer.py` | 실제 출처 연결·응답 상태 검사 |
| `app/retrieval/base.py` | 검색 경계·자료 권한·본문 해시 검증 |
| `app/replay.py`, `app/privacy.py` | 재시도·중복 실행 방지·식별정보 마스킹 |
| `prompts/counsel.md` | 상담 범위와 근거 사용 규칙 |
| `tests/` | 모의 자료·모델·오프라인 계약/흐름 검사·테스트 서버 |

API 기준은 `../docs/api/`입니다. 새 공개 API나 필드를 추가하지 않았습니다. 출처 검사 통과가 모든 문장의 의미적 정확성을 보장하지는 않으며, 실제 문서·모델 연결 후 별도 품질 평가가 필요합니다.
