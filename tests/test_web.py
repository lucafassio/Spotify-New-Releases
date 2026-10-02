import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app import users
from app.spotify import ACCOUNTS_URL, API_URL
from app.web import app, get_conn

TOKEN_URL = f'{ACCOUNTS_URL}/api/token'


@pytest.fixture
def client():
    '''
    Cliente de la web sin DB: sirve para rutas que no la tocan.
        Returns:
            client (TestClient): cliente con cookies propias
    '''
    return TestClient(app)


@pytest.fixture
def db_client(db):
    '''
    Cliente de la web que usa la conexion del test en vez de abrir una contra Neon.
        Returns:
            client (TestClient): cliente con cookies propias
    '''
    app.dependency_overrides[get_conn] = lambda: db

    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def start_login(client):
    '''
    Pasa por /login y devuelve el state que el servidor guardo en la sesion.
        Args:
            client (TestClient): cliente de la web
        Returns:
            state (str): state de la URL de autorizacion
    '''
    response = client.get('/login', follow_redirects=False)
    state = parse_qs(urlparse(response.headers['location']).query)['state'][0]
    return state


def test_health_is_ok(client):
    response = client.get('/health')

    assert response.status_code == 200
    assert response.text == 'ok'


def test_home_without_session_shows_login(db_client):
    response = db_client.get('/')

    assert 'Conectar con Spotify' in response.text
    assert 'htmx.org' in response.text


def test_login_redirects_to_spotify_authorize(client):
    response = client.get('/login', follow_redirects=False)

    assert response.status_code == 303
    assert response.headers['location'].startswith(f'{ACCOUNTS_URL}/authorize?')


def test_callback_with_wrong_state_is_rejected(client):
    start_login(client)

    response = client.get('/callback?code=c&state=otro', follow_redirects=False)

    assert response.headers['location'] == '/?error=vencido'


def test_callback_cancelled_by_user(client):
    state = start_login(client)

    response = client.get(f'/callback?error=access_denied&state={state}', follow_redirects=False)

    assert response.headers['location'] == '/?error=cancelado'


def test_callback_outside_allowlist_shows_alta(client, spotify):
    state = start_login(client)
    spotify.post(TOKEN_URL).respond(json={'access_token': 'a', 'refresh_token': 'r'})
    spotify.get(f'{API_URL}/me').respond(403)

    response = client.get(f'/callback?code=c&state={state}')

    assert 'Todavia no estas en la lista' in response.text
    assert 'email de tu cuenta de Spotify' in response.text


def test_callback_logs_in_and_shows_empty_dashboard(db_client, db, spotify):
    spotify_user_id = f'u_{uuid.uuid4().hex[:10]}'
    state = start_login(db_client)
    spotify.post(TOKEN_URL).respond(json={'access_token': 'a', 'refresh_token': 'r'})
    spotify.get(f'{API_URL}/me').respond(json={'id': spotify_user_id, 'display_name': 'Luca'})

    response = db_client.get(f'/callback?code=c&state={state}')
    row = db.execute('SELECT connection_mode, refresh_token_enc FROM users WHERE spotify_user_id = %s', (spotify_user_id,)).fetchone()

    assert 'Hola, Luca' in response.text
    assert 'Todavia no tenes Canales' in response.text
    assert row[0] == 'shared'
    assert row[1] != 'r'


def test_reconnection_shows_aviso_once(db_client, db, spotify):
    spotify_user_id = f'u_{uuid.uuid4().hex[:10]}'
    login = users.save_shared_login(db, spotify_user_id, 'viejo')
    users.mark_disconnected(db, login['user_id'])
    state = start_login(db_client)
    spotify.post(TOKEN_URL).respond(json={'access_token': 'a', 'refresh_token': 'r'})
    spotify.get(f'{API_URL}/me').respond(json={'id': spotify_user_id, 'display_name': 'Luca'})

    first = db_client.get(f'/callback?code=c&state={state}')
    second = db_client.get('/')

    assert 'Estuviste desconectado desde' in first.text
    assert 'Estuviste desconectado desde' not in second.text


def test_disconnected_user_with_live_session_sees_crit_aviso(db_client, db, spotify):
    spotify_user_id = f'u_{uuid.uuid4().hex[:10]}'
    state = start_login(db_client)
    spotify.post(TOKEN_URL).respond(json={'access_token': 'a', 'refresh_token': 'r'})
    spotify.get(f'{API_URL}/me').respond(json={'id': spotify_user_id})
    db_client.get(f'/callback?code=c&state={state}')
    user_id = db.execute('SELECT id FROM users WHERE spotify_user_id = %s', (spotify_user_id,)).fetchone()[0]
    users.mark_disconnected(db, user_id)

    response = db_client.get('/')

    assert 'Volver a conectar' in response.text


def test_logout_clears_session(db_client, db, spotify):
    state = start_login(db_client)
    spotify.post(TOKEN_URL).respond(json={'access_token': 'a', 'refresh_token': 'r'})
    spotify.get(f'{API_URL}/me').respond(json={'id': f'u_{uuid.uuid4().hex[:10]}', 'display_name': 'Luca'})
    db_client.get(f'/callback?code=c&state={state}')

    response = db_client.post('/logout')

    assert 'Conectar con Spotify' in response.text
