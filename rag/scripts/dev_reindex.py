#!/usr/bin/env python3
"""One-document development reindex. No seed, upload or backend source changes.

Run from anywhere: python3 rag/scripts/dev_reindex.py --document-id ID [--apply]
Requires the running local Docker Compose stack. Intended for reviewed tables.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
DB_CLIENT = ('MYSQL_PWD="$MARIADB_PASSWORD" mariadb --user="$MARIADB_USER" '
             '--database="$MARIADB_DATABASE" --default-character-set=utf8mb4 '
             '--batch --raw --skip-column-names')
RAG_CLIENT = '''
import json,os,sys,urllib.request,urllib.error
p=json.load(sys.stdin)
headers={'X-Internal-Token':os.environ['INTERNAL_TOKEN'],'Content-Type':'application/json'}
request=urllib.request.Request('http://127.0.0.1:8001'+p['path'],headers=headers,
    data=json.dumps(p['body']).encode() if p['body'] is not None else None,
    method='POST' if p['body'] is not None else 'GET')
try:
    with urllib.request.urlopen(request,timeout=15) as response:
        print(response.read().decode())
except urllib.error.HTTPError as error:
    print(json.dumps({'http_error':error.code,'response':json.loads(error.read())}))
'''
VALIDATE_INDEX = '''
import json,sys
from app.indexing.job import default_roots,validate_revision
from app.indexing.models import IndexJobRequest
from app.retrieval.base import Evidence
from app.schemas import IndexedChunkMetadata
from app.generation.table_evidence import table_rows
p=json.load(sys.stdin)
req=IndexJobRequest.model_validate(p['request'])
directory=default_roots()[1]/'revisions'/p['job']['index_revision']
validate_revision(directory,req,p['job']['index_revision'])
build=json.loads((directory/'build-manifest.json').read_text())
count=0
for line in (directory/'chunks.jsonl').read_text().splitlines():
    r=json.loads(line)
    e=Evidence(text=r['text'],metadata=IndexedChunkMetadata.model_validate(r['metadata']),
        quality_flags=tuple(r.get('review',{}).get('quality_flags',[])),
        parent_section_key=r.get('section',{}).get('parent_section_key'),table=r.get('table'))
    count+=len(table_rows(e))
print(json.dumps({'chunk_count':build['chunk_count'],'structured_rows':count,
                  'source_sha256':build['source_sha256']}))
'''


def command(args, data):
    result = subprocess.run(['docker', 'compose', *args], cwd=ROOT, input=data,
                            text=True, capture_output=True, timeout=40)
    if result.returncode:
        raise RuntimeError('Docker 명령 실패: ' + result.stderr.strip()[-500:])
    return result.stdout.strip()


def db(sql):
    return command(['exec', '-T', 'db', 'sh', '-c', DB_CLIENT], sql)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', value):
        raise ValueError('허용되지 않은 ID')
    return "'" + value + "'"


def current_version(document_id):
    # Backend DATETIME values are UTC; publication and approval remain untouched.
    columns = ['document_id', 'version_id', 'title', 'version', 'category', 'material_type',
               'is_demo', 'visibility', 'lifecycle', 'index_status', 'index_revision',
               'original_source', 'indexing_policy', 'storage_key', 'sha256', 'media_type']
    pairs = ','.join(f"'{name}',v.{name}" for name in columns)
    rows = db(f"""SELECT JSON_OBJECT('brand_id',d.brand_id,{pairs},
        'effective_from',DATE_FORMAT(v.effective_from,'%Y-%m-%dT%H:%i:%s.%f'))
        FROM document_versions v JOIN documents d ON d.document_id=v.document_id
        WHERE v.document_id={identifier(document_id)} AND v.lifecycle='published'
          AND v.index_status='ready' AND v.index_revision IS NOT NULL
          AND v.effective_from<=UTC_TIMESTAMP(6)
          AND (v.effective_until IS NULL OR UTC_TIMESTAMP(6)<v.effective_until);""")
    values = [json.loads(line) for line in rows.splitlines() if line]
    if len(values) != 1:
        raise RuntimeError('현재 배포·색인 완료 판본이 정확히 한 개 있어야 합니다.')
    return values[0]


def index_request(version, job_id):
    fields = ('brand_id', 'document_id', 'version_id', 'title', 'version', 'category',
              'material_type', 'is_demo', 'visibility', 'original_source', 'indexing_policy')
    body = {k: version[k] for k in fields}
    body['is_demo'] = bool(body['is_demo'])
    for k in ('original_source', 'indexing_policy'):
        if isinstance(body[k], str):
            body[k] = json.loads(body[k])
    instant = datetime.fromisoformat(version['effective_from']).replace(tzinfo=timezone.utc)
    body['effective_from'] = instant.astimezone(ZoneInfo('Asia/Seoul')).isoformat()
    body['job_id'] = job_id
    body['source'] = {k: version[k] for k in ('storage_key', 'sha256', 'media_type')}
    return body


def rag(path, body=None):
    result = json.loads(command(['exec', '-T', 'rag', 'python', '-c', RAG_CLIENT],
                                json.dumps({'path': path, 'body': body})))
    if 'http_error' in result:
        error = result.get('response', {}).get('error', {})
        raise RuntimeError(f"RAG HTTP {result['http_error']}: {error.get('code', 'ERROR')}")
    return result['data']


def validate_job(version, request, job):
    if (job['status'] != 'succeeded' or job['document_id'] != version['document_id']
            or job['version_id'] != version['version_id'] or job['job_id'] != request['job_id']
            or type(job['chunk_count']) is not int or job['chunk_count'] < 1):
        raise RuntimeError('색인 성공 결과의 문서·판본·작업·조각 수가 일치하지 않습니다.')
    identifier(job['index_revision'])
    result = json.loads(command(['exec', '-T', 'rag', 'python', '-c', VALIDATE_INDEX],
                                json.dumps({'request': request, 'job': job})))
    if (result['source_sha256'] != version['sha256'] or result['chunk_count'] != job['chunk_count']
            or result['structured_rows'] < 1):
        raise RuntimeError('새 색인의 원본·조각 수·검수된 표 행 검증에 실패했습니다.')
    return result


def activate_sql(version, job):
    if type(job['chunk_count']) is not int or job['chunk_count'] < 1:
        raise ValueError('잘못된 조각 수')
    vid, old, new, jid = map(identifier, (version['version_id'], version['index_revision'],
                                         job['index_revision'], job['job_id']))
    sha = version['sha256']
    if not re.fullmatch('[a-f0-9]{64}', sha):
        raise ValueError('잘못된 원본 SHA256')
    return f"""START TRANSACTION;
UPDATE document_versions SET index_revision={new},index_status='ready'
WHERE version_id={vid} AND index_revision={old} AND sha256='{sha}'
  AND lifecycle='published' AND index_status='ready';
SET @changed=ROW_COUNT();
INSERT INTO index_jobs(job_id,version_id,status,chunk_count,index_revision,created_at,updated_at)
SELECT {jid},{vid},'succeeded',{job['chunk_count']},{new},UTC_TIMESTAMP(6),UTC_TIMESTAMP(6)
WHERE @changed=1;
COMMIT;
SELECT @changed;
"""


def reindex(version, backup, timeout=300):
    backup.mkdir(parents=True, exist_ok=False)
    (backup / 'before.json').write_text(json.dumps(version, ensure_ascii=False, indent=2))
    request = index_request(version, 'dev-reindex-' + uuid4().hex)
    (backup / 'request.json').write_text(json.dumps(request, ensure_ascii=False, indent=2))
    job = rag('/internal/v1/index-jobs', request)
    deadline, previous = time.monotonic() + timeout, None
    while True:
        (backup / 'job.json').write_text(json.dumps(job, ensure_ascii=False, indent=2))
        if job['status'] != previous:
            print('색인 상태:', job['status'], flush=True)
            previous = job['status']
        if job['status'] == 'failed':
            raise RuntimeError('색인 실패: ' + str(job.get('error_code')))
        if job['status'] == 'succeeded':
            break
        if time.monotonic() >= deadline:
            raise TimeoutError('색인 대기 시간 초과. 기존 revision은 유지됩니다.')
        time.sleep(2)
        job = rag('/internal/v1/index-jobs/' + request['job_id'])
    checked = validate_job(version, request, job)
    (backup / 'validation.json').write_text(json.dumps(checked, indent=2))
    if job['index_revision'] == version['index_revision']:
        print('이미 현재 코드의 revision입니다. DB 변경 없음.', flush=True)
        return job
    restore = (f"UPDATE document_versions SET index_revision={identifier(version['index_revision'])},"
               f"index_status='ready' WHERE version_id={identifier(version['version_id'])} "
               f"AND index_revision={identifier(job['index_revision'])};\n")
    (backup / 'restore.sql').write_text(restore)
    if db(activate_sql(version, job)).splitlines()[-1] != '1':
        raise RuntimeError('기존 revision이 변경돼 DB 갱신을 중단했습니다.')
    (backup / 'after.json').write_text(json.dumps({**version, 'index_revision': job['index_revision']},
                                                ensure_ascii=False, indent=2))
    print('적용 완료:', version['index_revision'], '→', job['index_revision'], flush=True)
    print('검수된 표 행:', checked['structured_rows'], flush=True)
    return job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--document-id', required=True)
    parser.add_argument('--apply', action='store_true', help='없으면 대상 조회만')
    parser.add_argument('--timeout', type=float, default=300)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout은 양수여야 합니다.')
    version = current_version(args.document_id)
    print('대상:', version['document_id'], '/', version['version_id'], flush=True)
    print('현재 revision:', version['index_revision'], flush=True)
    if not args.apply:
        print('미리보기입니다. 실행하려면 --apply를 추가하세요.')
        return
    backup = ROOT / 'rag' / '.demo' / ('dev-reindex-' + uuid4().hex[:12])
    print('복구 자료:', backup, flush=True)
    reindex(version, backup, args.timeout)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, TimeoutError, subprocess.SubprocessError) as exc:
        print('중단:', str(exc), file=sys.stderr)
        sys.exit(1)
