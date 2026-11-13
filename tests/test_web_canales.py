import uuid

import pytest
from fastapi.testclient import TestClient

from app import canales, web
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
    # la navegacion cambia solo el Panel: si Inicio no trae el CSS de la Vista Canal, al navegar las filas se desarman
    response = logged['client'].get('/')
    css = logged['client'].get(web.static_url('app.css'))

    assert f'href="{web.static_url("app.css")}"' in response.text
    assert '.pl-row' in css.text
    assert '.optlist' in css.text


def test_page_has_no_big_inline_css_or_js(logged):
    response = logged['client'].get('/')

    assert '.pl-row' not in response.text
    assert 'addEventListener' not in response.text
    assert f'<script src="{web.static_url("app.js")}" defer></script>' in response.text
    assert f'<script src="{web.static_url("htmx-2.0.4.min.js")}" defer></script>' in response.text
    assert 'unpkg.com' not in response.text


BOOSTED = {'HX-Request': 'true', 'HX-Boosted': 'true'}


def views(db, logged, spotify):
    '''
    Paths de las cuatro vistas del dashboard, con un Canal propio y la biblioteca mockeada.
        Returns:
            paths (list): Inicio, Canal, Ajustes y Nuevo Canal
    '''
    canal_id = canales.create_canal(db, logged['user_id'], 'mia', 'Trap', False)
    mock_library(spotify, logged['spotify_user_id'])
    paths = ['/', f'/canales/{canal_id}', f'/canales/{canal_id}/ajustes', '/canales/nuevo']
    return paths


def test_direct_entry_gets_the_whole_page(logged, db, spotify):
    for path in views(db, logged, spotify):
        response = logged['client'].get(path)

        assert response.text.startswith('<!doctype html>'), path
        assert 'data-n="Perfil"' in response.text, path
        assert '<div id="vista" class="vista">' in response.text, path


def test_boosted_click_gets_only_panel_and_sidebar(logged, db, spotify):
    for path in views(db, logged, spotify):
        response = logged['client'].get(path, headers=BOOSTED)

        assert '<html' not in response.text, path
        assert 'data-n="Perfil"' not in response.text, path
        assert '<title>Radar de Viernes</title>' in response.text, path
        assert 'data-n="Panel"' in response.text, path
        assert 'id="nav-inicio" hx-swap-oob="outerHTML"' in response.text, path
        assert 'id="lista-canales" hx-swap-oob="outerHTML"' in response.text, path
        assert 'HX-Boosted' in response.headers['vary'], path
        assert response.headers['cache-control'] == 'no-store', path


def test_boosted_click_marks_its_row_in_the_sidebar(logged, db, spotify):
    canal_id = canales.create_canal(db, logged['user_id'], 'mia', 'Trap', False)
    mock_library(spotify, logged['spotify_user_id'])

    canal = logged['client'].get(f'/canales/{canal_id}', headers=BOOSTED)
    home = logged['client'].get('/', headers=BOOSTED)

    assert 'class="nav-row on" href="/canales/' in canal.text
    assert 'class="nav-row on" href="/"' not in canal.text
    assert 'class="nav-row on" href="/"' in home.text
    assert 'id="canal-panel"' in canal.text
    # la vista abierta no se precarga: el mouse que quedo encima tras el click no vuelve a pedirla
    assert 'href="/" preload="mouseover"' in canal.text
    assert 'href="/" preload="mouseover"' not in home.text
    assert f'href="/canales/{canal_id}" preload="mouseover"' in home.text
    assert f'href="/canales/{canal_id}" preload="mouseover"' not in canal.text


def test_preloaded_view_is_cached_for_a_few_seconds(logged):
    response = logged['client'].get('/', headers=dict(BOOSTED, **{'HX-Preloaded': 'true'}))

    assert response.headers['cache-control'] == f'private, max-age={web.PRELOAD_AGE}'


def test_session_cookie_is_not_rewritten_on_every_click(logged):
    # una cookie nueva por respuesta rompe el Vary: Cookie y la vista precargada nunca se reusa
    page = logged['client'].get('/')
    click = logged['client'].get('/', headers=BOOSTED)

    assert 'set-cookie' not in page.headers
    assert 'set-cookie' not in click.headers


def test_back_without_snapshot_gets_the_whole_page(logged):
    # htmx pide la pagina para reemplazar el body cuando no tiene la vista guardada
    response = logged['client'].get('/', headers=dict(BOOSTED, **{'HX-History-Restore-Request': 'true'}))

    assert response.text.startswith('<!doctype html>')


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
