import uuid

import pytest

from app import canales, users
from app import spotify as spotify_api
from app.spotify import API_URL


def new_user(db):
    '''
    Crea un User de prueba con un spotify_user_id unico.
        Returns:
            user_id (int): id interno del User
    '''
    login = users.save_shared_login(db, f'u_{uuid.uuid4().hex[:10]}', 'refresh')
    return login['user_id']


def whitelist_ids(db, canal_id):
    '''
    Ids de artistas de la Whitelist del Canal.
        Returns:
            ids (set): artist_id de cada fila
    '''
    return {row['artist_id'] for row in canales.get_whitelist(db, canal_id)}


def artists(**names):
    '''
    Arma el dict que devuelven los lectores de Spotify: nombre=True marca feat.
        Returns:
            artists (dict): artist_id -> {name, feat}
    '''
    return {artist_id: {'name': artist_id.upper(), 'feat': feat} for artist_id, feat in names.items()}


def test_canal_limit_is_five(db):
    user_id = new_user(db)
    for n in range(canales.MAX_CANALES):
        canales.create_canal(db, user_id, f'p{n}', f'Playlist {n}', True)

    with pytest.raises(canales.CanalError):
        canales.create_canal(db, user_id, 'p9', 'Una mas', True)


def test_playlist_is_target_of_one_canal(db):
    user_id = new_user(db)
    canales.create_canal(db, user_id, 'p1', 'Rock', False)

    with pytest.raises(canales.CanalError):
        canales.create_canal(db, user_id, 'p1', 'Rock', False)


def test_new_canal_is_acumulativa_with_guardado(db):
    user_id = new_user(db)
    canal = canales.get_canal(db, user_id, canales.create_canal(db, user_id, 'p1', 'Rock', True))

    assert canal['retencion_n'] is None
    assert canal['guardado_filter'] is True


def test_retencion_n_only_on_bot_playlist(db):
    user_id = new_user(db)
    canal = canales.get_canal(db, user_id, canales.create_canal(db, user_id, 'p1', 'Mia', False))

    with pytest.raises(canales.CanalError):
        canales.set_retencion(db, canal, 4)


def test_other_user_cannot_see_canal(db):
    canal_id = canales.create_canal(db, new_user(db), 'p1', 'Rock', True)

    assert canales.get_canal(db, new_user(db), canal_id) is None


def test_whitelist_is_union_of_sources_without_feats(db):
    canal_id = canales.create_canal(db, new_user(db), 'p1', 'Rock', True)

    canales.add_source(db, canal_id, 'seed_playlist', artists(a=False, b=False, x=True), 'seed1')
    canales.add_source(db, canal_id, 'followed', artists(b=False, c=False))
    canales.add_manual_artist(db, canal_id, 'd', 'D')

    assert whitelist_ids(db, canal_id) == {'a', 'b', 'c', 'd'}


def test_feats_toggle_adds_and_removes_guests(db):
    canal_id = canales.create_canal(db, new_user(db), 'p1', 'Rock', True)
    canales.add_source(db, canal_id, 'seed_playlist', artists(a=False, x=True), 'seed1')

    canales.set_feats(db, canal_id, 'seed1', True)
    with_feats = whitelist_ids(db, canal_id)
    canales.set_feats(db, canal_id, 'seed1', False)

    assert with_feats == {'a', 'x'}
    assert whitelist_ids(db, canal_id) == {'a'}


def test_removing_a_source_keeps_artists_other_sources_bring(db):
    canal_id = canales.create_canal(db, new_user(db), 'p1', 'Rock', True)
    canales.add_source(db, canal_id, 'seed_playlist', artists(a=False, b=False), 'seed1')
    canales.add_source(db, canal_id, 'seed_playlist', artists(b=False), 'seed2')

    canales.remove_source(db, canal_id, 'seed_playlist', 'seed1')

    assert whitelist_ids(db, canal_id) == {'b'}


def test_artist_that_leaves_and_returns_gets_new_added_at(db):
    canal_id = canales.create_canal(db, new_user(db), 'p1', 'Rock', True)
    canales.add_manual_artist(db, canal_id, 'a', 'A')
    db.execute("UPDATE whitelist_artists SET added_at = now() - interval '30 days' WHERE canal_id = %s", (canal_id,))
    old = canales.get_whitelist(db, canal_id)[0]['added_at']

    canales.add_source(db, canal_id, 'followed', artists(a=False))
    kept = canales.get_whitelist(db, canal_id)[0]['added_at']
    canales.remove_manual_artist(db, canal_id, 'a')
    canales.remove_source(db, canal_id, 'followed')
    canales.add_manual_artist(db, canal_id, 'a', 'A')

    assert kept == old
    assert canales.get_whitelist(db, canal_id)[0]['added_at'] > old


def test_reenabling_a_seed_replaces_its_artists(db):
    canal_id = canales.create_canal(db, new_user(db), 'p1', 'Rock', True)
    canales.add_source(db, canal_id, 'seed_playlist', artists(a=False), 'seed1')

    canales.add_source(db, canal_id, 'seed_playlist', artists(b=False), 'seed1')

    assert whitelist_ids(db, canal_id) == {'b'}
    assert len(canales.get_sources(db, canal_id)) == 1


def test_whitelist_shows_artist_names(db):
    canal_id = canales.create_canal(db, new_user(db), 'p1', 'Rock', True)
    canales.add_manual_artist(db, canal_id, 'id1', 'Duki')

    assert canales.get_whitelist(db, canal_id)[0]['name'] == 'Duki'


def test_delete_canal_cascades_and_keeps_playlist(db, spotify):
    user_id = new_user(db)
    canal_id = canales.create_canal(db, user_id, 'p1', 'Rock', True)
    canales.add_manual_artist(db, canal_id, 'a', 'A')

    canales.delete_canal(db, None, canales.get_canal(db, user_id, canal_id), False)

    assert db.execute('SELECT count(*) FROM whitelist_artists WHERE canal_id = %s', (canal_id,)).fetchone()[0] == 0
    assert db.execute('SELECT count(*) FROM artist_sources WHERE canal_id = %s', (canal_id,)).fetchone()[0] == 0
    assert not spotify.calls


def test_delete_canal_with_playlist_removes_it_from_library(db, spotify):
    user_id = new_user(db)
    canal_id = canales.create_canal(db, user_id, 'p1', 'Rock', True)
    route = spotify.delete(f'{API_URL}/me/library').respond(200)

    canales.delete_canal(db, 'token', canales.get_canal(db, user_id, canal_id), True)

    assert route.calls.last.request.url.params['uris'] == 'spotify:playlist:p1'
    assert canales.get_canal(db, user_id, canal_id) is None


def test_refresh_sources_rereads_spotify(db, spotify):
    canal_id = canales.create_canal(db, new_user(db), 'p1', 'Rock', True)
    canales.add_source(db, canal_id, 'followed', artists(a=False))
    spotify.get(f'{API_URL}/me/following').respond(json={'artists': {'items': [{'id': 'b', 'name': 'B'}], 'next': None}})

    canales.refresh_sources(db, 'token', canal_id)

    assert whitelist_ids(db, canal_id) == {'b'}


def track(*artist_ids, **extra):
    '''
    Item de GET /playlists/{id}/items con un track de esos artistas.
        Returns:
            entry (dict): item con su track
    '''
    return {'item': {'type': 'track', 'is_local': False, 'artists': [{'id': a, 'name': a.upper()} for a in artist_ids], **extra}}


def test_playlist_artists_main_wins_over_feat(spotify):
    spotify.get(f'{API_URL}/playlists/s1/items').respond(json={
        'items': [track('a', 'b'), track('b'), track('c', 'd'), {'item': None}, {'item': {'type': 'episode'}}, track('z', is_local=True)],
        'next': None,
    })

    found = spotify_artists('s1')

    assert found == {
        'a': {'name': 'A', 'feat': False},
        'b': {'name': 'B', 'feat': False},
        'c': {'name': 'C', 'feat': False},
        'd': {'name': 'D', 'feat': True},
    }


def spotify_artists(playlist_id):
    return spotify_api.get_playlist_artists('token', playlist_id)


def test_playlist_artists_follows_pages(spotify):
    second = f'{API_URL}/playlists/s1/items?offset=50&limit=50'
    spotify.get(second).respond(json={'items': [track('b')], 'next': None})
    spotify.get(f'{API_URL}/playlists/s1/items').respond(json={'items': [track('a')], 'next': second})

    assert set(spotify_artists('s1')) == {'a', 'b'}


def test_followed_artists_follow_cursor(spotify):
    second = f'{API_URL}/me/following?type=artist&after=a&limit=50'
    spotify.get(second).respond(json={'artists': {'items': [{'id': 'b', 'name': 'B'}], 'next': None}})
    spotify.get(f'{API_URL}/me/following').respond(json={'artists': {'items': [{'id': 'a', 'name': 'A'}], 'next': second}})

    assert set(spotify_api.get_followed_artists('token')) == {'a', 'b'}
