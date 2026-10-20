from unittest.mock import MagicMock, Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from app import canales, web
from app import spotify as spotify_api


@pytest.fixture
def screen(monkeypatch, spotify):
    user = {'id': 91001, 'spotify_user_id': 'performance', 'disconnected_at': None}
    canal = {'id': 1, 'user_id': user['id'], 'target_playlist_id': 'p0', 'target_name': 'Playlist 0', 'target_total': 250, 'target_created_by_bot': True, 'retencion_n': 1, 'guardado_filter': True, 'whitelist_count': 0}
    library = [{'id': f'p{i}', 'name': f'Playlist {i}', 'owner': {'id': user['spotify_user_id']}, 'items': {'total': 250}, 'snapshot_id': 'v1'} for i in range(100)]
    conn = Mock()
    monkeypatch.setattr(canales, 'get_canal', lambda *args: canal.copy())
    monkeypatch.setattr(canales, 'list_canales', lambda *args: [canal.copy()])
    monkeypatch.setattr(canales, 'get_sources', lambda *args: [])
    monkeypatch.setattr(canales, 'get_whitelist', lambda *args: [])
    monkeypatch.setattr(web, 'get_token', lambda *args: 'test-access')
    monkeypatch.setattr(web.users, 'get_user', lambda *args: user)
    web.app.dependency_overrides[web.get_conn] = lambda: conn
    web.app.dependency_overrides[web.require_user] = lambda: user

    if hasattr(web, 'library_cache'):
        web.library_cache.clear()
    if hasattr(spotify_api, 'clear_cooldowns'):
        spotify_api.clear_cooldowns()

    route = spotify.get(f'{spotify_api.API_URL}/me/playlists').respond(json={'items': library, 'next': None})

    try:
        with TestClient(web.app) as client:
            yield {'client': client, 'conn': conn, 'library': library, 'route': route, 'user': user, 'canal': canal}
    finally:
        web.app.dependency_overrides.clear()


def test_open_canal_does_not_write_once_per_playlist(screen):
    response = screen['client'].get('/canales/1')

    assert response.status_code == 200
    assert screen['conn'].execute.call_count <= 5


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
    assert 'Columna Fuentes' not in response.text
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


def test_preview_is_reused_and_does_not_reload_the_page(screen, spotify):
    tracks = spotify.get(f'{spotify_api.API_URL}/playlists/p1/items').respond(json={'items': [{'item': {'type': 'track', 'artists': [{'id': 'a1', 'name': 'Artista'}]}}], 'next': None})
    response = screen['client'].get('/canales/1/playlists/p1')
    screen['client'].get('/canales/1/playlists/p1')

    assert 'Artistas de la playlist' in response.text
    assert 'Columna Fuentes' not in response.text
    assert tracks.call_count == 1
    assert screen['route'].call_count == 1


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
    assert 'Columna Fuentes' not in response.text


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
