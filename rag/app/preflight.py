"""Configuration-only startup check. No provider/DB calls, jobs or server startup."""
import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

REPO = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Check:
    level: str
    name: str
    message: str


def check_config(config, *, target='local', service='all'):
    checks = []

    def add(ok, name, message):
        checks.append(Check('OK' if ok else 'ERROR', name, message))

    def value(name, default=''):
        return (config.get(name) or default).strip()

    def absolute(name, default):
        try:
            path = Path(value(name, default)).expanduser()
            if not path.is_absolute():
                raise ValueError()
            return path.resolve()
        except (OSError, ValueError):
            checks.append(Check('ERROR', name, '절대 경로를 지정해 주세요.'))
            return None

    add(len(value('JWT_SECRET').encode()) >= 32, 'JWT_SECRET', 'Java와 같은 UTF-8 키, 최소 32바이트 필요')
    add(value('JWT_ISSUER') == 'frangizer', 'JWT_ISSUER', 'frangizer 설정 확인')
    add(value('JWT_AUDIENCE') == 'frangizer-api', 'JWT_AUDIENCE', 'frangizer-api 설정 확인')
    add(value('JWT_ALGORITHM', 'HS256') == 'HS256', 'JWT_ALGORITHM', 'HS256 고정')
    required = ('INTERNAL_TOKEN', 'DB_USERNAME', 'DB_PASSWORD', 'DEMO_PASSWORD') if service == 'all' else ('INTERNAL_TOKEN',)
    for key in required:
        add(bool(value(key)), key, '필수 설정 입력 여부')
    key = value('LLM_API_KEY')
    add(bool(key) and '...' not in key and not any(s in key for s in ('실제_API_키', '여기에_', 'changeme')),
        'LLM_API_KEY', 'API 키 입력·자리표시자 여부만 확인; API 인증은 검사하지 않음')
    model = value('LLM_MODEL')
    add(bool(model) and not model.startswith('sk-') and '...' not in model and not any(c.isspace() for c in model),
        'LLM_MODEL', 'API 키가 아닌 모델 ID 필요; 계정별 모델 접근은 별도 확인')
    model = value('EMBEDDING_MODEL')
    dimensions = {'text-embedding-3-small': 1536, 'text-embedding-3-large': 3072}
    add(model in dimensions, 'EMBEDDING_MODEL', '지원 임베딩 모델 설정 확인')
    if value('EMBEDDING_DIMENSIONS'):
        try:
            valid = 1 <= int(value('EMBEDDING_DIMENSIONS')) <= dimensions.get(model, 0)
        except ValueError:
            valid = False
        add(valid, 'EMBEDDING_DIMENSIONS', '모델의 지원 차원 범위 확인; 변경 시 재색인 필요')

    urls = (('DB_URL', 'jdbc:mariadb://'), ('RAG_URL', 'http')) if service == 'all' else ()
    for key, prefix in urls:
        url = value(key)
        try:
            parsed = urlsplit(url.removeprefix('jdbc:'))
            valid = (url.startswith(prefix) and bool(parsed.hostname)
                     and parsed.scheme in ('mariadb', 'http', 'https'))
            if key == 'DB_URL':
                valid = valid and parsed.path not in ('', '/')
            _ = parsed.port  # Reject invalid port syntax without showing a URL/password.
        except ValueError:
            parsed, valid = None, False
        add(valid, key, '서비스 주소 형식 확인; 실제 연결은 검사하지 않음')
        if target == 'docker' and valid:
            add(parsed.hostname not in ('localhost', '127.0.0.1', '::1'), key + '_NETWORK',
                '다른 컨테이너는 localhost 대신 Compose 서비스 이름 사용')

    document_root = absolute('DOCUMENT_STORE_DIR', '/var/lib/frangizer')
    index_root = absolute('FAISS_DIR', '/var/lib/frangizer/faiss')
    state_db = absolute('RAG_STATE_DB', '/var/lib/frangizer/rag/query-results.sqlite3')
    if target == 'local' and state_db and state_db.exists():
        add(state_db.is_file(), 'RAG_STATE_DB_FILE', '상태 DB 경로가 폴더가 아닌 파일인지 확인')
    if document_root:
        for name, path in (('FAISS_DIR', index_root), ('RAG_STATE_DB', state_db)):
            if path:
                add(path.is_relative_to(document_root) and path != document_root, name,
                    '문서 공유 저장 경로 아래에 지속 상태 배치')
        if target == 'local':
            for name, path in (('DOCUMENT_STORE_DIR', document_root), ('FAISS_DIR', index_root),
                               ('RAG_STATE_DB', state_db.parent if state_db else None)):
                if path is None:
                    continue
                try:
                    if path.exists() and not path.is_dir():
                        raise ValueError()
                    ancestor = path
                    while not ancestor.exists():
                        ancestor = ancestor.parent
                    add(ancestor.is_dir() and os.access(ancestor, os.W_OK | os.X_OK), name + '_ACCESS',
                        '저장 경로 또는 생성할 상위 폴더의 쓰기 권한 확인')
                except (ValueError, OSError):
                    add(False, name + '_ACCESS', '저장 경로와 권한을 확인해 주세요.')

    recipe = value('RECIPE_STRUCTURED_FILE')
    if not recipe:
        checks.append(Check('WARN', 'RECIPE_STRUCTURED_FILE', '구조화 레시피 빠른 경로가 꺼져 있음'))
    else:
        path = absolute('RECIPE_STRUCTURED_FILE', '')
        if path:
            if target == 'docker' and not path.exists():
                checks.append(Check('WARN', 'RECIPE_STRUCTURED_FILE', '컨테이너 경로의 COPY 또는 마운트는 이미지 도착 후 확인'))
            else:
                try:
                    data = json.loads(path.read_text())
                    valid = (data.get('schema') == 'frangizer.recipe-structured.draft'
                             and data.get('schema_version') == 0 and bool(data.get('doc_id'))
                             and bool(data.get('menus')) and bool(data.get('recipe_versions')))
                except (OSError, ValueError, AttributeError):
                    valid = False
                add(valid, 'RECIPE_STRUCTURED_FILE', '레시피 JSON 파일과 기본 형식 확인')
    if target == 'docker':
        checks.append(Check('WARN', 'SHARED_VOLUME', 'backend/RAG의 같은 볼륨·경로 연결은 Compose에서 별도 확인'))
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=REPO / '.env')
    parser.add_argument('--target', choices=('local', 'docker'), default='local')
    parser.add_argument('--service', choices=('all', 'rag'), default='all')
    args = parser.parse_args(argv)
    try:
        config = {**dotenv_values(args.env_file), **os.environ}
        checks = check_config(config, target=args.target, service=args.service)
    except (OSError, ValueError):
        print('[ERROR] CONFIG: 설정 파일을 읽을 수 없습니다.')
        return 1
    for item in checks:
        print(f'[{item.level}] {item.name}: {item.message}')
    failures = sum(c.level == 'ERROR' for c in checks)
    print(f'Configuration errors: {failures}. No API/DB calls, server startup or indexing performed.')
    print('첫 색인 전 /health=503은 정상입니다. Docker에서 backend 시작을 RAG ready 상태에 종속시키지 마세요.')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
