# RAG 서비스

Java 게이트웨이(:8080)의 내부 요청을 FastAPI(:8001)가 처리합니다. 상담은 LangChain + LangGraph 단일 에이전트이며, 레시피도 기존 `/rag-query`를 사용합니다. 별도 레시피 RDB·공개 API는 추가하지 않았습니다.

구현된 경로:

| 경로 | 인증 | 동작 |
|---|---|---|
| `POST /rag-query` | 서비스 토큰 + 원 사용자 JWT | 허용 판본 검색·상담 또는 구조화 레시피 응답 |
| `POST /internal/v1/index-jobs` | 서비스 토큰 | 202 작업 접수·동일 요청의 기존 상태 반환 |
| `GET /internal/v1/index-jobs/{job_id}` | 서비스 토큰 | 작업 상태 조회 |
| `GET /health` | 서비스 토큰 | 모델 설정·유효한 검색 산출물 준비 여부 |

현행 C 백엔드의 **같은 job_id로 POST를 반복하는 조회 방식**도 지원합니다. 후속 GET 조회로 전환해도 RAG 변경 없이 연결할 수 있습니다. `docs/api` 계약은 변경하지 않았습니다. 문서 상세의 `sections` 반환을 위한 새 내부 API는 아직 없습니다.

## 설치

Python 3.11, Java 검사에는 Java 25를 사용합니다.

```bash
cd /Users/inwoopark/workspace/ai-service/ai-service-project/frangizer/rag
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

외부 모델·임베딩 호출 없이 API, 그래프, 실제 PDF 추출·FAISS 생성/검색, 판본/권한 경계, 레시피 응답을 검사합니다. 기본 검사에서 실제 Java JWT 검사 3개는 건너뜁니다. 다음 절차로 Java까지 검사할 수 있습니다.

SWIG 확장 모듈의 알려진 초기화 경고 3종만 `pytest.ini`에서 제외하고 나머지 경고는 실패 처리합니다. FAISS import 시 이 경고를 일괄 `-W error`로 올리면 네이티브 확장 초기화가 비정상 종료할 수 있으므로 위 명령을 사용합니다.

## Java와 실제 HTTP 연동 검사

기존 서버·DB·`.env`를 사용하지 않습니다. 임시 H2, 임시 저장 폴더, 빈 포트를 사용하고 종료 시 정리합니다. 실제 Java 인증·업무 코드와 FastAPI·PDF·FAISS를 연결하되 임베딩은 테스트 대역이며 LLM 호출은 금지합니다.

```bash
cd /Users/inwoopark/workspace/ai-service/ai-service-project/frangizer/rag
source .venv/bin/activate
# macOS; 다른 OS에서는 설치된 Java 25의 JAVA_HOME을 지정
export JAVA_HOME=$(/usr/libexec/java_home -v 25)
export PATH="$JAVA_HOME/bin:$PATH"
mkdir -p .demo
../backend/gradlew -p ../backend \
  -I "$PWD/tests/java/classpath.init.gradle" -q testClasses ragInteropClasspath \
  > .demo/backend-classpath.txt
export FRANGIZER_JAVA_CP="$(tail -n 1 .demo/backend-classpath.txt)"
python -m pytest -q
python -m tests.backend_smoke
```

확인 범위: 역할별 Java JWT → PDF 업로드 → 승인 → 비동기 색인 → 기존 POST 폴링 → 배포 → 점주·본사 질문 → **20 mL / 출처 PDF 5쪽** → 같은 요청 재사용 → 예비 점주 접근 차단. 테스트 안에서 v1.1을 명시적으로 업로드·승인·배포하고, **2026-10-26 00:00 KST 전후 20→15 mL 전환**도 확인합니다. staged 폴더를 자동 색인하는 기능은 없습니다.

이 검사를 실제 임베딩 검색·LLM 상담 품질, MariaDB 또는 브라우저 E2E 통과로 해석하지 않습니다.

## 실행 전 점검

실제 API·DB 호출, 서버 시작, 색인 없이 설정과 경로만 확인합니다. 비밀값은 출력하지 않습니다.

```bash
cd /Users/inwoopark/workspace/ai-service/ai-service-project/frangizer/rag
source .venv/bin/activate
python -m app.preflight --env-file ../.env
```

프로세스 환경변수가 `.env`보다 우선합니다(uvicorn과 동일). 통과는 키의 실제 유효성, 계정의 모델 접근 권한, DB 연결, 답변 품질을 보장하지 않습니다. 선택한 `gpt-5.4-mini` 요청 형식은 네트워크 없는 LangChain 검사에도 포함합니다.

로컬 개발 웹은 `frontend/.env.development.local`에 `VITE_USE_MOCK=false`, `VITE_API_BASE_URL=http://localhost:8080/api/v1`을 설정합니다. Docker 프론트도 **빌드 시** `VITE_USE_MOCK=false`를 주입해야 합니다. 생산 빌드의 API 기본 경로는 `/api/v1`입니다. 생성된 프론트 JS에 서버용 API 키나 내부 토큰을 넣지 않습니다.

Docker 구성 도착 후 확인할 연결 조건:

- backend 빌드 context는 저장소 루트, Dockerfile은 `backend/Dockerfile`. Gradle 작업 위치는 `/src/backend`이며 Swagger YAML이 JAR에 포함되어야 합니다. 루트 `.dockerignore`가 실제 `.env`·가상환경·실행 산출물을 제외합니다.
- 컨테이너 간 `DB_URL`, `RAG_URL`은 localhost가 아닌 Compose 서비스 주소를 사용합니다. 로컬 `.env`의 3307은 호스트 연결용이며 DB 컨테이너 내부 포트와 구분합니다.
- backend/RAG가 같은 볼륨을 `/var/lib/frangizer`에 마운트하고 FAISS·상태 DB도 그 아래에 둡니다. 레시피 JSON은 `data/structured/demo_cafe_recipes.json`을 이미지에 COPY하거나 파일로 마운트하고, 경로도 컨테이너 내부 위치로 바꿉니다. 호스트의 절대 경로를 그대로 넘기지 않습니다.
- RAG 명령은 `uvicorn app.bootstrap:create_service --factory --host 0.0.0.0 --port 8001 --workers 1`입니다. 이미지 Python은 검증한 3.11을 사용합니다.
- **첫 색인 전 `/health=503`은 정상**입니다. backend의 시작을 RAG `service_healthy`에 종속시키면 초기 업로드·승인·색인을 시작하지 못합니다. RAG 프로세스 시작과 답변 준비를 구분하고 backend는 초기 색인을 요청할 수 있어야 합니다.

설정이 주입된 RAG 컨테이너 내부에서는 `python -m app.preflight --target docker --service rag`로 검사할 수 있습니다. 이 모드는 RAG에 필요 없는 DB 계정·시연 비밀번호를 요구하지 않습니다. 컨테이너의 같은 볼륨 연결과 실제 네트워크는 Compose/E2E에서 별도로 검증합니다.

## 실제 서비스 실행

루트 `.env.example`을 참고해 **루트 `.env` 한 곳**에 backend/RAG 공통 값을 설정합니다. 실제 비밀값은 Git에 넣지 않습니다. RAG 전용 항목은 `rag/.env.example`에 정리했습니다.

- `JWT_SECRET`: Java와 동일한 UTF-8 문자열, 최소 32바이트. Base64 디코딩하지 않습니다.
- `INTERNAL_TOKEN`, `JWT_ISSUER=frangizer`, `JWT_AUDIENCE=frangizer-api`를 공유합니다. 알고리즘은 HS256으로 고정합니다.
- `LLM_API_KEY`, `LLM_MODEL`, `EMBEDDING_MODEL`을 명시합니다. 임베딩 어댑터는 `text-embedding-3-small` / `text-embedding-3-large`를 지원하며 자동 기본 모델은 없습니다. 별도 `EMBEDDING_API_KEY`도 사용할 수 있습니다.
- `DOCUMENT_STORE_DIR`: Java와 같은 절대 경로. 공유 볼륨은 `/var/lib/frangizer`입니다. 로컬에서는 쓰기 가능한 저장소의 `var/` 절대 경로를 사용할 수 있습니다.
- `FAISS_DIR`: 기본 `/var/lib/frangizer/faiss`. `RAG_STATE_DB`: 질의 결과 재사용용 SQLite 경로.
- `RECIPE_STRUCTURED_FILE`: 선택 사항. 구조화 경로를 켜려면 저장소의 `data/structured/demo_cafe_recipes.json` 절대 경로를 지정합니다.

```bash
cd /Users/inwoopark/workspace/ai-service/ai-service-project/frangizer/rag
source .venv/bin/activate
python -m uvicorn app.bootstrap:create_service --factory \
  --env-file ../.env --host 127.0.0.1 --port 8001 --workers 1
```

Docker에서는 내부 네트워크 접근을 위해 `--host 0.0.0.0`을 사용합니다. Java의 `RAG_URL`은 실행 위치에 맞춰 `http://localhost:8001` 또는 `http://rag:8001`로 설정합니다. 브라우저는 Java API만 호출합니다.

실행 순서는 RAG → Java → 본사 로그인·문서 업로드·승인 → 색인 성공 확인 → 배포 → 점주 질문입니다. 레시피 시연 자료는 manifest의 `document_id=demo-cafe-recipes`로 등록해 구조화 JSON과 연결합니다. 최초 색인 전 health는 503입니다. 설정·유효한 산출물이 준비되면 200이며, health가 외부 제공자의 인증·답변 품질까지 검증하지는 않습니다. 모델 설정이 없으면 질문은 503, 임베딩 설정이 없으면 색인은 `EMBEDDING_NOT_CONFIGURED`로 실패합니다.

## 검색과 색인 경계

- 저장 키는 `DOCUMENT_STORE_DIR` 아래 `documents/...`만 허용하고 경로 이탈·심볼릭 링크 이탈·원본 SHA256 불일치를 차단합니다.
- A의 PDF/TXT/MD 추출·분할을 사용합니다. FAISS, chunks.jsonl, build-manifest, quality-report를 한 revision으로 원자적으로 저장합니다. 같은 입력·설정은 재사용하고 모델·차원 변경은 새 revision을 만듭니다.
- 서비스는 **단일 worker**, 색인 동시 실행 1개·대기 포함 최대 16개입니다. 같은 작업 ID와 다른 내용은409, 과부하는503입니다. 재시작 시 남은 queued/running은 failed + `INTERRUPTED`가 됩니다. 새 job_id로 다시 요청해야 합니다.
- 검색은 Java가 전달한 `(document_id, version_id, index_revision)`에만 접근합니다. 브랜드·역할·조각 적용 기간을 **유사도 순위 계산 전에** 제한합니다. 허용 범위를 자동으로 넓히지 않습니다.
- 모델·차원·정규화 방식·본문 해시·조각 매핑을 검사합니다. pickle을 읽지 않습니다. 조회 최대 5조각, 배치 최대 12,000자이며 원문 섹션 조회도 이미 확보한 조각에 한정합니다. 현재 긴 조문의 다른 분할 조각을 자동 확장하지는 않습니다.
- `legal_temporal_reviewed=false`인 공식 자료를 인용하면 `[시행일 미검수]`를 답변 앞에 붙입니다. 표의 `table_needs_review` 또는 `table_pairing_mismatch` 경고가 있는 출처로는 수치를 확정 답변하지 않고 `needs_review`로 반환합니다. OCR·법률적 정확성 평가는 별도로 필요합니다.

## 레시피의 생성 없는 응답

구조화 파일을 설정하면 명확한 단일 메뉴·기본 1잔의 계량·제조 순서를 저장된 값으로 반환합니다. 양·단위 환산을 하지 않습니다. 미지원 질문은 상담 에이전트가 처리합니다.

구조화 JSON 자체를 배포 증거로 쓰지 않습니다. **Java의 허용 판본 + 브랜드/역할/시점 + 색인 원본 SHA256 + 쪽 번호 + 본문의 재료량·순서 대조**를 통과한 경우에만 사용합니다. A의 파일은 draft이고, 본문 대조가 온도 구간·모든 옵션의 의미까지 검수하는 것은 아니므로 시연 전 데이터 담당자의 원문 검수도 필요합니다.

예: `아이스 바닐라라떼 시럽 얼마 넣어?`는 정확한 값·출처를 반환합니다. 온도 미지정이어도 요청한 재료량이 HOT/ICED에서 같으면 공통 값을 바로 반환합니다. 양이 다르면 온도를 확인하고, `아이스 딸기라떼 다음 단계?`는 완료 단계를 확인합니다. `1단계 완료, 다음 단계?`는 저장된 2단계를 반환합니다. 여러 메뉴 비교·용량 변경·옵션 변경·펌프 환산은 빠른 경로로 강제 처리하지 않습니다. 대화의 예전 답변을 완료 단계로 간주하지 않습니다.

응답 계약은 동일하며 이 경로는 `retrieval.llm_called=false`, 모델/임베딩 토큰 0입니다. 문맥에서 생략된 메뉴를 복원하는 질문은 현재 일반 상담 경로를 이용합니다.

## 상담·재시도

상담 도구는 문서 검색과 확보한 조각 읽기뿐입니다. 외부 검색·임의 SQL·승인/환불 실행 도구는 없습니다. 기본 예산은 모델 최대4회, 도구3회, 검색2회이며 요청 deadline을 적용합니다. 일반적인 단일 검색 흐름은 판단→검색→판단→최종 출력으로 모델3회가 들 수 있습니다.

같은 사용자·question_id·attempt_id의 결과/안전한 오류는 SQLite에 24시간 재사용하고 동시 요청을 합칩니다. 실패 재실행은 새 attempt_id를 사용합니다. 여러 worker/복제본을 지원하는 공유 작업 저장소는 아직 없습니다.

인식 가능한 연락처·명시적 고객명/주문번호를 모델 입력·응답에서 마스킹하고 자동 외부 추적은 끕니다. 모든 비정형 개인정보를 식별한다는 보장은 아닙니다. 출처 검증이 모든 답변 문장의 의미적 정확성을 보장하지는 않습니다.
