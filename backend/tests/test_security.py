import pytest
from app import security


def test_signature_and_tamper(monkeypatch):
    monkeypatch.setenv('DEMO_SIGNING_SECRET', 'this-is-a-testing-secret-only')
    token = security.sign({'session_id': 'x', 'state': {'turn_count': 0}})
    assert security.verify(token)['session_id'] == 'x'
    with pytest.raises(ValueError):
        security.verify(token.replace('x', 'y', 1))
