import json
from pathlib import Path

import pytest

from app.preflight import check_config, main


@pytest.fixture
def config(tmp_path):
    store = tmp_path / 'store'
    recipe = tmp_path / 'recipes.json'
    recipe.write_text(json.dumps({'schema': 'frangizer.recipe-structured.draft', 'schema_version': 0,
        'doc_id': 'test-recipes', 'menus': [{'menu_id': 'test'}], 'recipe_versions': [{'menu_id': 'test'}]}))
    return {
        'JWT_SECRET': 'offline-preflight-test-key-at-least-32-bytes',
        'JWT_ISSUER': 'frangizer', 'JWT_AUDIENCE': 'frangizer-api',
        'INTERNAL_TOKEN': 'offline-preflight-token', 'DEMO_PASSWORD': 'offline-demo',
        'DB_URL': 'jdbc:mariadb://127.0.0.1:3307/frangizer', 'DB_USERNAME': 'test', 'DB_PASSWORD': 'test',
        'RAG_URL': 'http://localhost:8001', 'LLM_API_KEY': 'offline-preflight-api-key',
        'LLM_MODEL': 'gpt-5.4-mini', 'EMBEDDING_MODEL': 'text-embedding-3-small',
        'DOCUMENT_STORE_DIR': str(store), 'FAISS_DIR': str(store/'faiss'),
        'RAG_STATE_DB': str(store/'rag/results.sqlite3'), 'RECIPE_STRUCTURED_FILE': str(recipe),
    }


def errors(config, **kwargs):
    return {c.name for c in check_config(config, **kwargs) if c.level == 'ERROR'}


def test_valid_local_config_needs_no_network_or_filesystem_writes(config):
    assert not errors(config)
    assert not Path(config['DOCUMENT_STORE_DIR']).exists()


def test_key_pasted_into_model_field_is_rejected_without_leaking_it(config):
    config['LLM_MODEL'] = 'sk-proj-offline-do-not-print-this'
    checks = check_config(config)
    assert 'LLM_MODEL' in errors(config)
    assert all(config['LLM_MODEL'] not in c.message for c in checks)


def test_empty_or_truncated_key_is_rejected(config):
    for value in ('', 'sk-proj-incomplete...'):
        config['LLM_API_KEY'] = value
        assert 'LLM_API_KEY' in errors(config)


def test_incorrect_jwt_settings_and_embedding_dimensions_are_rejected(config):
    config.update(JWT_SECRET='short', JWT_ISSUER='wrong', JWT_ALGORITHM='HS512', EMBEDDING_DIMENSIONS='0')
    assert {'JWT_SECRET','JWT_ISSUER','JWT_ALGORITHM','EMBEDDING_DIMENSIONS'} <= errors(config)


def test_docker_rejects_host_localhost_urls(config):
    assert {'DB_URL_NETWORK','RAG_URL_NETWORK'} <= errors(config, target='docker')
    config.update(DB_URL='jdbc:mariadb://db:3306/frangizer', RAG_URL='http://rag:8001')
    assert not errors(config, target='docker')


def test_storage_paths_must_share_root(config, tmp_path):
    config['FAISS_DIR'] = str(tmp_path/'separate-volume/faiss')
    assert 'FAISS_DIR' in errors(config)
    config['RAG_STATE_DB'] = 'relative/results.sqlite3'
    assert 'RAG_STATE_DB' in errors(config)


def test_missing_recipe_path_is_visible(config):
    config['RECIPE_STRUCTURED_FILE'] = '/nonexistent/preflight-recipe.json'
    assert 'RECIPE_STRUCTURED_FILE' in errors(config)
    config['RECIPE_STRUCTURED_FILE'] = ''
    assert any(c.name == 'RECIPE_STRUCTURED_FILE' and c.level == 'WARN' for c in check_config(config))


def test_cli_outputs_only_safe_messages(config, tmp_path, monkeypatch, capsys):
    for key in config:
        monkeypatch.delenv(key, raising=False)
    file = tmp_path/'.env'
    file.write_text('\n'.join(f'{k}={v}' for k,v in config.items()))
    assert main(['--env-file',str(file)]) == 0
    text = capsys.readouterr().out
    for key in ('LLM_API_KEY','JWT_SECRET','INTERNAL_TOKEN','DB_PASSWORD','DEMO_PASSWORD'):
        assert config[key] not in text
    assert 'No API/DB calls' in text


def test_rag_container_does_not_require_backend_credentials(config):
    for key in ('DB_URL','DB_USERNAME','DB_PASSWORD','DEMO_PASSWORD','RAG_URL'):
        config.pop(key)
    assert not errors(config, target='docker', service='rag')


def test_unknown_review_mode_is_rejected(config):
    config['RAG_REVIEW_MODE'] = 'disabled'
    assert 'RAG_REVIEW_MODE' in errors(config)
