"""Ensure Hermes does not write to read-only HOME on serverless functions."""

import os
import tempfile
from pathlib import Path

from app import hermes_runtime


def test_vercel_sets_writable_hermes_home(monkeypatch):
    monkeypatch.setenv('VERCEL', '1')
    monkeypatch.delenv('AWS_LAMBDA_FUNCTION_NAME', raising=False)
    monkeypatch.delenv('HERMES_HOME', raising=False)

    hermes_runtime._configure_hermes_home()

    assert os.environ['HERMES_HOME'] == str(Path(tempfile.gettempdir()) / 'hermes-agent')


def test_lambda_sets_writable_hermes_home(monkeypatch):
    monkeypatch.delenv('VERCEL', raising=False)
    monkeypatch.setenv('AWS_LAMBDA_FUNCTION_NAME', 'test-hermes-agent')
    monkeypatch.delenv('HERMES_HOME', raising=False)

    hermes_runtime._configure_hermes_home()

    assert os.environ['HERMES_HOME'] == str(Path(tempfile.gettempdir()) / 'hermes-agent')


def test_explicit_hermes_home_is_never_overridden(monkeypatch):
    monkeypatch.setenv('VERCEL', '1')
    monkeypatch.setenv('HERMES_HOME', '/tmp/my-hermes-data')

    hermes_runtime._configure_hermes_home()

    assert os.environ['HERMES_HOME'] == '/tmp/my-hermes-data'


def test_local_development_keeps_hermes_default(monkeypatch):
    monkeypatch.delenv('VERCEL', raising=False)
    monkeypatch.delenv('AWS_LAMBDA_FUNCTION_NAME', raising=False)
    monkeypatch.delenv('HERMES_HOME', raising=False)

    hermes_runtime._configure_hermes_home()

    assert 'HERMES_HOME' not in os.environ
