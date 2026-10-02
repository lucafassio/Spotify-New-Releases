from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.crypto import decrypt_text, encrypt_text
from app.spotify import (
    ACCOUNTS_URL,
    API_URL,
    SCOPES,
    NotRegistered,
    TokenRejected,
    build_authorize_url,
    exchange_code,
    get_current_user,
    refresh_access_token,
)

TOKEN_URL = f'{ACCOUNTS_URL}/api/token'


def test_authorize_url_asks_every_scope_with_state():
    url = build_authorize_url('cid', 'http://127.0.0.1:8000/callback', 'abc')
    query = parse_qs(urlparse(url).query)

    assert query['state'] == ['abc']
    assert query['response_type'] == ['code']
    assert set(query['scope'][0].split()) == set(SCOPES)


def test_exchange_code_sends_client_auth(spotify):
    route = spotify.post(TOKEN_URL).respond(json={'access_token': 'a', 'refresh_token': 'r'})

    tokens = exchange_code('cid', 'secret', 'code1', 'http://127.0.0.1:8000/callback')

    assert tokens['refresh_token'] == 'r'
    assert route.calls.last.request.headers['Authorization'].startswith('Basic ')
    assert b'grant_type=authorization_code' in route.calls.last.request.content


def test_refresh_rejected_raises_oauth_code(spotify):
    spotify.post(TOKEN_URL).respond(400, json={'error': 'invalid_grant'})

    with pytest.raises(TokenRejected) as error:
        refresh_access_token('cid', 'secret', 'dead')

    assert error.value.error == 'invalid_grant'


def test_token_endpoint_5xx_is_not_a_rejection(spotify):
    spotify.post(TOKEN_URL).respond(503)

    with pytest.raises(httpx.HTTPStatusError):
        refresh_access_token('cid', 'secret', 'r')


def test_me_403_means_not_registered(spotify):
    spotify.get(f'{API_URL}/me').respond(403, json={'error': {'status': 403}})

    with pytest.raises(NotRegistered):
        get_current_user('a')


def test_fernet_roundtrip_hides_secret():
    token = encrypt_text('refresh-secreto')

    assert 'refresh-secreto' not in token
    assert decrypt_text(token) == 'refresh-secreto'
