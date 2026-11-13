from unittest.mock import MagicMock, Mock

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from app import canales, copias, web
from app import db as database
from app import spotify as spotify_api
from test_vista_canal import canal, entry, logged, read_once, urls  # noqa: F401


@pytest.fixture
def screen(monkeypatch, spotify):
    user = {'id': 91001, 'spotify_user_id': 'performance', 'disconnected_at': None}
    row = {'id': 1, 'user_id': user['id'], 'target_playlist_id': 'p0', 'target_name': 'Playlist 0', 'target_total': 250, 'target_created_by_bot': True, 'retencion_n': 1, 'guardado_filter': True, 'whitelist_count': 0, 'uses_seed': False}
    library = [{'id': f'p{i}', 'name': f'Playlist {i}', 'owner': {'id': user['spotify_user_id']}, 'items': {'total': 250}, 'snapshot_id': 'v1'} for i in range(100)]
    conn = Mock(autocommit=False)
    monkeypatch.setattr(canales, 'list_canales', lambda *args: [row.copy()])
    monkeypatch.setattr(canales, 'get_sources', lambda *args: [])
    monkeypatch.setattr(canales, 'get_whitelist', lambda *args: [])
    copies = []

    def save_library(conn, user_id, playlists):
        copies[:] = [{'playlist_id': p['id'], 'position': i, 'name': p['name'], 'owner_id': p['owner']['id'], 'collaborative': False, 'snapshot_id': p['snapshot_id'], 'total': 250, 'artists_snapshot_id': None, 'artists_total': None, 'last_track_uri': None} for i, p in enumerate(playlists)]

    monkeypatch.setattr(copias, 'save_library', save_library)
    monkeypatch.setattr(copias, 'list_copies', lambda *args: list(copies))
    monkeypatch.setattr(web, 'get_token', lambda *args: 'test-access')
    monkeypatch.setattr(web.users, 'get_user', lambda *args: user)
    web.app.dependency_overrides[web.get_conn] = lambda: conn
    web.app.dependency_overrides[web.require_user] = lambda: user

    if hasattr(web, 'library_cache'):
        web.library_cache.clear()
    if hasattr(spotify_api, 'clear_cooldowns'):
        spotify_api.clear_cooldowns()

    route = spotify.get(f'{spotify_api.API_URL}/me/playlists').respond(json={'items': library, 'next': None})
    # sin login de por medio la sesion no trae la foto de perfil y la pide una vez
    spotify.get(f'{spotify_api.API_URL}/me').respond(json={'id': user['spotify_user_id'], 'images': []})

    try:
        with TestClient(web.app) as client:
            yield {'client': client, 'conn': conn, 'library': library, 'route': route, 'user': user, 'canal': row}
    finally:
        web.app.dependency_overrides.clear()


def test_repeated_navigation_reuses_library(screen):
    screen['client'].get('/canales/1')
    screen['client'].get('/canales/1')

    assert screen['route'].call_count == 1


@pytest.mark.parametrize('action,data', [('guardado', {'on': '0'}), ('retencion', {'retencion': '4'})])
def test_rule_change_is_a_fragment_without_spotify(screen, action, data):
    response = screen['client'].post(f'/canales/1/{action}', data=data, headers={'HX-Request': 'true', 'HX-Target': 'canal-settings'})

    assert response.status_code == 200
    assert not response.history
    assert 'Editor de Retencion' in response.text
    assert 'Columna izquierda' not in response.text
    assert '<html' not in response.text
    assert screen['route'].call_count == 0


def test_rate_limit_does_not_sleep_in_web(screen, monkeypatch):
    screen['route'].respond(429, headers={'Retry-After': '30'}, json={'error': {'status': 429}})
    sleep = Mock()
    monkeypatch.setattr(spotify_api.time, 'sleep', sleep)

    response = screen['client'].get('/canales/1', headers={'HX-Request': 'true'}, follow_redirects=False)

    assert response.status_code == 200
    assert response.headers['HX-Retarget'] == '#action-notice'
    assert not sleep.called
    assert screen['route'].call_count == 1


def test_daily_quota_becomes_an_aviso(screen):
    screen['route'].respond(429, headers={'Retry-After': '86000'}, json={'error': {'reason': 'QUOTA_EXCEEDED'}})

    response = screen['client'].get('/canales/1', headers={'HX-Request': 'true'}, follow_redirects=False)

    assert response.status_code == 200
    assert 'cuota' in response.text.lower()


def test_background_api_keeps_retry_policy(monkeypatch, spotify):
    route = spotify.get(f'{spotify_api.API_URL}/me/playlists').mock(side_effect=[httpx.Response(429, headers={'Retry-After': '2'}), httpx.Response(200, json={'items': [], 'next': None})])
    sleep = Mock()
    monkeypatch.setattr(spotify_api.time, 'sleep', sleep)

    assert spotify_api.get_my_playlists('background-token') == []
    sleep.assert_called_once_with(2)
    assert route.call_count == 2


def test_cooldown_stops_repeat_clicks(screen):
    screen['route'].respond(429, headers={'Retry-After': '30'}, json={'error': {'status': 429}})
    headers = {'HX-Request': 'true'}
    screen['client'].get('/canales/1', headers=headers)
    response = screen['client'].get('/canales/1', headers=headers)

    assert screen['route'].call_count == 1
    assert response.headers['HX-Retarget'] == '#action-notice'
    assert int(response.headers['Retry-After']) > 0


def test_logout_invalidates_library(screen):
    web.library_cache.get((screen['user']['id'], 'library'), lambda: screen['library'])
    # la sesion real se firma como en SessionMiddleware, sin usar secretos del entorno
    import base64
    import json

    from itsdangerous import TimestampSigner

    data = base64.b64encode(json.dumps({'user_id': screen['user']['id']}).encode())
    cookie = TimestampSigner(web.get_env('SESSION_SECRET')).sign(data).decode()
    screen['client'].cookies.set('session', cookie)
    screen['client'].post('/logout', follow_redirects=False)

    assert not any(key[0] == screen['user']['id'] for key in web.library_cache.entries)


def test_expired_session_redirects_the_whole_page(screen):
    def require_login():
        raise web.LoginRequired()

    web.app.dependency_overrides[web.require_user] = require_login
    response = screen['client'].post('/canales/1/guardado', data={'on': '0'}, headers={'HX-Request': 'true'})

    assert response.headers['HX-Redirect'] == '/'
    assert 'Columna izquierda' not in response.text


def test_expired_deadline_stops_pagination_before_network(spotify, monkeypatch):
    route = spotify.get(f'{spotify_api.API_URL}/me/playlists').respond(json={'items': [], 'next': None})
    with spotify_api.request_context():
        monkeypatch.setattr(spotify_api.time, 'monotonic', lambda: float('inf'))
        with pytest.raises(httpx.TimeoutException):
            spotify_api.get_my_playlists('deadline-token')
    assert route.call_count == 0


def test_run_started_from_web_leaves_interactive_policy(monkeypatch):
    monkeypatch.setattr(web, 'connect', MagicMock())
    monkeypatch.setattr(web.users, 'get_user', lambda *args: {'id': 1})
    deadlines = []

    def run(*args):
        deadlines.append(spotify_api.request_deadline.get())
        return {'failures': []}

    monkeypatch.setattr(web.runs, 'run_user', run)
    with spotify_api.request_context():
        deadline = spotify_api.request_deadline.get()
        web.run_in_background(1)
        assert spotify_api.request_deadline.get() == deadline
    assert deadlines == [None]


# Idas y vueltas a la base por pantalla (#29)


@pytest.fixture
def round_trips(monkeypatch):
    '''
    Cuenta lo que cada request le manda a Postgres: consultas del cursor y comandos sueltos (BEGIN, COMMIT, ROLLBACK).
        Returns:
            sent (list): una entrada por ida y vuelta, con la consulta o el comando
    '''
    sent = []
    execute, executemany, command = psycopg.Cursor.execute, psycopg.Cursor.executemany, psycopg.Connection._exec_command

    def count_execute(self, query, *args, **kwargs):
        sent.append(str(query)[:80])
        return execute(self, query, *args, **kwargs)

    def count_executemany(self, query, *args, **kwargs):
        sent.append(str(query)[:80])
        return executemany(self, query, *args, **kwargs)

    def count_command(self, text, *args, **kwargs):
        sent.append(bytes(text).decode() if isinstance(text, (bytes, bytearray, memoryview)) else str(text))
        return command(self, text, *args, **kwargs)

    monkeypatch.setattr(psycopg.Cursor, 'execute', count_execute)
    monkeypatch.setattr(psycopg.Cursor, 'executemany', count_executemany)
    monkeypatch.setattr(psycopg.Connection, '_exec_command', count_command)
    return sent


def measure(sent, call, overhead=False):
    '''
    Corre un request y devuelve lo que le mando a la base.
        Args:
            sent (list): lista del fixture round_trips
            call (callable): hace el request
            overhead (bool): True cuenta tambien BEGIN y COMMIT
        Returns:
            texts (list): consultas, y comandos si overhead
    '''
    sent.clear()
    response = call()
    assert response.status_code == 200
    return list(sent) if overhead else [text for text in sent if text not in ('BEGIN', 'COMMIT')]


# topes de consultas por pantalla, sin BEGIN ni COMMIT; antes de #29 eran 2, 6, 8, 7, 5, 5, 12 y 9
SCREEN_QUERIES = {
    'inicio': 1,
    'canal': 3,
    'canal_open': 4,
    'fila': 3,
    'refresco_igual': 3,
    'ajustes': 3,
    'sumar': 5,
    'quitar': 5,
}


def test_screens_keep_their_round_trips(canal, round_trips, spotify):  # noqa: F811
    read_once(canal, spotify, [entry('t1', 'Duki'), entry('t2', 'Mora')])
    client, base = canal['client'], f'/canales/{canal["canal_id"]}'
    fila, refresco = urls(canal)
    hx = {'HX-Request': 'true'}
    screens = {
        'inicio': lambda: client.get('/'),
        'canal': lambda: client.get(base),
        'canal_open': lambda: client.get(base, params={'open': canal['pid']}),
        'fila': lambda: client.get(fila, headers=hx),
        'refresco_igual': lambda: client.get(refresco, headers=hx),
        'ajustes': lambda: client.get(f'{base}/ajustes'),
        'sumar': lambda: client.post(f'{base}/artistas/sumar', data={'playlist_id': canal['pid'], 'artist_id': 'duki'}, headers=hx),
        'quitar': lambda: client.post(f'{base}/artistas/quitar', data={'artist_id': 'duki', 'open': canal['pid']}, headers=hx),
    }

    counts = {name: measure(round_trips, call) for name, call in screens.items()}

    assert {name: len(queries) for name, queries in counts.items()} == SCREEN_QUERIES, counts


def test_read_only_screens_skip_the_transaction_with_the_real_pool(canal, db, round_trips, spotify, db_schema, monkeypatch):  # noqa: F811
    read_once(canal, spotify, [entry('t1', 'Duki'), entry('t2', 'Mora')])
    # get_conn commitea al final de cada request; la conexion del test que lo reemplaza no
    db.commit()
    client, base = canal['client'], f'/canales/{canal["canal_id"]}'
    fila = urls(canal)[0]
    hx = {'HX-Request': 'true'}
    # el pool de verdad ve lo commiteado desde el schema efimero
    web.app.dependency_overrides.pop(web.get_conn)
    monkeypatch.setattr(database, 'WEB_OPTIONS', f'{database.WEB_OPTIONS} -c search_path={db_schema}')
    database.close_web_pool()

    try:
        client.get(base)
        screens = {
            'canal': lambda: client.get(base),
            'canal_open': lambda: client.get(base, params={'open': canal['pid']}),
            'fila': lambda: client.get(fila, headers=hx),
            'sumar': lambda: client.post(f'{base}/artistas/sumar', data={'playlist_id': canal['pid'], 'artist_id': 'duki'}, headers=hx),
        }
        sent = {name: measure(round_trips, call, overhead=True) for name, call in screens.items()}
    finally:
        database.close_web_pool()

    # con la conexion recien usada no hay chequeo del pool, y una vista que solo lee no paga BEGIN ni COMMIT
    assert {name: len(texts) for name, texts in sent.items()} == {'canal': 3, 'canal_open': 4, 'fila': 3, 'sumar': 7}, sent
    assert [sent['sumar'].count('BEGIN'), sent['sumar'].count('COMMIT')] == [1, 1]
    assert not any(text in ('BEGIN', 'COMMIT', '') for name in ('canal', 'canal_open', 'fila') for text in sent[name])
