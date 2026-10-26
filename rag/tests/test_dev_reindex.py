import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('dev_reindex', Path(__file__).parents[1] / 'scripts/dev_reindex.py')
dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev)


def version():
    return {'document_id': 'doc-1', 'version_id': 'doc-1-v1', 'brand_id': 'brand-1',
            'index_revision': 'idx-old', 'index_status': 'ready', 'lifecycle': 'published',
            'title': '합성 자료', 'version': 'v1', 'category': 'franchise',
            'material_type': 'public_disclosure', 'is_demo': 0, 'visibility': 'prospect',
            'effective_from': '2025-11-20T15:00:00.000000',
            'original_source': '{"title":"원문"}', 'indexing_policy': '{"exclude_pages":[]}',
            'storage_key': 'documents/doc-1/v1.pdf', 'sha256': 'a' * 64, 'media_type': 'application/pdf'}


def job(body, status='succeeded'):
    return {'job_id': body['job_id'], 'document_id': 'doc-1', 'version_id': 'doc-1-v1',
            'status': status, 'chunk_count': 5, 'index_revision': 'idx-new', 'error_code': 'EXTRACTION_FAILED'}


def test_request_preserves_source_and_backend_utc_to_kst_semantics():
    v = version()
    body = dev.index_request(v, 'job-1')
    assert body['effective_from'] == '2025-11-21T00:00:00+09:00'
    assert body['source'] == {k: v[k] for k in ('storage_key', 'sha256', 'media_type')}
    assert body['is_demo'] is False and body['indexing_policy'] == {'exclude_pages': []}
    assert v['lifecycle'] == 'published' and v['index_revision'] == 'idx-old'


@pytest.mark.parametrize('value', ["x' OR 1=1", 'x;DELETE', '../x', '`x`'])
def test_sql_identifiers_cannot_inject_commands(value):
    with pytest.raises(ValueError):
        dev.identifier(value)


def test_failed_indexing_never_updates_database(monkeypatch, tmp_path):
    monkeypatch.setattr(dev, 'rag', lambda path, body: job(body, 'failed'))
    monkeypatch.setattr(dev, 'db', lambda sql: pytest.fail('DB must not be changed'))
    with pytest.raises(RuntimeError, match='색인 실패'):
        dev.reindex(version(), tmp_path / 'backup')
    assert (tmp_path / 'backup/before.json').exists()
    assert not (tmp_path / 'backup/after.json').exists()


def test_artifact_validation_failure_never_updates_database(monkeypatch, tmp_path):
    monkeypatch.setattr(dev, 'rag', lambda path, body: job(body))
    def reject(*args):
        raise RuntimeError('invalid artifact')
    monkeypatch.setattr(dev, 'validate_job', reject)
    monkeypatch.setattr(dev, 'db', lambda sql: pytest.fail('DB must not be changed'))
    with pytest.raises(RuntimeError, match='invalid artifact'):
        dev.reindex(version(), tmp_path / 'backup')


@pytest.mark.parametrize('changed', ['1', '0'])
def test_activation_is_guarded_and_backed_up_without_changing_publication(monkeypatch, tmp_path, changed):
    updates = []
    monkeypatch.setattr(dev, 'rag', lambda path, body: job(body))
    monkeypatch.setattr(dev, 'validate_job', lambda *args: {'structured_rows': 3, 'chunk_count': 5})
    def db(sql):
        assert (tmp_path / 'backup/before.json').exists()
        assert (tmp_path / 'backup/restore.sql').exists()
        updates.append(sql)
        return changed
    monkeypatch.setattr(dev, 'db', db)
    if changed == '1':
        dev.reindex(version(), tmp_path / 'backup')
        after = json.loads((tmp_path / 'backup/after.json').read_text())
        assert after['index_revision'] == 'idx-new' and after['lifecycle'] == 'published'
    else:
        with pytest.raises(RuntimeError, match='기존 revision이 변경'):
            dev.reindex(version(), tmp_path / 'backup')
        assert not (tmp_path / 'backup/after.json').exists()
    assert len(updates) == 1
    assert "index_revision='idx-old'" in updates[0] and "WHERE @changed=1" in updates[0]
    assert "SET lifecycle" not in updates[0] and 'DELETE' not in updates[0]
