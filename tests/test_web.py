import base64
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


def basic_auth(client_id, client_secret):
    '''
    Header Authorization que manda httpx al token endpoint con un client.
        Args:
            client_id (str): client id
            client_secret (str): client secret
        Returns:
            header (str): Basic con id:secret en base64
    '''
    return 'Basic ' + base64.b64encode(f'{client_id}:{client_secret}'.encode()).decode()


def connect_own_app(client, spotify, spotify_user_id, client_id):
    '''
    Pasa por el Formulario de claves y el callback con una Own App valida.
        Args:
            client (TestClient): cliente de la web
            spotify (respx.Router): router de Spotify
            spotify_user_id (str): cuenta que autoriza
            client_id (str): client id de la Own App
        Returns:
            route (respx.Route): ruta del token endpoint, para mirar con que client se llamo
    '''
    route = spotify.post(TOKEN_URL).respond(json={'access_token': 'a', 'refresh_token': 'r'})
    spotify.get(f'{API_URL}/me').respond(json={'id': spotify_user_id, 'display_name': 'Amigo'})
    response = client.post('/own-app', data={'client_id': client_id, 'client_secret': 'own-secret'}, follow_redirects=False)
    state = parse_qs(urlparse(response.headers['location']).query)['state'][0]
    client.get(f'/callback?code=c&state={state}')
    return route


def test_alta_shows_own_app_steps_with_redirect(client):
    response = client.get('/alta')

    assert 'Todavia no estas en la lista' in response.text
    assert 'http://127.0.0.1:8000/callback' in response.text
    assert 'name="client_secret"' in response.text
    assert 'Spotify Premium' in response.text


def test_own_app_form_requires_both_keys(client):
    response = client.post('/own-app', data={'client_id': 'abc', 'client_secret': ''})

    assert 'Pega el Client ID y el Client secret' in response.text
    assert 'value="abc"' in response.text


def test_own_app_form_rejects_wrong_keys(client, spotify):
    spotify.post(TOKEN_URL).respond(400, json={'error': 'invalid_client'})

    response = client.post('/own-app', data={'client_id': 'abc', 'client_secret': 'mal'})

    assert 'Spotify no reconoce ese Client ID' in response.text


def test_own_app_form_authorizes_with_own_client(client, spotify):
    route = spotify.post(TOKEN_URL).respond(json={'access_token': 'a'})

    response = client.post('/own-app', data={'client_id': 'own-cid', 'client_secret': 'own-secret'}, follow_redirects=False)
    query = parse_qs(urlparse(response.headers['location']).query)

    assert response.headers['location'].startswith(f'{ACCOUNTS_URL}/authorize?')
    assert query['client_id'] == ['own-cid']
    assert query['redirect_uri'] == ['http://127.0.0.1:8000/callback']
    assert route.calls.last.request.headers['Authorization'] == basic_auth('own-cid', 'own-secret')


def test_own_app_callback_saves_user_with_own_client(db_client, db, spotify):
    spotify_user_id = f'u_{uuid.uuid4().hex[:10]}'
    client_id = f'cid_{uuid.uuid4().hex[:10]}'

    route = connect_own_app(db_client, spotify, spotify_user_id, client_id)
    user_id = db.execute('SELECT id FROM users WHERE spotify_user_id = %s', (spotify_user_id,)).fetchone()[0]
    user = users.get_user(db, user_id)

    assert route.calls.last.request.headers['Authorization'] == basic_auth(client_id, 'own-secret')
    assert user['connection_mode'] == 'own'
    assert user['client_id'] == client_id
    assert user['client_secret_enc'] != 'own-secret'
    assert db_client.cookies.get('own_app') == client_id


def test_own_app_user_returns_by_cookie(db_client, db, spotify):
    client_id = f'cid_{uuid.uuid4().hex[:10]}'
    connect_own_app(db_client, spotify, f'u_{uuid.uuid4().hex[:10]}', client_id)
    db_client.post('/logout')

    response = db_client.get('/login', follow_redirects=False)

    assert parse_qs(urlparse(response.headers['location']).query)['client_id'] == [client_id]


def test_own_app_with_dead_keys_asks_for_new_ones(db_client, db, spotify):
    client_id = f'cid_{uuid.uuid4().hex[:10]}'
    connect_own_app(db_client, spotify, f'u_{uuid.uuid4().hex[:10]}', client_id)
    spotify.post(TOKEN_URL).respond(400, json={'error': 'invalid_client'})

    response = db_client.get('/login')

    assert 'Tu app de Spotify dejo de andar' in response.text


def test_own_app_outside_its_user_management(client, spotify):
    spotify.post(TOKEN_URL).respond(json={'access_token': 'a', 'refresh_token': 'r'})
    spotify.get(f'{API_URL}/me').respond(403)
    response = client.post('/own-app', data={'client_id': 'own-cid', 'client_secret': 'own-secret'}, follow_redirects=False)
    state = parse_qs(urlparse(response.headers['location']).query)['state'][0]

    response = client.get(f'/callback?code=c&state={state}')

    assert 'Revisa que tu email de Spotify este en User Management' in response.text
    assert 'value="own-cid"' in response.text
