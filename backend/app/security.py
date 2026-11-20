"""Simple signed session state: avoids a database in the Vercel free demo.

A signed token prevents casual client-side editing of lead stage or a booking.
It is not a shared appointment calendar, account-level rate limit, or durable
persistence service: users can start a fresh session by clearing browser data.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

SESSION_AGE_SECONDS = 60 * 60 * 48


def _secret() -> bytes:
    secret = os.environ.get('DEMO_SIGNING_SECRET') or os.environ.get('OPENROUTER_API_KEY')
    if not secret:
        # Intentionally refuse chat sessions in deploys without configured secrets.
        raise RuntimeError('Set DEMO_SIGNING_SECRET and OPENROUTER_API_KEY in the backend environment.')
    return secret.encode('utf-8')


def sign(payload: dict) -> str:
    serialized = json.dumps({'payload': payload, 'issued_at': int(time.time())}, separators=(',', ':'), sort_keys=True).encode()
    body = base64.urlsafe_b64encode(serialized).rstrip(b'=')
    signature = hmac.new(_secret(), body, hashlib.sha256).digest()
    return body.decode() + '.' + base64.urlsafe_b64encode(signature).rstrip(b'=').decode()


def verify(token: str) -> dict:
    try:
        body, supplied = token.split('.', 1)
        expected = hmac.new(_secret(), body.encode(), hashlib.sha256).digest()
        received = base64.urlsafe_b64decode(supplied + '=' * (-len(supplied) % 4))
        if not hmac.compare_digest(expected, received):
            raise ValueError('Invalid session signature')
        data = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
        if time.time() - data['issued_at'] > SESSION_AGE_SECONDS or data['issued_at'] > time.time() + 60:
            raise ValueError('Expired session')
        return data['payload']
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError('Session expired or invalid; start a new demo session.') from exc
