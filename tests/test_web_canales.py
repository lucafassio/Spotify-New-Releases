import uuid

import pytest
from fastapi.testclient import TestClient

from app import canales
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


def playlist(playlist_id, name, owner, collaborative=False):
    '''
    Playlist simplificada como la devuelve GET /me/playlists.
        Returns:
            playlist (dict): playlist de prueba
    '''
    return {'id': playlist_id, 'name': name, 'owner': {'id': owner, 'display_name': owner}, 'collaborative': collaborative, 'items': {'total': 10}}


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
    assert 'Columna Fuentes' in response.text
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


def test_three_sources_build_the_whitelist(logged, db, spotify):
    canal_id = canales.create_canal(db, logged['user_id'], 'mia', 'Trap', False)
    mock_library(spotify, logged['spotify_user_id'])
    spotify.get(f'{API_URL}/playlists/colab/items').respond(json={'items': [{'item': {'type': 'track', 'artists': [{'id': 'duki', 'name': 'Duki'}, {'id': 'ysy', 'name': 'YSY A'}]}}], 'next': None})
    spotify.get(f'{API_URL}/me/following').respond(json={'artists': {'items': [{'id': 'mora', 'name': 'Mora'}], 'next': None}})
    client = logged['client']

    client.post(f'/canales/{canal_id}/seeds', data={'playlist_id': 'colab', 'on': '1'})
    client.post(f'/canales/{canal_id}/follows', data={'on': '1'})
    response = client.post(f'/canales/{canal_id}/manual', data={'artist_id': 'bb', 'name': 'Bad Bunny'})

    whitelist = {row['name'] for row in canales.get_whitelist(db, canal_id)}
    assert whitelist == {'Duki', 'Mora', 'Bad Bunny'}
    for name in whitelist:
        assert name in response.text
    assert 'YSY A' not in response.text
    assert 'Spotify no deja usarla' in response.text


def test_feats_toggle_keeps_row_open(logged, db, spotify):
    canal_id = canales.create_canal(db, logged['user_id'], 'mia', 'Trap', False)
    canales.add_source(db, canal_id, 'seed_playlist', {'duki': {'name': 'Duki', 'feat': False}, 'ysy': {'name': 'YSY A', 'feat': True}}, 'colab')
    mock_library(spotify, logged['spotify_user_id'])

    response = logged['client'].post(f'/canales/{canal_id}/feats', data={'playlist_id': 'colab', 'on': '1', 'open': 'colab'})

    assert response.url.params['open'] == 'colab'
    assert 'Artistas de la playlist' in response.text
    assert {row['name'] for row in canales.get_whitelist(db, canal_id)} == {'Duki', 'YSY A'}


def test_search_hides_artists_already_added(logged, db, spotify):
    canal_id = canales.create_canal(db, logged['user_id'], 'mia', 'Trap', False)
    canales.add_manual_artist(db, canal_id, 'duki', 'Duki')
    spotify.get(f'{API_URL}/search').respond(json={'artists': {'items': [{'id': 'duki', 'name': 'Duki'}, {'id': 'dk', 'name': 'Duko'}]}})

    response = logged['client'].get(f'/canales/{canal_id}/buscar', params={'q': 'duk'})

    assert 'Duko' in response.text
    assert 'Duki' not in response.text


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
