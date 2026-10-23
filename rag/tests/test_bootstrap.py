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


def test_configured_factory_connects_recipe_and_scoped_retriever(monkeypatch, tmp_path):
    from pathlib import Path
    from app.bootstrap import LangChainModel
    from app.generation.recipe import RagRuntime
    from tests.support import ScriptedModel
    root = Path(__file__).resolve().parents[2]
    values = {
        'INTERNAL_TOKEN': 'offline-bootstrap-token',
        'JWT_SECRET': 'offline-bootstrap-key-at-least-32-bytes',
        'JWT_ISSUER': 'frangizer', 'JWT_AUDIENCE': 'frangizer-api', 'JWT_ALGORITHM': 'HS256',
        'DOCUMENT_STORE_DIR': str(tmp_path/'store'), 'FAISS_DIR': str(tmp_path/'faiss'),
        'RAG_STATE_DB': str(tmp_path/'queries.sqlite3'),
        'LLM_API_KEY': 'offline-no-provider-calls', 'LLM_MODEL': 'gpt-5.4-mini',
        'EMBEDDING_MODEL': 'text-embedding-3-small',
        'RECIPE_STRUCTURED_FILE': str(root/'data/structured/demo_cafe_recipes.json'),
    }
    for key,value in values.items():
        monkeypatch.setenv(key,value)
    for key in ('EMBEDDING_DIMENSIONS','EMBEDDING_API_KEY','EMBEDDING_BASE_URL'):
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setattr(LangChainModel, 'from_env', lambda: ScriptedModel())
    app = create_service()
    try:
        assert isinstance(app.state.agent, RagRuntime)
        assert app.state.agent.retriever is app.state.agent.recipe.retriever
        assert app.state.index_service.embedder is app.state.agent.retriever.embedder
        assert not app.state.agent.retriever.ready
    finally:
        app.state.index_service.embedder.client.close()
