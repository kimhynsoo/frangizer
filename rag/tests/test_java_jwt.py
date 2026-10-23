"""Optional real-Java interoperability check, enabled by FRANGIZER_JAVA_CP."""
import os
import subprocess
from pathlib import Path

import pytest

from app.auth import AuthSettings, check_jwt, check_principal
from tests.support import query_payload
from app.schemas import Principal


@pytest.mark.parametrize('role', ['owner', 'hq', 'prospect'])
def test_actual_java_jwt(role, tmp_path):
    cp = os.environ.get('FRANGIZER_JAVA_CP')
    if not cp:
        pytest.skip('Set FRANGIZER_JAVA_CP to backend main/test runtime classpath')
    source = Path(__file__).parent / 'java' / 'JwtInterop.java'
    subprocess.run(['javac', '-cp', cp, '-d', str(tmp_path), str(source)], check=True, capture_output=True)
    result = subprocess.run(['java', '-cp', str(tmp_path) + os.pathsep + cp, 'com.frangizer.auth.JwtInterop', role],
                            check=True, capture_output=True, text=True)
    auth = AuthSettings('offline-internal', 'offline-java-interop-key-at-least-32-bytes', 'frangizer', 'frangizer-api')
    claims = check_jwt('Bearer ' + result.stdout, auth)
    principal = query_payload()['principal']
    principal.update(role=role, store_id='store-test' if role == 'owner' else None)
    check_principal(Principal.model_validate(principal), claims)
    assert claims['aud'] == ['frangizer-api']
    if role != 'owner':
        assert 'store_id' not in claims  # JJWT removes null claim values.
