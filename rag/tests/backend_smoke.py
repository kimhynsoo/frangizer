"""Real Java/H2↔FastAPI/PDF/FAISS HTTP check; fake embeddings, no model calls.

FRANGIZER_JAVA_CP and Java 25 are required. Existing DB/.env/services are untouched.
"""
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn

from app.auth import AuthSettings
from app.generation.agent import CounselAgent
from app.generation.recipe import RagRuntime, RecipeShortcut
from app.indexing.service import IndexService
from app.main import create_app
from app.retrieval.faiss_store import FaissRetriever
from tests.indexing.conftest import FakeEmbedder

REPO = Path(__file__).resolve().parents[2]


class NoModel:
    name = 'offline-model-disabled'
    async def decide(self, messages):
        raise AssertionError('This scenario must use structured recipe data')
    async def finalize(self, messages):
        raise AssertionError('No model calls allowed')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_for(client, url, process=None):
    until = time.monotonic() + 60
    while time.monotonic() < until:
        if process and process.poll() is not None:
            raise RuntimeError('Java process exited')
        try:
            if client.get(url).status_code in (200, 401):
                return
        except httpx.TransportError:
            pass
        time.sleep(.1)
    raise TimeoutError('Smoke server did not start')


def main():
    cp = os.environ.get('FRANGIZER_JAVA_CP')
    if not cp:
        raise SystemExit('FRANGIZER_JAVA_CP required; see README.md')
    java = str(Path(os.environ['JAVA_HOME']) / 'bin/java') if os.environ.get('JAVA_HOME') else 'java'
    auth = AuthSettings('offline-http-service-token', 'offline-http-signing-key-at-least-32-bytes', 'frangizer', 'frangizer-api')
    with tempfile.TemporaryDirectory(prefix='frangizer-smoke-') as temp:
        root = Path(temp)
        docroot, idxroot = root / 'store', root / 'faiss'
        retriever = FaissRetriever(idxroot, FakeEmbedder())
        agent = RagRuntime(CounselAgent(NoModel(), retriever),
                           RecipeShortcut(REPO / 'data/structured/demo_cafe_recipes.json', retriever))
        app = create_app(auth=auth, agent=agent, index_service=IndexService(docroot, idxroot, embedder=FakeEmbedder()),
                         state_db=str(root / 'query.sqlite3'), demo=True)
        rag_port, java_port = free_port(), free_port()
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=rag_port, log_level='error'))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        process = None
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{java_port}', timeout=30) as client, (root / 'backend.log').open('w+') as log:
                wait_for(client, f'http://127.0.0.1:{rag_port}/health')
                process = subprocess.Popen([
                    java, '-cp', cp, 'com.frangizer.FrangizerApplication',
                    '--spring.profiles.active=test', f'--server.port={java_port}', '--spring.config.import=',
                    f'--app.rag-url=http://127.0.0.1:{rag_port}', f'--app.internal-token={auth.internal_token}',
                    f'--app.jwt.secret={auth.jwt_secret}', '--app.jwt.issuer=frangizer', '--app.jwt.audience=frangizer-api',
                    f'--app.document-store-dir={docroot}', '--app.demo-password=offline-smoke-password',
                ], cwd=root, stdout=log, stderr=subprocess.STDOUT)
                try:
                    wait_for(client, '/api/v1/health', process)
                except Exception:
                    log.seek(0); print(log.read()[-6000:]); raise
                def call(method, path, role=None, key=None, **kwargs):
                    headers = {'Authorization': 'Bearer ' + tokens[role]} if role else {}
                    if key:
                        headers['Idempotency-Key'] = key
                    response = client.request(method, path, headers=headers, **kwargs)
                    assert response.is_success, (path, response.status_code, response.text)
                    return response.json()
                tokens = {role: call('POST', '/api/v1/login', json={
                    'username': username, 'password': 'offline-smoke-password',
                })['accessToken'] for role, username in (
                    ('hq', 'demo-hq-01'), ('owner', 'demo-owner-a001'), ('prospect', 'demo-prospect-01'),
                )}
                manifest = json.loads((REPO / 'data/manifest.json').read_text())
                doc = next(d for d in manifest['documents'] if d['local_key'] == 'demo-cafe-recipes'
                           and d['upload_metadata']['version'] == '1.0')
                meta = dict(doc['upload_metadata'], document_id=doc['server']['document_id'])
                with (REPO / 'data' / doc['path']).open('rb') as pdf:
                    uploaded = call('POST', '/api/v1/documents', 'hq', 'smoke-upload-0001', files={
                        'file': ('recipe.pdf', pdf, 'application/pdf'),
                        'metadata': (None, json.dumps(meta, ensure_ascii=False), 'application/json'),
                    })['data']
                base = '/api/v1/documents/' + uploaded['document_id'] + '/versions/' + uploaded['version_id']
                assert client.get(base, headers={'Authorization': 'Bearer ' + tokens['owner']}).status_code == 403
                approved = call('POST', base + '/approve', 'hq', 'smoke-approve-001', json={'review_note': '오프라인 계약 검증'})['data']
                until = time.monotonic() + 30
                while True:
                    job = call('GET', '/api/v1/index-jobs/' + approved['job_id'], 'hq')['data']
                    if job['status'] in ('succeeded', 'failed'):
                        break
                    assert time.monotonic() < until
                    time.sleep(.05)
                assert job['status'] == 'succeeded', job
                call('POST', base + '/publish', 'hq', 'smoke-publish-001', json={'notice_title': '합성 레시피 시연'})
                ask = {'question': '바닐라라테 시럽 몇 mL예요?', 'as_of': '2026-10-23T12:00:00+09:00'}
                for role in ('owner', 'hq'):
                    answer = call('POST', '/api/v1/rag-query', role, 'smoke-query-' + role, json=ask)['data']
                    assert answer['status'] == 'answered' and '20 mL' in answer['answer'], answer
                    assert answer['sources'][0]['version_id'] == uploaded['version_id']
                    assert answer['sources'][0]['page'] == 5
                    again = call('POST', '/api/v1/rag-query', role, 'smoke-query-' + role, json=ask)['data']
                    assert again['question_id'] == answer['question_id']
                assert client.get(base, headers={'Authorization': 'Bearer ' + tokens['prospect']}).status_code == 403
                # Explicit upload/approval/publication of staged v1.1; never automatic ingestion.
                future = next(d for d in manifest['documents'] if d['local_key'] == 'demo-cafe-recipes'
                              and d['upload_metadata']['version'] == '1.1')
                meta = dict(future['upload_metadata'], document_id=future['server']['document_id'])
                with (REPO / 'data' / future['path']).open('rb') as pdf:
                    uploaded = call('POST', '/api/v1/documents', 'hq', 'smoke-upload-0002', files={
                        'file': ('recipe-v1.1.pdf', pdf, 'application/pdf'),
                        'metadata': (None, json.dumps(meta, ensure_ascii=False), 'application/json'),
                    })['data']
                base = '/api/v1/documents/' + uploaded['document_id'] + '/versions/' + uploaded['version_id']
                approved = call('POST', base + '/approve', 'hq', 'smoke-approve-002', json={'review_note': '오프라인 판본 전환 검증'})['data']
                until = time.monotonic() + 30
                while True:
                    job = call('GET', '/api/v1/index-jobs/' + approved['job_id'], 'hq')['data']
                    if job['status'] in ('succeeded', 'failed'):
                        break
                    assert time.monotonic() < until
                    time.sleep(.05)
                assert job['status'] == 'succeeded', job
                call('POST', base + '/publish', 'hq', 'smoke-publish-002', json={'notice_title': '합성 시럽량 변경'})
                for date, version, amount in (('2026-10-25T23:59:59+09:00', '1.0', '20 mL'),
                                              ('2026-10-26T00:00:00+09:00', '1.1', '15 mL')):
                    response = call('POST', '/api/v1/rag-query', 'owner', 'smoke-cutover-' + version,
                                    json=dict(ask, as_of=date))['data']
                    assert amount in response['answer'] and response['sources'][0]['version'] == version, response
                print('PASS: Java login → upload PDF → approve → FAISS index → POST polling → publish → owner/HQ query (20 mL, p5) → repeated question; prospect denied.')
                print('PASS: explicit v1.1 publication; 2026-10-26 00:00 KST switches 20 mL/v1.0 → 15 mL/v1.1.')
                print('OFFLINE: H2 + synthetic PDF + fake embeddings, no LLM; not a MariaDB/browser or search quality evaluation.')
        finally:
            if process:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
            server.should_exit = True
            thread.join(timeout=30)


if __name__ == '__main__':
    main()
