import uuid

import pytest

from app import canales, copias, users
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


def test_sync_targets_only_updates_owned_canales_and_keeps_missing(db):
    user_id = new_user(db)
    other_id = new_user(db)
    canal_id = canales.create_canal(db, user_id, 'target', 'Antes', True)
    missing_id = canales.create_canal(db, user_id, 'missing', 'No cambiar', True)
    other_canal_id = canales.create_canal(db, other_id, 'target', 'Ajeno', True)
    library = [{'id': f'irrelevant-{n}', 'name': 'Otra', 'items': {'total': 1}} for n in range(100)]
    library.append({'id': 'target', 'name': 'Despues', 'items': {'total': 42}})
    before = canales.list_canales(db, user_id)

    updated = canales.sync_target_names(db, user_id, library, before)

    assert next(row for row in updated if row['id'] == canal_id)['target_name'] == 'Despues'
    assert next(row for row in before if row['id'] == canal_id)['target_name'] == 'Antes'
    assert canales.get_canal(db, user_id, canal_id)['target_total'] == 42
    assert canales.get_canal(db, user_id, missing_id)['target_name'] == 'No cambiar'
    assert canales.get_canal(db, other_id, other_canal_id)['target_name'] == 'Ajeno'


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


def test_seeds_are_a_union_without_feats_and_exclude_the_lista_manual(db):
    canal_id = canales.create_canal(db, new_user(db), 'p1', 'Rock', True)
    canales.add_manual_artist(db, canal_id, 'd', 'D')

    canales.add_source(db, canal_id, 'seed_playlist', artists(a=False, b=False, x=True), 'seed1')
    canales.add_source(db, canal_id, 'seed_playlist', artists(b=False, c=False), 'seed2')

    assert whitelist_ids(db, canal_id) == {'a', 'b', 'c'}


def test_lista_manual_comes_back_when_the_last_seed_goes(db):
    canal_id = canales.create_canal(db, new_user(db), 'p1', 'Rock', True)
    canales.add_manual_artist(db, canal_id, 'd', 'D')
    canales.add_source(db, canal_id, 'seed_playlist', artists(a=False), 'seed1')

    canales.remove_source(db, canal_id, 'seed_playlist', 'seed1')

    assert whitelist_ids(db, canal_id) == {'d'}


def test_artist_in_both_forms_keeps_added_at_when_switching(db):
    canal_id = canales.create_canal(db, new_user(db), 'p1', 'Rock', True)
    canales.add_manual_artist(db, canal_id, 'a', 'A')
    db.execute("UPDATE whitelist_artists SET added_at = now() - interval '30 days' WHERE canal_id = %s", (canal_id,))
    old = canales.get_whitelist(db, canal_id)[0]['added_at']

    canales.add_source(db, canal_id, 'seed_playlist', artists(a=False), 'seed1')
    canales.remove_source(db, canal_id, 'seed_playlist', 'seed1')

    assert canales.get_whitelist(db, canal_id)[0]['added_at'] == old


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

    canales.remove_manual_artist(db, canal_id, 'a')
    canales.add_manual_artist(db, canal_id, 'a', 'A')

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


def seed_library(db, user_id, *playlists):
    '''
    Guarda la biblioteca como lo hace el Run antes de refrescar las Seed Playlists.
        Returns:
            library (dict): playlist_id -> playlist
    '''
    copias.save_library(db, user_id, list(playlists))
    return {playlist['id']: playlist for playlist in playlists}


def library_playlist(playlist_id, snapshot, total=1):
    '''
    Playlist de GET /me/playlists.
        Returns:
            playlist (dict): playlist simplificada
    '''
    return {'id': playlist_id, 'name': playlist_id, 'owner': {'id': 'yo'}, 'snapshot_id': snapshot, 'items': {'total': total}}


def test_refresh_sources_lista_manual_reads_nothing(db, spotify):
    user_id = new_user(db)
    canal = canales.get_canal(db, user_id, canales.create_canal(db, user_id, 'p1', 'Rock', True))
    canales.add_manual_artist(db, canal['id'], 'a', 'A')
    library = seed_library(db, user_id, library_playlist('otra', 's1'))

    canales.refresh_sources(db, 'token', canal, library)

    assert whitelist_ids(db, canal['id']) == {'a'}
    assert not spotify.calls


def test_refresh_sources_rereads_only_changed_seeds(db, spotify):
    user_id = new_user(db)
    canal = canales.get_canal(db, user_id, canales.create_canal(db, user_id, 'p1', 'Rock', True))
    canales.add_source(db, canal['id'], 'seed_playlist', artists(a=False), 'igual')
    canales.add_source(db, canal['id'], 'seed_playlist', artists(b=False), 'cambio')
    db.execute("UPDATE artist_sources SET snapshot_id = 's1' WHERE canal_id = %s", (canal['id'],))
    same = spotify.get(f'{API_URL}/playlists/igual/items').respond(json={'items': [], 'next': None})
    changed = spotify.get(f'{API_URL}/playlists/cambio/items').respond(json={'items': [track('c', external_ids={'isrc': 'X1'})], 'next': None})
    library = seed_library(db, user_id, library_playlist('igual', 's1'), library_playlist('cambio', 's2'))

    canales.refresh_sources(db, 'token', canal, library)

    assert not same.called
    assert changed.called
    assert whitelist_ids(db, canal['id']) == {'a', 'c'}
    assert db.execute("SELECT sa.isrc FROM source_artists sa JOIN artist_sources s ON s.id = sa.artist_source_id WHERE s.canal_id = %s AND sa.artist_id = 'c'", (canal['id'],)).fetchone()[0] == 'X1'


def test_refresh_sources_uses_a_copy_another_canal_already_refreshed(db, spotify):
    user_id = new_user(db)
    canal = canales.get_canal(db, user_id, canales.create_canal(db, user_id, 'p1', 'Rock', True))
    canales.add_source(db, canal['id'], 'seed_playlist', artists(a=False), 'seed')
    db.execute("UPDATE artist_sources SET snapshot_id = 's1' WHERE canal_id = %s", (canal['id'],))
    route = spotify.get(f'{API_URL}/playlists/seed/items').respond(json={'items': [track('b')], 'next': None})
    library = seed_library(db, user_id, library_playlist('seed', 's2'))
    copias.refresh(db, 'token', user_id, library['seed'])

    canales.refresh_sources(db, 'token', canal, library)

    assert route.call_count == 1
    assert whitelist_ids(db, canal['id']) == {'b'}


def test_refresh_sources_tail_only_when_tracks_were_added(db, spotify):
    user_id = new_user(db)
    canal = canales.get_canal(db, user_id, canales.create_canal(db, user_id, 'p1', 'Rock', True))
    old = track('a', uri='spotify:track:t1')
    route = spotify.get(f'{API_URL}/playlists/seed/items').respond(json={'items': [old], 'next': None})
    copias.save_library(db, user_id, [library_playlist('seed', 's1')])
    copias.refresh(db, 'token', user_id, library_playlist('seed', 's1'))
    canales.add_source(db, canal['id'], 'seed_playlist', copias.get_artists(db, user_id, 'seed'), 'seed')
    route.respond(json={'items': [old, track('b', uri='spotify:track:t2')], 'next': None})
    route.reset()
    library = seed_library(db, user_id, library_playlist('seed', 's2', total=2))

    canales.refresh_sources(db, 'token', canal, library)

    assert route.call_count == 1
    assert route.calls.last.request.url.params.get('offset') is None
    assert whitelist_ids(db, canal['id']) == {'a', 'b'}


def test_refresh_sources_lets_the_run_see_spotify_failures(db, spotify):
    user_id = new_user(db)
    canal = canales.get_canal(db, user_id, canales.create_canal(db, user_id, 'p1', 'Rock', True))
    canales.add_source(db, canal['id'], 'seed_playlist', artists(a=False), 'seed')
    spotify.get(f'{API_URL}/playlists/seed/items').respond(429, headers={'Retry-After': '86000'}, json={'error': {'status': 429, 'reason': 'QUOTA_EXCEEDED'}})
    library = seed_library(db, user_id, library_playlist('seed', 's2'))

    with pytest.raises(spotify_api.QuotaExceeded):
        canales.refresh_sources(db, 'token', canal, library)


def test_refresh_sources_keeps_seed_missing_from_library(db, spotify):
    user_id = new_user(db)
    canal = canales.get_canal(db, user_id, canales.create_canal(db, user_id, 'p1', 'Rock', True))
    canales.add_source(db, canal['id'], 'seed_playlist', artists(a=False), 'borrada')

    canales.refresh_sources(db, 'token', canal, {})

    assert whitelist_ids(db, canal['id']) == {'a'}
    assert not spotify.calls


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
        'a': {'name': 'A', 'feat': False, 'isrc': None},
        'b': {'name': 'B', 'feat': False, 'isrc': None},
        'c': {'name': 'C', 'feat': False, 'isrc': None},
        'd': {'name': 'D', 'feat': True, 'isrc': None},
    }


def test_playlist_artists_keep_an_isrc(spotify):
    spotify.get(f'{API_URL}/playlists/s1/items').respond(json={
        'items': [track('a', 'b'), track('b', external_ids={'isrc': 'B1'}), track('a', external_ids={'isrc': 'A2'})],
        'next': None,
    })

    found = spotify_artists('s1')

    assert found['a']['isrc'] == 'A2'
    assert found['b']['isrc'] == 'B1'


def spotify_artists(playlist_id):
    return spotify_api.collect_artists(spotify_api.get_playlist_entries('token', playlist_id))


def test_playlist_artists_follows_pages(spotify):
    second = f'{API_URL}/playlists/s1/items?offset=50&limit=50'
    spotify.get(second).respond(json={'items': [track('b')], 'next': None})
    spotify.get(f'{API_URL}/playlists/s1/items').respond(json={'items': [track('a')], 'next': second})

    assert set(spotify_artists('s1')) == {'a', 'b'}
