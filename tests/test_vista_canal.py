import uuid
from contextlib import nullcontext
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import canales, deezer, web
from app import spotify as spotify_api
from app.deezer import API_URL as DEEZER_URL
from app.spotify import API_URL
from app.web import get_conn
from test_web import start_login
from test_web_canales import TOKEN_URL, logged, playlist  # noqa: F401


def uid(prefix):
    '''
    Id unico: los tests comparten schema y las rutas de respx son por id de playlist.
        Returns:
            value (str): prefijo con sufijo al azar
    '''
    return f'{prefix}{uuid.uuid4().hex[:10]}'


def entry(uri, *artists, isrc=None):
    '''
    Item de GET /playlists/{id}/items: el primer artista es principal, el resto invitados.
        Returns:
            entry (dict): item con su track
    '''
    track = {'type': 'track', 'is_local': False, 'uri': f'spotify:track:{uri}', 'artists': [{'id': a.lower(), 'name': a} for a in artists]}
    if isrc:
        track['external_ids'] = {'isrc': isrc}
    return {'item': track}


def library(spotify, *playlists):
    '''
    Mockea GET /me/playlists y descarta la biblioteca cacheada, como si pasara el minuto del WebCache.
        Returns:
            route (respx.Route): ruta de la biblioteca
    '''
    web.library_cache.clear()
    return spotify.get(f'{API_URL}/me/playlists').respond(json={'items': list(playlists), 'next': None})


def items(spotify, playlist_id, entries):
    '''
    Mockea los items de una playlist.
        Returns:
            route (respx.Route): ruta de los items
    '''
    route = spotify.get(f'{API_URL}/playlists/{playlist_id}/items')
    route.respond(json={'items': entries, 'next': None})
    return route


@pytest.fixture
def canal(logged, db, spotify):  # noqa: F811
    '''
    Canal en Lista manual con una playlist propia ya en la biblioteca.
        Returns:
            setup (dict): client, canal_id, owner, pid y la playlist de prueba
    '''
    pid = uid('p')
    owner = logged['spotify_user_id']
    target = uid('t')
    canal_id = canales.create_canal(db, logged['user_id'], target, 'Radar', True)
    db.commit()
    return {'client': logged['client'], 'canal_id': canal_id, 'owner': owner, 'pid': pid, 'target': target, 'user_id': logged['user_id']}


def urls(canal):
    '''
    Paths de una fila de playlist del Canal.
        Returns:
            paths (tuple): fragmento al abrir y refresco por detras
    '''
    base = f'/canales/{canal["canal_id"]}/playlists/{canal["pid"]}'
    return base, f'{base}/refrescar'


def read_once(canal, spotify, entries, **kwargs):
    '''
    Deja la Copia de playlist leida: primera apertura con su refresco.
        Returns:
            route (respx.Route): ruta de los items, con una llamada hecha
    '''
    target = playlist(canal['pid'], 'Trap', canal['owner'], total=len(entries), **kwargs)
    library(spotify, target, playlist(canal['target'], 'Radar', canal['owner']))
    route = items(spotify, canal['pid'], entries)
    canal['client'].get(f'/canales/{canal["canal_id"]}')
    canal['client'].get(urls(canal)[1])
    return route


# Lista manual


def test_new_canal_starts_empty_in_lista_manual(canal, spotify):
    library(spotify, playlist(canal['pid'], 'Trap', canal['owner']))

    response = canal['client'].get(f'/canales/{canal["canal_id"]}')

    assert 'Elegi artistas desde una playlist o buscalos.' in response.text
    assert 'data-n="Buscador de artistas"' in response.text
    assert 'data-n="Aviso de Seed"' not in response.text
    assert 'Interruptor de playlist' not in response.text
    assert 'Artistas que seguis' not in response.text


def test_first_open_shows_loading_and_reads_behind(canal, spotify):
    library(spotify, playlist(canal['pid'], 'Trap', canal['owner'], total=2))
    route = items(spotify, canal['pid'], [entry('t1', 'Zoe', 'Mora'), entry('t2', 'Ángel')])
    canal['client'].get(f'/canales/{canal["canal_id"]}')
    fragment_url, refresh_url = urls(canal)

    fragment = canal['client'].get(fragment_url)

    assert 'Cargando artistas' in fragment.text
    assert 'Buscando...' in fragment.text
    assert f'hx-get="{refresh_url}"' in fragment.text
    assert 'hx-trigger="load"' in fragment.text
    assert route.call_count == 0

    refreshed = canal['client'].get(refresh_url)

    assert route.call_count == 1
    assert 'Al dia' in refreshed.text
    assert refreshed.text.index('Ángel') < refreshed.text.index('Mora') < refreshed.text.index('Zoe')
    assert 'artistas nuevos' not in refreshed.text


def test_copy_shows_at_once_and_survives_the_cache(canal, spotify):
    route = read_once(canal, spotify, [entry('t1', 'Duki')])
    web.library_cache.clear()

    fragment = canal['client'].get(urls(canal)[0])

    assert 'Duki' in fragment.text
    assert 'Cargando artistas' not in fragment.text
    assert route.call_count == 1


def test_arrow_moves_artist_without_spotify_and_keeps_isrc(canal, db, spotify):
    read_once(canal, spotify, [entry('t1', 'Duki', 'YSY', isrc='AR1'), entry('t2', 'Mora')])
    calls = spotify.calls.call_count

    response = canal['client'].post(f'/canales/{canal["canal_id"]}/artistas/sumar', data={'playlist_id': canal['pid'], 'artist_id': 'ysy'}, headers={'HX-Request': 'true'})

    assert spotify.calls.call_count == calls
    assert [row['name'] for row in canales.get_whitelist(db, canal['canal_id'])] == ['YSY']
    assert db.execute('SELECT sa.isrc, sa.feat FROM source_artists sa JOIN artist_sources s ON s.id = sa.artist_source_id WHERE s.canal_id = %s', (canal['canal_id'],)).fetchone() == ('AR1', False)
    assert '<html' not in response.text
    assert 'id="canal-panel"' not in response.text
    assert f'id="art-{canal["pid"]}-ysy" hx-swap-oob="delete"' in response.text
    assert 'hx-swap-oob="beforeend:#whitelist-filas"' in response.text
    assert 'id="wl-ysy"' in response.text
    assert 'id="side-list"' in response.text
    assert '1 artistas' in response.text


def test_chosen_artist_leaves_the_list_until_ya_estan_todos(canal, spotify):
    read_once(canal, spotify, [entry('t1', 'Duki'), entry('t2', 'Mora')])
    client = canal['client']
    add = f'/canales/{canal["canal_id"]}/artistas/sumar'

    client.post(add, data={'playlist_id': canal['pid'], 'artist_id': 'duki'}, headers={'HX-Request': 'true'})
    fragment = client.get(urls(canal)[0])
    last = client.post(add, data={'playlist_id': canal['pid'], 'artist_id': 'mora'}, headers={'HX-Request': 'true'})

    assert 'Duki' not in fragment.text
    assert 'Mora' in fragment.text
    assert 'Ya estan todos' in last.text
    assert 'Ya estan todos' in client.get(urls(canal)[0]).text


def test_removing_an_artist_returns_it_to_the_open_playlist(canal, db, spotify):
    read_once(canal, spotify, [entry('t1', 'Duki'), entry('t2', 'Mora')])
    client = canal['client']
    client.post(f'/canales/{canal["canal_id"]}/artistas/sumar', data={'playlist_id': canal['pid'], 'artist_id': 'duki'}, headers={'HX-Request': 'true'})

    back = client.post(f'/canales/{canal["canal_id"]}/artistas/quitar', data={'artist_id': 'duki', 'open': canal['pid']}, headers={'HX-Request': 'true'})

    assert canales.get_whitelist(db, canal['canal_id']) == []
    assert 'id="wl-duki" hx-swap-oob="delete"' in back.text
    assert f'hx-swap-oob="beforeend:#artistas-{canal["pid"]}"' in back.text
    assert 'data-sort="duki"' in back.text
    assert 'Elegi artistas desde una playlist o buscalos.' in back.text


def test_removing_an_artist_not_in_the_open_playlist_adds_no_row(canal, spotify):
    read_once(canal, spotify, [entry('t1', 'Mora')])
    client = canal['client']
    client.post(f'/canales/{canal["canal_id"]}/manual', data={'artist_id': 'bb', 'name': 'Bad Bunny'}, headers={'HX-Request': 'true'})

    back = client.post(f'/canales/{canal["canal_id"]}/artistas/quitar', data={'artist_id': 'bb', 'open': canal['pid']}, headers={'HX-Request': 'true'})

    assert 'beforeend:#artistas' not in back.text


def test_whitelist_is_alphabetical_with_inverse_arrows(canal, db, spotify):
    library(spotify, playlist(canal['pid'], 'Trap', canal['owner']))
    canales.add_manual_artist(db, canal['canal_id'], 'z', 'Zoe')
    canales.add_manual_artist(db, canal['canal_id'], 'a', 'Ángel')
    canales.add_manual_artist(db, canal['canal_id'], 'm', 'mora')
    db.commit()

    text = canal['client'].get(f'/canales/{canal["canal_id"]}').text

    assert text.index('id="wl-a"') < text.index('id="wl-m"') < text.index('id="wl-z"')
    assert f'action="/canales/{canal["canal_id"]}/artistas/quitar"' in text


def test_buscador_adds_by_name_without_isrc(canal, db, spotify):
    read_once(canal, spotify, [entry('t1', 'Duki', isrc='AR1')])

    response = canal['client'].post(f'/canales/{canal["canal_id"]}/manual', data={'artist_id': 'duki', 'name': 'Duki', 'open': canal['pid']}, headers={'HX-Request': 'true'})

    assert db.execute('SELECT sa.isrc FROM source_artists sa JOIN artist_sources s ON s.id = sa.artist_source_id WHERE s.canal_id = %s', (canal['canal_id'],)).fetchone()[0] is None
    assert 'id="res-duki" class="res added" hx-swap-oob="true"' in response.text
    assert f'id="art-{canal["pid"]}-duki" hx-swap-oob="delete"' in response.text
    assert 'Ya estan todos' in response.text


def test_arrow_rejects_artist_missing_from_the_copy(canal, db, spotify):
    read_once(canal, spotify, [entry('t1', 'Duki')])

    response = canal['client'].post(f'/canales/{canal["canal_id"]}/artistas/sumar', data={'playlist_id': canal['pid'], 'artist_id': 'nadie'}, headers={'HX-Request': 'true'})

    assert response.headers['HX-Retarget'] == '#action-notice'
    assert canales.get_whitelist(db, canal['canal_id']) == []


# Filas de playlist


def test_rows_keep_spotify_order_with_unavailable_at_the_bottom(logged, db, spotify):  # noqa: F811
    owner = logged['spotify_user_id']
    own, other_target, foreign, target, colab = uid('a'), uid('b'), uid('c'), uid('d'), uid('e')
    canal_id = canales.create_canal(db, logged['user_id'], target, 'Radar', True)
    canales.create_canal(db, logged['user_id'], other_target, 'Otro', True)
    db.commit()
    library(spotify, playlist(foreign, 'Ajena', 'otro'), playlist(other_target, 'Otro', owner, total=7), playlist(target, 'Radar', owner), playlist(colab, 'Juntada', 'otro', True), playlist(own, 'Trap', owner, total=321))

    text = logged['client'].get(f'/canales/{canal_id}').text

    assert text.index(f'id="pl-{other_target}"') < text.index(f'id="pl-{colab}"') < text.index(f'id="pl-{own}"') < text.index(f'id="pl-{foreign}"') < text.index(f'id="pl-{target}"')
    assert text.count('No disponible') == 2
    assert text.count('data-n="Boton ver artistas"') == 3
    assert text.count('data-n="Fila no disponible"') == 2
    assert '321 canciones' in text


def test_unavailable_row_does_not_open(logged, db, spotify):  # noqa: F811
    foreign, target = uid('c'), uid('d')
    canal_id = canales.create_canal(db, logged['user_id'], target, 'Radar', True)
    db.commit()
    library(spotify, playlist(foreign, 'Ajena', 'otro'), playlist(target, 'Radar', logged['spotify_user_id']))
    client = logged['client']
    client.get(f'/canales/{canal_id}')

    for playlist_id in (foreign, target):
        response = client.get(f'/canales/{canal_id}/playlists/{playlist_id}', headers={'HX-Request': 'true'})
        assert response.headers['HX-Retarget'] == '#action-notice'


def test_open_param_opens_a_single_row(canal, spotify):
    read_once(canal, spotify, [entry('t1', 'Duki')])

    text = canal['client'].get(f'/canales/{canal["canal_id"]}', params={'open': canal['pid']}).text

    assert 'Duki' in text
    assert 'Buscando...' in text
    assert 'hx-trigger="load"' in text
    assert 'aria-expanded="true"' in text


def test_page_paints_from_copies_without_spotify(canal, spotify):
    read_once(canal, spotify, [entry('t1', 'Duki')])
    web.library_cache.clear()
    calls = spotify.calls.call_count

    response = canal['client'].get(f'/canales/{canal["canal_id"]}')

    assert 'Trap' in response.text
    assert spotify.calls.call_count == calls


def test_new_user_reads_library_once_and_no_items(canal, spotify):
    route = library(spotify, playlist(canal['pid'], 'Trap', canal['owner']))
    tracks = items(spotify, canal['pid'], [])

    canal['client'].get(f'/canales/{canal["canal_id"]}')

    assert route.call_count == 1
    assert tracks.call_count == 0


def test_new_playlist_appears_after_the_library_is_checked(canal, spotify):
    read_once(canal, spotify, [entry('t1', 'Duki')])
    other = uid('n')
    library(spotify, playlist(canal['pid'], 'Trap', canal['owner'], total=1), playlist(other, 'Nueva', canal['owner']))

    canal['client'].get(urls(canal)[1])

    assert 'Nueva' in canal['client'].get(f'/canales/{canal["canal_id"]}').text


def test_unchanged_library_does_not_write_rows(canal, db, spotify):
    read_once(canal, spotify, [entry('t1', 'Duki')])
    before = db.execute('SELECT playlist_id, xmin::text FROM playlist_copies WHERE user_id = %s', (canal['user_id'],)).fetchall()
    db.commit()
    web.library_cache.clear()

    canal['client'].get(urls(canal)[1])
    after = db.execute('SELECT playlist_id, xmin::text FROM playlist_copies WHERE user_id = %s', (canal['user_id'],)).fetchall()

    assert sorted(after) == sorted(before)


# Copia de playlist y refresco


def test_same_snapshot_spends_no_items_call(canal, spotify):
    route = read_once(canal, spotify, [entry('t1', 'Duki')])

    refreshed = canal['client'].get(urls(canal)[1])

    assert route.call_count == 1
    assert 'Al dia' in refreshed.text


def test_only_added_tracks_spend_one_call_and_go_to_the_bloque(canal, spotify):
    route = read_once(canal, spotify, [entry('t1', 'Duki'), entry('t2', 'Mora')])
    library(spotify, playlist(canal['pid'], 'Trap', canal['owner'], snapshot='v2', total=4))
    route.respond(json={'items': [entry('t2', 'Mora'), entry('t3', 'Zoe', 'Duki'), entry('t4', 'Bizarrap')], 'next': None})
    route.reset()

    refreshed = canal['client'].get(urls(canal)[1])

    assert route.call_count == 1
    assert route.calls.last.request.url.params['offset'] == '1'
    assert '2 artistas nuevos' in refreshed.text
    assert refreshed.text.index('Bizarrap') < refreshed.text.index('Zoe')
    assert f'id="nuevos-{canal["pid"]}" hx-swap-oob="innerHTML"' in refreshed.text
    assert 'artistas/sumar' in refreshed.text
    assert 'Al dia' in refreshed.text


def test_bloque_merges_when_the_row_is_reopened(canal, spotify):
    route = read_once(canal, spotify, [entry('t1', 'Duki'), entry('t2', 'Mora')])
    library(spotify, playlist(canal['pid'], 'Trap', canal['owner'], snapshot='v2', total=3))
    route.respond(json={'items': [entry('t2', 'Mora'), entry('t3', 'Bizarrap')], 'next': None})
    canal['client'].get(urls(canal)[1])

    fragment = canal['client'].get(urls(canal)[0])

    assert 'artista nuevo' not in fragment.text
    assert fragment.text.index('Bizarrap') < fragment.text.index('Duki') < fragment.text.index('Mora')


def test_moved_last_track_reads_it_all(canal, spotify):
    route = read_once(canal, spotify, [entry('t1', 'Duki'), entry('t2', 'Mora')])
    library(spotify, playlist(canal['pid'], 'Trap', canal['owner'], snapshot='v2', total=3))
    route.side_effect = [
        httpx.Response(200, json={'items': [entry('t9', 'Zoe')], 'next': None}),
        httpx.Response(200, json={'items': [entry('t2', 'Mora'), entry('t9', 'Zoe'), entry('t8', 'Bizarrap')], 'next': None}),
    ]
    route.reset()

    refreshed = canal['client'].get(urls(canal)[1])

    assert route.call_count == 2
    assert f'id="art-{canal["pid"]}-duki" hx-swap-oob="delete"' in refreshed.text
    assert '2 artistas nuevos' in refreshed.text


@pytest.mark.parametrize('total', [2, 1])
def test_same_or_smaller_total_reads_it_all_and_drops_the_gone(canal, spotify, total):
    route = read_once(canal, spotify, [entry('t1', 'Duki'), entry('t2', 'Mora')])
    library(spotify, playlist(canal['pid'], 'Trap', canal['owner'], snapshot='v2', total=total))
    route.respond(json={'items': [entry('t2', 'Mora')], 'next': None})
    route.reset()

    refreshed = canal['client'].get(urls(canal)[1])
    fragment = canal['client'].get(urls(canal)[0])

    assert route.call_count == 1
    assert 'offset' not in route.calls.last.request.url.params
    assert f'id="art-{canal["pid"]}-duki" hx-swap-oob="delete"' in refreshed.text
    assert 'Duki' not in fragment.text


def test_chosen_artist_stays_when_it_leaves_the_playlist(canal, db, spotify):
    route = read_once(canal, spotify, [entry('t1', 'Duki'), entry('t2', 'Mora')])
    canal['client'].post(f'/canales/{canal["canal_id"]}/artistas/sumar', data={'playlist_id': canal['pid'], 'artist_id': 'duki'}, headers={'HX-Request': 'true'})
    library(spotify, playlist(canal['pid'], 'Trap', canal['owner'], snapshot='v2', total=1))
    route.respond(json={'items': [entry('t2', 'Mora')], 'next': None})

    refreshed = canal['client'].get(urls(canal)[1])

    assert 'art-' not in refreshed.text
    assert [row['name'] for row in canales.get_whitelist(db, canal['canal_id'])] == ['Duki']


def test_rate_limit_keeps_the_copy_without_retrying(canal, spotify):
    route = read_once(canal, spotify, [entry('t1', 'Duki')])
    library(spotify, playlist(canal['pid'], 'Trap', canal['owner'], snapshot='v2', total=2))
    route.respond(429, headers={'Retry-After': '30'}, json={'error': {'status': 429}})
    route.reset()
    spotify_api.clear_cooldowns()

    try:
        refreshed = canal['client'].get(urls(canal)[1])
        fragment = canal['client'].get(urls(canal)[0])
    finally:
        spotify_api.clear_cooldowns()

    assert route.call_count == 1
    assert 'Sin actualizar' in refreshed.text
    assert 'No pudimos' not in refreshed.text
    assert 'Duki' in fragment.text


def test_first_read_failure_says_so(canal, spotify):
    library(spotify, playlist(canal['pid'], 'Trap', canal['owner']))
    canal['client'].get(f'/canales/{canal["canal_id"]}')
    spotify.get(f'{API_URL}/playlists/{canal["pid"]}/items').respond(503)

    refreshed = canal['client'].get(urls(canal)[1])

    assert 'Sin actualizar' in refreshed.text
    assert 'No pudimos leer esta playlist. Proba mas tarde.' in refreshed.text


def test_library_failure_keeps_the_copy(canal, spotify):
    read_once(canal, spotify, [entry('t1', 'Duki')])
    web.library_cache.clear()
    spotify.get(f'{API_URL}/me/playlists').respond(503)

    refreshed = canal['client'].get(urls(canal)[1])

    assert 'Sin actualizar' in refreshed.text


def test_page_survives_spotify_failure_with_copies(canal, spotify):
    read_once(canal, spotify, [entry('t1', 'Duki')])
    web.library_cache.clear()
    spotify.get(f'{API_URL}/me/playlists').respond(503)

    response = canal['client'].get(f'/canales/{canal["canal_id"]}')

    assert 'Trap' in response.text
    assert 'Spotify no respondio bien' not in response.text


# Seed Playlists


def seed_on(canal, spotify, entries, **kwargs):
    '''
    Prende la playlist del Canal como Seed Playlist desde la Vista Ajustes.
        Returns:
            response (Response): respuesta del Interruptor de playlist
    '''
    target = playlist(canal['pid'], 'Trap', canal['owner'], total=len(entries), **kwargs)
    library(spotify, target)
    items(spotify, canal['pid'], entries)
    return canal['client'].post(f'/canales/{canal["canal_id"]}/seeds', data={'playlist_id': canal['pid'], 'on': '1'}, headers={'HX-Request': 'true'})


def test_seed_makes_the_view_read_only(canal, db, spotify):
    response = seed_on(canal, spotify, [entry('t1', 'Duki', 'YSY')])
    assert 'data-n="Seed Playlists"' in response.text

    text = canal['client'].get(f'/canales/{canal["canal_id"]}').text

    assert [row['name'] for row in canales.get_whitelist(db, canal['canal_id'])] == ['Duki']
    assert 'inert' in text
    assert 'data-n="Boton ver artistas"' not in text
    assert 'data-n="Aviso de Seed"' in text
    assert f'href="/canales/{canal["canal_id"]}/ajustes"' in text
    assert 'data-n="Buscador de artistas"' not in text
    assert 'artistas/quitar' not in text
    assert 'id="wl-duki"' in text


def test_seed_rejects_manual_moves(canal, db, spotify):
    seed_on(canal, spotify, [entry('t1', 'Duki', 'YSY')])
    client = canal['client']
    headers = {'HX-Request': 'true'}
    base = f'/canales/{canal["canal_id"]}'

    add = client.post(f'{base}/artistas/sumar', data={'playlist_id': canal['pid'], 'artist_id': 'ysy'}, headers=headers)
    search_add = client.post(f'{base}/manual', data={'artist_id': 'bb', 'name': 'Bad Bunny'}, headers=headers)
    remove = client.post(f'{base}/artistas/quitar', data={'artist_id': 'duki'}, headers=headers)
    opened = client.get(urls(canal)[0], headers=headers)

    for response in (add, search_add, remove, opened):
        assert response.headers['HX-Retarget'] == '#action-notice'
    assert [row['name'] for row in canales.get_whitelist(db, canal['canal_id'])] == ['Duki']


def test_seed_excludes_lista_manual_and_turning_it_off_restores_it(canal, db, spotify):
    canales.add_manual_artist(db, canal['canal_id'], 'bb', 'Bad Bunny')
    db.commit()

    seed_on(canal, spotify, [entry('t1', 'Duki')])
    with_seed = [row['name'] for row in canales.get_whitelist(db, canal['canal_id'])]
    canal['client'].post(f'/canales/{canal["canal_id"]}/seeds', data={'playlist_id': canal['pid'], 'on': '0'})

    assert with_seed == ['Duki']
    assert [row['name'] for row in canales.get_whitelist(db, canal['canal_id'])] == ['Bad Bunny']


def test_seed_uses_a_current_copy_without_reading_items(canal, spotify):
    route = read_once(canal, spotify, [entry('t1', 'Duki')])

    canal['client'].post(f'/canales/{canal["canal_id"]}/seeds', data={'playlist_id': canal['pid'], 'on': '1'})

    assert route.call_count == 1


def test_feats_switch_is_per_seed(canal, db, spotify):
    seed_on(canal, spotify, [entry('t1', 'Duki', 'YSY')])

    response = canal['client'].post(f'/canales/{canal["canal_id"]}/feats', data={'playlist_id': canal['pid'], 'on': '1'}, headers={'HX-Request': 'true'})

    assert {row['name'] for row in canales.get_whitelist(db, canal['canal_id'])} == {'Duki', 'YSY'}
    assert 'data-n="Interruptor Feats"' in response.text


def test_seed_cannot_be_the_target_or_foreign(canal, db, spotify):
    foreign = uid('f')
    target = canales.get_canal(db, canal['user_id'], canal['canal_id'])['target_playlist_id']
    library(spotify, playlist(foreign, 'Ajena', 'otro'), playlist(target, 'Radar', canal['owner']))
    client = canal['client']

    settings = client.get(f'/canales/{canal["canal_id"]}/ajustes').text
    for playlist_id in (foreign, target):
        assert f'value="{playlist_id}"' not in settings
        response = client.post(f'/canales/{canal["canal_id"]}/seeds', data={'playlist_id': playlist_id, 'on': '1'}, headers={'HX-Request': 'true'})
        assert response.headers['HX-Retarget'] == '#action-notice'

    assert canales.get_sources(db, canal['canal_id']) == []


def test_seed_on_a_failing_unread_playlist_gives_an_aviso(canal, db, spotify):
    library(spotify, playlist(canal['pid'], 'Trap', canal['owner']))
    canal['client'].get(f'/canales/{canal["canal_id"]}')
    spotify.get(f'{API_URL}/playlists/{canal["pid"]}/items').respond(503)

    response = canal['client'].post(f'/canales/{canal["canal_id"]}/seeds', data={'playlist_id': canal['pid'], 'on': '1'}, headers={'HX-Request': 'true'})

    assert 'No pudimos leer esta playlist' in response.text
    assert canales.get_sources(db, canal['canal_id']) == []


def test_seed_on_a_playlist_gone_from_spotify_gives_an_aviso(canal, db, spotify):
    read_once(canal, spotify, [entry('t1', 'Duki')])
    canales.add_manual_artist(db, canal['canal_id'], 'bb', 'Bad Bunny')
    db.commit()
    library(spotify, playlist(canal['target'], 'Radar', canal['owner']))

    response = canal['client'].post(f'/canales/{canal["canal_id"]}/seeds', data={'playlist_id': canal['pid'], 'on': '1'}, headers={'HX-Request': 'true'})

    assert response.headers['HX-Retarget'] == '#action-notice'
    assert canales.get_sources(db, canal['canal_id'])[0]['type'] == 'manual'
    assert [row['name'] for row in canales.get_whitelist(db, canal['canal_id'])] == ['Bad Bunny']


def test_seed_with_no_artists_does_not_ask_to_choose(canal, db, spotify):
    seed_on(canal, spotify, [entry('t1', 'Duki')])
    db.execute("DELETE FROM source_artists WHERE artist_source_id IN (SELECT id FROM artist_sources WHERE canal_id = %s)", (canal['canal_id'],))
    canales.recalc_whitelist(db, canal['canal_id'])
    db.commit()

    text = canal['client'].get(f'/canales/{canal["canal_id"]}').text

    assert 'Elegi artistas' not in text
    assert 'todavia no aportan artistas' in text


def test_ajustes_shows_a_new_playlist(canal, spotify):
    read_once(canal, spotify, [entry('t1', 'Duki')])
    other = uid('n')
    library(spotify, playlist(canal['target'], 'Radar', canal['owner']), playlist(canal['pid'], 'Trap', canal['owner'], total=1), playlist(other, 'Nueva', canal['owner']))

    assert f'id="pl-{other}"' in canal['client'].get(f'/canales/{canal["canal_id"]}/ajustes').text


def test_each_canal_decides_on_its_own(canal, db, spotify):
    other = canales.create_canal(db, canal['user_id'], uid('t'), 'Otro', True)
    canales.add_manual_artist(db, other, 'bb', 'Bad Bunny')
    db.commit()

    seed_on(canal, spotify, [entry('t1', 'Duki')])

    assert [row['name'] for row in canales.get_whitelist(db, other)] == ['Bad Bunny']
    assert 'data-n="Aviso de Seed"' not in canal['client'].get(f'/canales/{other}').text


# Vista Ajustes


def test_ajustes_holds_the_config_and_canal_view_does_not(canal, spotify):
    library(spotify, playlist(canal['pid'], 'Trap', canal['owner']))
    client = canal['client']

    view = client.get(f'/canales/{canal["canal_id"]}').text
    settings = client.get(f'/canales/{canal["canal_id"]}/ajustes').text

    assert 'data-n="Boton Ajustes"' in view
    assert 'data-n="Editor de Retencion"' not in view
    assert 'data-n="Columna izquierda"' not in settings
    for name in ('Caja Playlist', 'Editor de Retencion', 'Interruptor Guardado', 'Resumen de Canal', 'Seed Playlists', 'Boton Desvincular'):
        assert f'data-n="{name}"' in settings
    assert f'href="/canales/{canal["canal_id"]}"' in settings


def test_seed_switch_without_js_stays_in_ajustes(canal, spotify):
    library(spotify, playlist(canal['pid'], 'Trap', canal['owner'], total=1))
    items(spotify, canal['pid'], [entry('t1', 'Duki')])

    response = canal['client'].post(f'/canales/{canal["canal_id"]}/seeds', data={'playlist_id': canal['pid'], 'on': '1'}, follow_redirects=False)

    assert response.headers['location'] == f'/canales/{canal["canal_id"]}/ajustes'


def test_missing_target_warns_in_ajustes(canal, spotify):
    library(spotify, playlist(canal['pid'], 'Trap', canal['owner']))

    text = canal['client'].get(f'/canales/{canal["canal_id"]}/ajustes').text

    assert 'data-n="Aviso de playlist perdida"' in text


def test_scope_no_longer_asks_for_follows():
    assert 'user-follow-read' not in spotify_api.SCOPES
    assert not hasattr(spotify_api, 'get_followed_artists')


# Imagenes


def images(*widths):
    '''
    Lista images de Spotify, de la mas grande a la mas chica; un ancho None es una portada subida por el User.
        Returns:
            images (list): dicts con url y medidas
    '''
    return [{'url': f'https://i.scdn.co/{width}', 'width': width, 'height': width} for width in widths]


def test_image_url_takes_the_smallest_that_fits():
    assert spotify_api.get_image_url(images(640, 300, 60), 112) == 'https://i.scdn.co/300'
    assert spotify_api.get_image_url(images(60), 112) == 'https://i.scdn.co/60'
    assert spotify_api.get_image_url(images(None), 112) == 'https://i.scdn.co/None'
    assert spotify_api.get_image_url([], 112) is None
    assert spotify_api.get_image_url(None, 112) is None


def test_covers_show_the_spotify_image_over_the_gradient(canal, db, spotify):
    mine = dict(playlist(canal['pid'], 'Trap', canal['owner']), images=images(640, 300, 60))
    target = dict(playlist(canal['target'], 'Radar', canal['owner']), images=images(None))
    bare = playlist(uid('p'), 'Sin portada', canal['owner'])
    library(spotify, mine, target, bare)

    text = canal['client'].get(f'/canales/{canal["canal_id"]}').text

    assert db.execute('SELECT image_url FROM playlist_copies WHERE user_id = %s AND playlist_id = %s', (canal['user_id'], canal['pid'])).fetchone()[0] == 'https://i.scdn.co/300'
    assert 'src="https://i.scdn.co/300"' in text
    # la Portada del Canal sale de su Target: Barra lateral, Cabecera y su fila no disponible
    assert text.count('src="https://i.scdn.co/None"') == 3
    assert text.count('<img') == 4


def cdn(key):
    '''
    Url final de una foto chica en el CDN de Deezer; con key vacia es la silueta de un artista sin foto.
        Returns:
            url (str): url de 56 px
    '''
    return f'https://cdn-images.dzcdn.net/images/artist/{key}/56x56-000000-80-0-0.jpg'


def save_photo(db, artist_id, url, age='0 hours'):
    '''
    Deja guardada la foto de un artista, como si la hubiera buscado la web hace `age`.
    '''
    db.execute(
        "INSERT INTO artist_photos (spotify_artist_id, url, checked_at) VALUES (%s, %s, now() - %s::interval)",
        (artist_id, url, age),
    )
    db.commit()


@pytest.fixture
def photos(canal, db):
    '''
    Endpoint de fotos contra el schema de test: toma y suelta la conexion del test en vez del pool.
        Returns:
            post (callable): pide una tanda de ids o de dicts {id, name} y devuelve la respuesta
    '''
    web.app.dependency_overrides[web.get_conn_opener] = lambda: lambda: nullcontext(db)

    def post(*artists):
        body = {'artists': [artist if isinstance(artist, dict) else {'id': artist, 'name': ''} for artist in artists]}
        return canal['client'].post('/artistas/fotos', json=body)

    yield post
    web.app.dependency_overrides.pop(web.get_conn_opener, None)


def test_avatars_never_point_at_our_server(canal, db, spotify):
    linked, saved, fresh, stale, unlinked = (uid(name) for name in ('Linked', 'Saved', 'Fresh', 'Stale', 'Unlinked'))
    db.execute('INSERT INTO deezer_artists (spotify_artist_id, deezer_artist_id, matched_by) VALUES (%s, 27, %s)', (linked.lower(), 'isrc'))
    save_photo(db, saved.lower(), cdn('saved'))
    save_photo(db, fresh.lower(), None, '2 hours')
    save_photo(db, stale.lower(), None, '25 hours')
    read_once(canal, spotify, [entry('t1', linked), entry('t2', saved), entry('t3', fresh), entry('t4', stale), entry('t5', unlinked, isrc='AR9')])

    fragment = canal['client'].get(urls(canal)[0]).text

    # ninguna foto pasa por nuestro servidor ni por el redirect de la API de Deezer
    assert 'src="/' not in fragment
    assert 'api.deezer.com' not in fragment
    # la guardada sale del CDN; las demas quedan con la inicial y las busca static/fotos.js
    assert fragment.count('<img') == 1
    assert f'src="{cdn("saved")}"' in fragment
    assert f'data-foto="{linked.lower()}" data-foto-nombre="{linked}"' in fragment
    assert f'data-foto="{unlinked.lower()}"' in fragment
    assert f'data-foto="{stale.lower()}"' in fragment
    # una que Deezer no tiene no se vuelve a pedir hasta el dia siguiente
    assert f'data-foto="{fresh.lower()}"' not in fragment
    assert f'data-foto="{saved.lower()}"' not in fragment

    moved = canal['client'].post(f'/canales/{canal["canal_id"]}/artistas/sumar', data={'playlist_id': canal['pid'], 'artist_id': saved.lower()}, headers={'HX-Request': 'true'})

    assert f'src="{cdn("saved")}"' in moved.text
    assert f'src="{cdn("saved")}"' in canal['client'].get(f'/canales/{canal["canal_id"]}').text


def test_photos_are_found_once_and_saved(canal, db, spotify, photos):
    linked, unlinked = uid('Linked'), uid('Unlinked')
    db.execute('INSERT INTO deezer_artists (spotify_artist_id, deezer_artist_id, matched_by) VALUES (%s, 27, %s)', (linked.lower(), 'isrc'))
    db.commit()
    read_once(canal, spotify, [entry('t1', linked), entry('t2', unlinked, isrc='AR9')])
    by_id = spotify.get(f'{DEEZER_URL}/artist/27').respond(json={'id': 27, 'name': linked, 'picture_small': cdn('a')})
    by_isrc = spotify.get(f'{DEEZER_URL}/track/isrc:AR9').respond(json={
        'artist': {'id': 1, 'name': unlinked}, 'contributors': [{'id': 1, 'name': unlinked, 'picture_small': cdn('b')}],
    })
    expected = {linked.lower(): cdn('a'), unlinked.lower(): cdn('b')}

    response = photos(linked.lower(), unlinked.lower())

    assert response.status_code == 200
    assert response.json() == {'photos': expected}
    rows = db.execute('SELECT spotify_artist_id, url FROM artist_photos WHERE spotify_artist_id = ANY(%s)', (list(expected),)).fetchall()
    assert dict(rows) == expected

    # el mismo artista en otra pagina sale de la base sin ir a Deezer
    assert photos(linked.lower(), unlinked.lower()).json() == {'photos': expected}
    assert by_id.call_count == 1
    assert by_isrc.call_count == 1

    fragment = canal['client'].get(urls(canal)[0]).text

    assert 'data-foto=' not in fragment
    assert f'src="{cdn("a")}"' in fragment
    assert f'src="{cdn("b")}"' in fragment


def test_artist_without_photo_is_remembered_for_a_day(canal, db, spotify, photos):
    nobody = uid('Nobody')
    read_once(canal, spotify, [entry('t1', nobody)])
    spotify.get(f'{DEEZER_URL}/search/artist').respond(json={'data': [{'id': 2, 'name': nobody, 'picture_small': cdn('')}]})

    assert photos(nobody.lower()).json() == {'photos': {nobody.lower(): None}}
    assert db.execute('SELECT url FROM artist_photos WHERE spotify_artist_id = %s', (nobody.lower(),)).fetchone() == (None,)
    # queda la inicial y no se vuelve a pedir
    fragment = canal['client'].get(urls(canal)[0]).text
    assert 'data-foto=' not in fragment
    assert '<img' not in fragment

    db.execute("UPDATE artist_photos SET checked_at = now() - interval '25 hours' WHERE spotify_artist_id = %s", (nobody.lower(),))
    db.commit()

    assert f'data-foto="{nobody.lower()}"' in canal['client'].get(urls(canal)[0]).text


def test_deezer_quota_cuts_the_batch_without_sleeping(canal, db, spotify, photos, monkeypatch):
    monkeypatch.setattr(deezer.time, 'sleep', lambda seconds: pytest.fail('la web no duerme esperando a Deezer'))
    first, second = uid('First'), uid('Second')
    read_once(canal, spotify, [entry('t1', first), entry('t2', second)])
    spotify.get(f'{DEEZER_URL}/search/artist').respond(json={'error': {'code': 4, 'message': 'Quota limit exceeded'}})

    response = photos(first.lower(), second.lower())

    # sin respuesta para ninguno: static/fotos.js los vuelve a pedir mas tarde
    assert response.json() == {'photos': {}}
    assert db.execute('SELECT count(*) FROM artist_photos WHERE spotify_artist_id = ANY(%s)', ([first.lower(), second.lower()],)).fetchone()[0] == 0


def test_buscador_name_is_searched_but_not_saved(canal, db, spotify, photos):
    stranger = uid('stranger')
    spotify.get(f'{DEEZER_URL}/search/artist').respond(json={'data': [{'id': 2, 'name': 'Duki', 'picture_small': cdn('c')}]})

    assert photos({'id': stranger, 'name': 'Duki'}).json() == {'photos': {stranger: cdn('c')}}
    assert db.execute('SELECT count(*) FROM artist_photos WHERE spotify_artist_id = %s', (stranger,)).fetchone()[0] == 0


def test_photo_batch_is_capped(canal, photos):
    ids = [uid('x') for _ in range(web.PHOTO_BATCH + 5)]

    # sin nombre ni vinculo no hay nada que buscar: vuelven sin foto y sin tocar Deezer
    assert len(photos(*ids).json()['photos']) == web.PHOTO_BATCH


def test_photos_need_a_session():
    assert TestClient(web.app).post('/artistas/fotos', json={'artists': [{'id': 'duki', 'name': 'Duki'}]}).status_code == 401


def test_buscador_keeps_added_artists_dimmed(canal, db, spotify):
    library(spotify, playlist(canal['pid'], 'Trap', canal['owner']))
    canales.add_manual_artist(db, canal['canal_id'], 'duki', 'Duki')
    db.commit()
    found = {'artists': {'items': [{'id': 'duki', 'name': 'Duki', 'images': images(640, 320, 160)}, {'id': 'nadie', 'name': 'Nadie', 'images': []}]}}
    spotify.get(f'{API_URL}/search').respond(json=found)

    text = canal['client'].get(f'/canales/{canal["canal_id"]}/buscar', params={'q': 'du'}).text

    assert 'id="res-duki" class="res added"' in text
    assert '>added</small>' in text
    assert 'name="artist_id" value="duki"' not in text
    assert 'name="artist_id" value="nadie"' in text
    assert 'src="https://i.scdn.co/160"' in text
    # sin foto en Spotify la busca static/fotos.js en Deezer, con la pagina quieta
    assert 'data-foto="nadie" data-foto-nombre="Nadie"' in text
    assert 'src="/' not in text


def test_adding_from_the_buscador_dims_the_result(canal, spotify):
    library(spotify, playlist(canal['pid'], 'Trap', canal['owner']))
    canal['client'].get(f'/canales/{canal["canal_id"]}')

    response = canal['client'].post(f'/canales/{canal["canal_id"]}/manual', data={'artist_id': 'duki', 'name': 'Duki', 'image': 'https://i.scdn.co/160'}, headers={'HX-Request': 'true'})

    assert 'id="res-duki" class="res added" hx-swap-oob="true"' in response.text
    assert response.text.count('src="https://i.scdn.co/160"') == 2


def test_profile_photo_comes_from_login(db, spotify):
    web.app.dependency_overrides[get_conn] = lambda: db
    client = TestClient(web.app)
    spotify.post(TOKEN_URL).respond(json={'access_token': 'a', 'refresh_token': 'r'})
    spotify.get(f'{API_URL}/me').respond(json={'id': uid('u_'), 'display_name': 'Luca', 'images': images(300, 64)})

    try:
        client.get(f'/callback?code=c&state={start_login(client)}')
        text = client.get('/canales/nuevo').text
    finally:
        web.app.dependency_overrides.clear()

    assert 'src="https://i.scdn.co/64"' in text


def test_old_session_asks_me_once_for_the_profile_photo(spotify, monkeypatch):
    monkeypatch.setattr(web, 'get_token', lambda conn, user: 'a')
    me = spotify.get(f'{API_URL}/me').respond(json={'id': 'luca', 'images': images(64)})
    request = SimpleNamespace(session={})
    user = {'id': 1, 'disconnected_at': None}

    assert web.get_profile_image(request, None, user) == 'https://i.scdn.co/64'
    assert web.get_profile_image(request, None, user) == 'https://i.scdn.co/64'
    assert me.call_count == 1


def test_profile_without_photo_keeps_the_initials(spotify, monkeypatch):
    monkeypatch.setattr(web, 'get_token', lambda conn, user: 'a')
    spotify.get(f'{API_URL}/me').respond(json={'id': 'luca', 'images': []})
    request = SimpleNamespace(session={})

    assert web.get_profile_image(request, None, {'id': 1, 'disconnected_at': None}) is None
    assert request.session['avatar'] == ''
