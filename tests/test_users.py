import uuid

import pytest

from app import users
from app.crypto import decrypt_text, encrypt_text
from app.spotify import ACCOUNTS_URL

TOKEN_URL = f'{ACCOUNTS_URL}/api/token'


def new_spotify_id():
    '''
    Genera un spotify_user_id unico: get_access_token commitea y las filas sobreviven entre tests.
        Returns:
            spotify_user_id (str): id de prueba
    '''
    return f'u_{uuid.uuid4().hex[:10]}'


def test_shared_login_creates_user_with_encrypted_token(db):
    login = users.save_shared_login(db, new_spotify_id(), 'refresh-1')
    user = users.get_user(db, login['user_id'])

    assert login['disconnected_at'] is None
    assert user['connection_mode'] == 'shared'
    assert user['refresh_token_enc'] != 'refresh-1'
    assert decrypt_text(user['refresh_token_enc']) == 'refresh-1'


def test_login_again_reconnects_and_reports_since_when(db):
    spotify_user_id = new_spotify_id()
    first = users.save_shared_login(db, spotify_user_id, 'refresh-1')
    users.mark_disconnected(db, first['user_id'])

    again = users.save_shared_login(db, spotify_user_id, 'refresh-2')
    user = users.get_user(db, again['user_id'])

    assert again['user_id'] == first['user_id']
    assert again['disconnected_at'] is not None
    assert user['disconnected_at'] is None
    assert decrypt_text(user['refresh_token_enc']) == 'refresh-2'


def test_access_token_persists_rotated_refresh_token(db, spotify):
    login = users.save_shared_login(db, new_spotify_id(), 'refresh-1')
    spotify.post(TOKEN_URL).respond(json={'access_token': 'access-1', 'refresh_token': 'refresh-2'})

    access_token = users.get_access_token(db, users.get_user(db, login['user_id']))

    assert access_token == 'access-1'
    assert decrypt_text(users.get_user(db, login['user_id'])['refresh_token_enc']) == 'refresh-2'


def test_access_token_keeps_refresh_token_when_not_rotated(db, spotify):
    login = users.save_shared_login(db, new_spotify_id(), 'refresh-1')
    spotify.post(TOKEN_URL).respond(json={'access_token': 'access-1'})

    users.get_access_token(db, users.get_user(db, login['user_id']))

    assert decrypt_text(users.get_user(db, login['user_id'])['refresh_token_enc']) == 'refresh-1'


def test_invalid_grant_disconnects_user(db, spotify):
    login = users.save_shared_login(db, new_spotify_id(), 'refresh-1')
    spotify.post(TOKEN_URL).respond(400, json={'error': 'invalid_grant'})

    with pytest.raises(users.UserDisconnected):
        users.get_access_token(db, users.get_user(db, login['user_id']))

    user = users.get_user(db, login['user_id'])
    assert user['disconnected_at'] is not None
    assert user['refresh_token_enc'] is None


def test_disconnected_user_does_not_call_spotify(db, spotify):
    login = users.save_shared_login(db, new_spotify_id(), 'refresh-1')
    users.mark_disconnected(db, login['user_id'])

    with pytest.raises(users.UserDisconnected):
        users.get_access_token(db, users.get_user(db, login['user_id']))

    assert not spotify.calls


def test_invalid_client_of_shared_app_fails_loud_without_disconnecting(db, spotify):
    login = users.save_shared_login(db, new_spotify_id(), 'refresh-1')
    spotify.post(TOKEN_URL).respond(401, json={'error': 'invalid_client'})

    with pytest.raises(RuntimeError):
        users.get_access_token(db, users.get_user(db, login['user_id']))

    assert users.get_user(db, login['user_id'])['disconnected_at'] is None


def test_invalid_client_of_own_app_disconnects_user(db, spotify):
    user_id = db.execute(
        "INSERT INTO users (spotify_user_id, connection_mode, client_id, client_secret_enc, refresh_token_enc) VALUES (%s, 'own', 'own-cid', %s, %s) RETURNING id",
        (new_spotify_id(), encrypt_text('own-secret'), encrypt_text('refresh-1')),
    ).fetchone()[0]
    route = spotify.post(TOKEN_URL).respond(401, json={'error': 'invalid_client'})

    with pytest.raises(users.UserDisconnected):
        users.get_access_token(db, users.get_user(db, user_id))

    assert route.calls.last.request.headers['Authorization'] == 'Basic b3duLWNpZDpvd24tc2VjcmV0'
    assert users.get_user(db, user_id)['disconnected_at'] is not None
