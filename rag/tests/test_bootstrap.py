from fastapi.testclient import TestClient

from app.bootstrap import create_service


def test_production_factory_without_provider_config_is_not_ready(monkeypatch, tmp_path):
    settings = {
        'INTERNAL_TOKEN': 'offline-bootstrap-token',
        'JWT_SECRET': 'offline-bootstrap-key-at-least-32-bytes',
        'JWT_ISSUER': 'frangizer', 'JWT_AUDIENCE': 'frangizer-api', 'JWT_ALGORITHM': 'HS256',
        'DOCUMENT_STORE_DIR': str(tmp_path / 'store'), 'FAISS_DIR': str(tmp_path / 'faiss'),
        'RAG_STATE_DB': str(tmp_path / 'queries.sqlite3'),
    }
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    for key in ('LLM_API_KEY', 'LLM_MODEL', 'EMBEDDING_API_KEY', 'EMBEDDING_MODEL', 'RECIPE_STRUCTURED_FILE'):
        monkeypatch.delenv(key, raising=False)
    with TestClient(create_service()) as client:
        assert client.get('/health').status_code == 401
        result = client.get('/health', headers={'X-Internal-Token': settings['INTERNAL_TOKEN']})
        assert result.status_code == 503 and result.json() == {'status': 'not_ready'}
        assert client.get('/internal/v1/index-jobs/missing', headers={
            'X-Internal-Token': settings['INTERNAL_TOKEN'],
        }).status_code == 404
