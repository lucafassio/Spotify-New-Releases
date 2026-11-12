import uuid

import pytest
from fastapi.testclient import TestClient

from app import canales, web, web_cache
from app import spotify as spotify_api
from app.spotify import ACCOUNTS_URL, API_URL
from app.web import app, get_conn
from test_web import start_login

TOKEN_URL = f'{ACCOUNTS_URL}/api/token'


@pytest.fixture
def logged(db, spotify):
    '''
    Cliente de la web con un User logueado por la Shared App y Spotify mockeado.
        Returns:
            session (dict): client, user_id y spotify_user_id
    '''
    app.dependency_overrides[get_conn] = lambda: db
    client = TestClient(app)
    spotify_user_id = f'u_{uuid.uuid4().hex[:10]}'
    spotify.post(TOKEN_URL).respond(json={'access_token': 'a', 'refresh_token': 'r'})
    spotify.get(f'{API_URL}/me').respond(json={'id': spotify_user_id, 'display_name': 'Luca'})
    state = start_login(client)
    client.get(f'/callback?code=c&state={state}')
    user_id = db.execute('SELECT id FROM users WHERE spotify_user_id = %s', (spotify_user_id,)).fetchone()[0]

    try:
        yield {'client': client, 'user_id': user_id, 'spotify_user_id': spotify_user_id}
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def cooldowns():
    '''
    Descarta las pausas locales de call_api: todos los tests usan el mismo access token.
    '''
    spotify_api.clear_cooldowns()
    try:
        yield
    finally:
        spotify_api.clear_cooldowns()


def playlist(playlist_id, name, owner, collaborative=False, snapshot='v1', total=10):
    '''
    Playlist simplificada como la devuelve GET /me/playlists.
        Returns:
            playlist (dict): playlist de prueba
    '''
    return {'id': playlist_id, 'name': name, 'owner': {'id': owner, 'display_name': owner}, 'collaborative': collaborative, 'snapshot_id': snapshot, 'items': {'total': total}}


def mock_library(spotify, owner):
    '''
    Biblioteca de prueba: una playlist propia, una colaborativa ajena y una ajena.
        Returns:
            route (respx.Route): ruta de GET /me/playlists
    '''
    return spotify.get(f'{API_URL}/me/playlists').respond(json={
        'items': [playlist('mia', 'Trap', owner), playlist('colab', 'Juntada', 'otro', True), playlist('ajena', 'La vibra', 'otro')],
        'next': None,
    })


def test_create_canal_with_new_playlist(logged, db, spotify):
    created = spotify.post(f'{API_URL}/me/playlists').respond(201, json={'id': 'nueva', 'name': 'Radar'})
    mock_library(spotify, logged['spotify_user_id'])

    response = logged['client'].post('/canales', data={'tipo': 'nueva', 'name': 'Radar'})
    canal = canales.list_canales(db, logged['user_id'])[0]

    assert created.calls.last.request.read() == b'{"name":"Radar","public":false,"description":"Lo nuevo de tus artistas, cada viernes. Radar de Viernes."}'
    assert canal['target_playlist_id'] == 'nueva'
    assert canal['target_created_by_bot'] is True
    assert 'Columna izquierda' in response.text
    assert 'Canales &middot; 1/5' in response.text


def test_create_canal_with_own_playlist(logged, db, spotify):
    mock_library(spotify, logged['spotify_user_id'])

    logged['client'].post('/canales', data={'tipo': 'mia', 'playlist_id': 'mia'})
    canal = canales.list_canales(db, logged['user_id'])[0]

    assert canal['target_playlist_id'] == 'mia'
    assert canal['target_name'] == 'Trap'
    assert canal['target_created_by_bot'] is False


def test_cannot_target_a_playlist_of_someone_else(logged, db, spotify):
    mock_library(spotify, logged['spotify_user_id'])

    response = logged['client'].post('/canales', data={'tipo': 'mia', 'playlist_id': 'colab'})

    assert 'No encontramos esa playlist entre las tuyas' in response.text
    assert canales.list_canales(db, logged['user_id']) == []


def test_sixth_canal_is_rejected_before_creating_playlist(logged, db, spotify):
    for n in range(canales.MAX_CANALES):
        canales.create_canal(db, logged['user_id'], f'p{n}', f'P{n}', True)
    created = spotify.post(f'{API_URL}/me/playlists').respond(201, json={'id': 'nueva', 'name': 'Radar'})

    response = logged['client'].post('/canales', data={'tipo': 'nueva', 'name': 'Radar'})

    assert 'que es el maximo' in response.text
    assert not created.called


def test_search_dims_artists_already_added(logged, db, spotify):
    canal_id = canales.create_canal(db, logged['user_id'], 'mia', 'Trap', False)
    canales.add_manual_artist(db, canal_id, 'duki', 'Duki')
    spotify.get(f'{API_URL}/search').respond(json={'artists': {'items': [{'id': 'duki', 'name': 'Duki'}, {'id': 'dk', 'name': 'Duko'}]}})

    response = logged['client'].get(f'/canales/{canal_id}/buscar', params={'q': 'duk'})

    assert 'name="artist_id" value="dk"' in response.text
    assert 'name="artist_id" value="duki"' not in response.text
    assert 'id="res-duki" class="res added"' in response.text


def test_search_repeats_text_from_cache_but_recomputes_added(logged, db, spotify):
    canal_id = canales.create_canal(db, logged['user_id'], 'mia', 'Trap', False)
    found = spotify.get(f'{API_URL}/search').respond(json={'artists': {'items': [{'id': 'duki', 'name': 'Duki'}]}})

    first = logged['client'].get(f'/canales/{canal_id}/buscar', params={'q': 'Duki'})
    canales.add_manual_artist(db, canal_id, 'duki', 'Duki')
    # mismo texto con otras mayusculas y espacios: sale del cache
    second = logged['client'].get(f'/canales/{canal_id}/buscar', params={'q': '  duKI '})

    assert found.call_count == 1
    assert found.calls.last.request.url.params['q'] == 'duki'
    assert 'name="artist_id" value="duki"' in first.text
    assert 'id="res-duki" class="res added"' in second.text


def test_search_cache_is_per_user_and_expires(logged, db, spotify, monkeypatch):
    canal_id = canales.create_canal(db, logged['user_id'], 'mia', 'Trap', False)
    found = spotify.get(f'{API_URL}/search').respond(json={'artists': {'items': [{'id': 'duki', 'name': 'Duki'}]}})
    now = web_cache.time.monotonic()
    monkeypatch.setattr(web_cache.time, 'monotonic', lambda: now)

    logged['client'].get(f'/canales/{canal_id}/buscar', params={'q': 'duki'})
    monkeypatch.setattr(web_cache.time, 'monotonic', lambda: now + web.SEARCH_TTL - 1)
    logged['client'].get(f'/canales/{canal_id}/buscar', params={'q': 'duki'})
    assert found.call_count == 1

    monkeypatch.setattr(web_cache.time, 'monotonic', lambda: now + web.SEARCH_TTL + 1)
    logged['client'].get(f'/canales/{canal_id}/buscar', params={'q': 'duki'})
    assert found.call_count == 2

    # reconectar limpia lo que busco ese User
    web.search_cache.clear(logged['user_id'])
    logged['client'].get(f'/canales/{canal_id}/buscar', params={'q': 'duki'})
    assert found.call_count == 3


def test_search_needs_two_characters(logged, db, spotify):
    canal_id = canales.create_canal(db, logged['user_id'], 'mia', 'Trap', False)
    found = spotify.get(f'{API_URL}/search').respond(json={'artists': {'items': [{'id': 'duki', 'name': 'Duki'}]}})

    for query in ('', 'd', '  d  '):
        response = logged['client'].get(f'/canales/{canal_id}/buscar', params={'q': query})
        assert response.status_code == 200
        assert response.text.strip() == ''

    assert not found.called


def test_search_short_text_still_checks_the_canal(logged, spotify):
    response = logged['client'].get('/canales/999999999/buscar', params={'q': 'd'}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers['location'] == '/'


def test_search_rate_limit_shows_its_aviso_in_the_results(logged, db, spotify, cooldowns):
    canal_id = canales.create_canal(db, logged['user_id'], 'mia', 'Trap', False)
    limited = spotify.get(f'{API_URL}/search').respond(429, headers={'Retry-After': '30'})

    response = logged['client'].get(f'/canales/{canal_id}/buscar', params={'q': 'duki'}, headers={'HX-Request': 'true'})
    # durante la pausa el cooldown de call_api corta antes de llamar a Spotify
    again = logged['client'].get(f'/canales/{canal_id}/buscar', params={'q': 'bad bunny'}, headers={'HX-Request': 'true'})

    assert response.status_code == 200
    assert 'HX-Retarget' not in response.headers
    assert response.headers['Retry-After'] == '30'
    assert 'Spotify pidio una pausa' in response.text
    assert 'data-n="Aviso de busqueda"' in response.text
    assert 'Spotify pidio una pausa' in again.text
    assert limited.call_count == 1


def test_search_quota_exceeded_shows_its_aviso(logged, db, spotify, cooldowns):
    canal_id = canales.create_canal(db, logged['user_id'], 'mia', 'Trap', False)
    spotify.get(f'{API_URL}/search').respond(429, headers={'Retry-After': '86400'}, json={'error': {'status': 429, 'reason': 'QUOTA_EXCEEDED'}})

    response = logged['client'].get(f'/canales/{canal_id}/buscar', params={'q': 'duki'}, headers={'HX-Request': 'true'})

    assert response.status_code == 200
    assert 'cuota de consultas' in response.text


def test_whitelist_ids_are_only_the_ids(logged, db):
    canal_id = canales.create_canal(db, logged['user_id'], 'mia', 'Trap', False)
    canales.add_manual_artist(db, canal_id, 'duki', 'Duki')
    canales.add_manual_artist(db, canal_id, 'bb', 'Bad Bunny')

    assert canales.get_whitelist_ids(db, canal_id) == {'duki', 'bb'}


def test_retencion_and_guardado_are_saved(logged, db, spotify):
    canal_id = canales.create_canal(db, logged['user_id'], 'nueva', 'Radar', True)
    mock_library(spotify, logged['spotify_user_id'])

    logged['client'].post(f'/canales/{canal_id}/retencion', data={'retencion': '4'})
    response = logged['client'].post(f'/canales/{canal_id}/guardado', data={'on': '0'})
    canal = canales.get_canal(db, logged['user_id'], canal_id)

    assert canal['retencion_n'] == 4
    assert canal['guardado_filter'] is False
    assert 'guarda 4 semanas' in response.text


def test_unlink_with_playlist_deletes_both(logged, db, spotify):
    canal_id = canales.create_canal(db, logged['user_id'], 'nueva', 'Radar', True)
    removed = spotify.delete(f'{API_URL}/me/library').respond(200)

    response = logged['client'].post(f'/canales/{canal_id}/desvincular', data={'borrar_playlist': '1'})

    assert removed.called
    assert canales.list_canales(db, logged['user_id']) == []
    assert 'Borramos &#34;Radar&#34; y su playlist.' in response.text


def test_canal_of_another_user_is_not_shown(logged, db):
    other = db.execute("INSERT INTO users (spotify_user_id, connection_mode) VALUES (%s, 'shared') RETURNING id", (f'u_{uuid.uuid4().hex[:10]}',)).fetchone()[0]
    canal_id = canales.create_canal(db, other, 'x', 'Ajena', True)

    response = logged['client'].post(f'/canales/{canal_id}/desvincular', data={'borrar_playlist': '0'}, follow_redirects=False)

    assert response.headers['location'] == '/'
    assert canales.get_canal(db, other, canal_id) is not None


def test_spotify_failure_shows_aviso_instead_of_500(logged, db, spotify):
    canal_id = canales.create_canal(db, logged['user_id'], 'mia', 'Trap', False)
    spotify.get(f'{API_URL}/me/playlists').respond(503)

    response = logged['client'].get(f'/canales/{canal_id}')

    assert response.status_code == 200
    assert 'Spotify no respondio bien' in response.text


def test_every_view_carries_the_css_of_all_views(logged):
    # hx-boost cambia solo el body: si Inicio no trae el CSS de la Vista Canal, al navegar las filas se desarman
    response = logged['client'].get('/')

    assert '.pl-row' in response.text
    assert '.optlist' in response.text


def test_run_now_queues_the_run(logged, db, monkeypatch):
    queued = []
    monkeypatch.setattr(web, 'run_in_background', queued.append)
    canales.create_canal(db, logged['user_id'], 'p1', 'Radar', True)

    home = logged['client'].get('/')
    response = logged['client'].post('/correr')

    assert 'Buscar novedades ahora' in home.text
    assert queued == [logged['user_id']]
    assert 'Estamos buscando novedades' in response.text


def test_run_now_button_needs_a_canal(logged):
    assert 'Buscar novedades ahora' not in logged['client'].get('/').text
