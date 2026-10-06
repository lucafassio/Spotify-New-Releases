import uuid
from datetime import date

import httpx
import pytest

from app import canales, runs, users
from app import spotify as spotify_api
from app.db import connect
from app.deezer import API_URL as DEEZER_URL
from app.spotify import ACCOUNTS_URL, API_URL

TOKEN_URL = f'{ACCOUNTS_URL}/api/token'

# jueves: el Lote de la semana arranca el sabado 2026-09-26 y cierra el viernes 2026-10-02
THURSDAY = date(2026, 10, 1)
FRIDAY = date(2026, 10, 2)


def uid(prefix):
    '''
    Id unico: los caches de Deezer son globales y sobreviven entre tests porque el Run commitea.
        Returns:
            value (str): prefijo mas un sufijo al azar
    '''
    return f'{prefix}_{uuid.uuid4().hex[:10]}'


def deezer_id():
    '''
    Id numerico unico de Deezer.
        Returns:
            value (int): id al azar
    '''
    return uuid.uuid4().int % 10**12


def new_user(db, spotify):
    '''
    User de la Shared App con el refresh de token y la biblioteca mockeados.
        Returns:
            user (dict): fila de users
    '''
    user_id = users.save_shared_login(db, uid('u'), 'refresh')['user_id']
    db.commit()
    spotify.post(TOKEN_URL).respond(json={'access_token': 'token'})
    spotify.get(f'{API_URL}/me/playlists').respond(json={'items': [], 'next': None})
    return users.get_user(db, user_id)


def new_canal(db, user, artists, added_at='2026-09-01T12:00:00-03:00'):
    '''
    Canal con artistas a mano dados de alta en added_at.
        Returns:
            canal_id (int): id del Canal
    '''
    canal_id = canales.create_canal(db, user['id'], uid('p'), 'Radar', True)

    for artist_id, name in artists.items():
        canales.add_manual_artist(db, canal_id, artist_id, name)

    db.execute('UPDATE whitelist_artists SET added_at = %s WHERE canal_id = %s', (added_at, canal_id))
    db.commit()
    return canal_id


def mock_deezer_artist(spotify, name, albums):
    '''
    Artista de Deezer que se encuentra por nombre, con su discografia.
        Returns:
            route (respx.Route): ruta de la discografia
    '''
    artist_id = deezer_id()
    spotify.get(f'{DEEZER_URL}/search/artist', params={'q': name}).respond(json={'data': [{'id': artist_id, 'name': name, 'nb_fan': 5}]})
    return spotify.get(f'{DEEZER_URL}/artist/{artist_id}/albums').respond(json={'data': albums, 'total': len(albums)})


def deezer_album(release_date, title='Single', record_type='single', album_id=None):
    '''
    Album como lo lista Deezer en la discografia de un artista.
        Returns:
            album (dict): album de prueba
    '''
    return {'id': album_id or deezer_id(), 'title': title, 'release_date': release_date, 'record_type': record_type, 'explicit_lyrics': True}


def mock_spotify_album(spotify, release, main, tracks):
    '''
    Mockea el camino Deezer -> Spotify de un Release: UPC en Deezer, busqueda por UPC y album en Spotify.
        Args:
            release (dict): album de Deezer
            main (list): ids de los artistas principales del album en Spotify
            tracks (list): tracks del album
        Returns:
            routes (dict): rutas search y album para contar llamadas
    '''
    upc = str(release['id'])
    album_id = uid('al')
    artists = [{'id': artist_id, 'name': artist_id.upper()} for artist_id in main]
    spotify.get(f'{DEEZER_URL}/album/{release["id"]}').respond(json={'id': release['id'], 'upc': upc})
    search = spotify.get(f'{API_URL}/search', params={'q': f'upc:{upc}'}).respond(json={'albums': {'items': [{'id': album_id, 'artists': artists}]}})
    album = spotify.get(f'{API_URL}/albums/{album_id}').respond(json={'id': album_id, 'artists': artists, 'tracks': {'items': tracks, 'next': None}})
    return {'search': search, 'album': album, 'album_id': album_id}


def track(number, *artist_ids):
    '''
    Track simplificado de un album de Spotify.
        Returns:
            track (dict): track de prueba
    '''
    return {'uri': f'spotify:track:{uid("t")}', 'artists': [{'id': artist_id, 'name': artist_id} for artist_id in artist_ids], 'disc_number': 1, 'track_number': number}


def lote_items(db, canal_id):
    '''
    Tracks de todos los Lotes del Canal en orden de album.
        Returns:
            items (list): tuplas (artist_name, track_number, release_date, status)
    '''
    return db.execute(
        '''
        SELECT i.artist_name, i.track_number, i.release_date, i.status FROM lote_items i JOIN lotes l ON l.id = i.lote_id
        WHERE l.canal_id = %s ORDER BY i.release_date, i.track_number
        ''',
        (canal_id,),
    ).fetchall()


def lotes(db, canal_id):
    '''
    Lotes del Canal por semana.
        Returns:
            lotes (list): tuplas (week_start, status)
    '''
    return db.execute('SELECT week_start, status FROM lotes WHERE canal_id = %s ORDER BY week_start', (canal_id,)).fetchall()


def run_status(db, user, day):
    return db.execute('SELECT status FROM runs WHERE user_id = %s AND date = %s', (user['id'], day)).fetchone()[0]


def test_week_starts_on_saturday():
    assert runs.get_week_start(date(2026, 10, 2)) == date(2026, 9, 26)
    assert runs.get_week_start(date(2026, 10, 3)) == date(2026, 10, 3)
    assert runs.get_week_start(date(2026, 10, 1)) == date(2026, 9, 26)


def test_run_adds_window_release_as_pending(db, spotify):
    user = new_user(db, spotify)
    a = uid('a')
    canal_id = new_canal(db, user, {a: 'Artista A'})
    single = deezer_album('2026-09-28')
    mock_deezer_artist(spotify, 'Artista A', [
        deezer_album('2026-08-01', 'Viejo', 'album'),
        deezer_album('2026-09-29', 'Hits', 'compile'),
        deezer_album('2026-10-09', 'Anunciado'),
        single,
    ])
    routes = mock_spotify_album(spotify, single, [a], [track(1, a), track(2, a)])

    found = runs.run_user(db, user, THURSDAY)

    assert found == 2
    assert lote_items(db, canal_id) == [(a.upper(), 1, date(2026, 9, 28), 'pendiente'), (a.upper(), 2, date(2026, 9, 28), 'pendiente')]
    assert lotes(db, canal_id) == [(date(2026, 9, 26), 'abierto')]
    assert routes['search'].call_count == 1
    assert run_status(db, user, THURSDAY) == 'exitoso'


def test_seen_release_costs_no_spotify_calls(db, spotify):
    user = new_user(db, spotify)
    a = uid('a')
    canal_id = new_canal(db, user, {a: 'Artista A'})
    single = deezer_album('2026-09-28')
    mock_deezer_artist(spotify, 'Artista A', [single])
    routes = mock_spotify_album(spotify, single, [a], [track(1, a)])

    runs.run_user(db, user, date(2026, 9, 30))
    found = runs.run_user(db, user, THURSDAY)

    assert found == 0
    assert len(lote_items(db, canal_id)) == 1
    assert routes['search'].call_count == 1
    assert routes['album'].call_count == 1


def test_release_before_artist_was_added_is_ignored(db, spotify):
    user = new_user(db, spotify)
    a = uid('a')
    canal_id = new_canal(db, user, {a: 'Artista A'}, added_at='2026-09-29T15:00:00-03:00')
    before = deezer_album('2026-09-28')
    same_day = deezer_album('2026-09-29')
    mock_deezer_artist(spotify, 'Artista A', [before, same_day])
    skipped = mock_spotify_album(spotify, before, [a], [track(1, a)])
    mock_spotify_album(spotify, same_day, [a], [track(1, a)])

    runs.run_user(db, user, THURSDAY)

    assert [item[2] for item in lote_items(db, canal_id)] == [date(2026, 9, 29)]
    assert not skipped['search'].called


def test_feat_in_foreign_album_adds_only_its_tracks(db, spotify):
    user = new_user(db, spotify)
    a = uid('a')
    canal_id = new_canal(db, user, {a: 'Artista A'})
    album = deezer_album('2026-09-28', 'Disco ajeno', 'album')
    mock_deezer_artist(spotify, 'Artista A', [album])
    mock_spotify_album(spotify, album, ['otro'], [track(1, 'otro'), track(2, 'otro', a), track(3, 'otro')])

    runs.run_user(db, user, THURSDAY)

    assert lote_items(db, canal_id) == [('OTRO', 2, date(2026, 9, 28), 'pendiente')]


def test_album_without_whitelist_artists_is_read_once(db, spotify):
    user = new_user(db, spotify)
    a = uid('a')
    canal_id = new_canal(db, user, {a: 'Artista A'})
    homonym = deezer_album('2026-09-28')
    mock_deezer_artist(spotify, 'Artista A', [homonym])
    routes = mock_spotify_album(spotify, homonym, ['otro'], [track(1, 'otro')])

    runs.run_user(db, user, date(2026, 9, 30))
    runs.run_user(db, user, THURSDAY)

    assert routes['album'].call_count == 1
    assert lote_items(db, canal_id) == []


def test_artist_in_two_canales_is_explored_once(db, spotify):
    user = new_user(db, spotify)
    a = uid('a')
    rock = new_canal(db, user, {a: 'Artista A'})
    trap = new_canal(db, user, {a: 'Artista A'})
    single = deezer_album('2026-09-28')
    discography = mock_deezer_artist(spotify, 'Artista A', [single])
    routes = mock_spotify_album(spotify, single, [a], [track(1, a)])

    found = runs.run_user(db, user, THURSDAY)

    assert found == 2
    assert discography.call_count == 1
    assert routes['album'].call_count == 1
    assert len(lote_items(db, rock)) == len(lote_items(db, trap)) == 1


def test_clean_twin_is_dropped(db, spotify):
    user = new_user(db, spotify)
    a = uid('a')
    canal_id = new_canal(db, user, {a: 'Artista A'})
    explicit = deezer_album('2026-09-28', 'Algun Dia')
    mock_deezer_artist(spotify, 'Artista A', [explicit, deezer_album('2026-09-28', 'Algun Dia (Clean)')])
    mock_spotify_album(spotify, explicit, [a], [track(1, a)])

    found = runs.run_user(db, user, THURSDAY)

    assert found == 1
    assert len(lote_items(db, canal_id)) == 1


def test_clean_without_explicit_twin_stays():
    releases = {
        1: {'title': 'Algun Dia', 'release_date': THURSDAY},
        2: {'title': 'Algun Dia [Clean Version]', 'release_date': THURSDAY},
        3: {'title': 'Otro (Clean)', 'release_date': THURSDAY},
        4: {'title': 'Algun Dia (Clean)', 'release_date': FRIDAY},
    }

    assert set(runs.drop_clean_twins(releases)) == {1, 3, 4}


def test_album_missing_in_spotify_is_retried_next_day(db, spotify):
    user = new_user(db, spotify)
    a = uid('a')
    canal_id = new_canal(db, user, {a: 'Artista A'})
    single = deezer_album('2026-09-28')
    mock_deezer_artist(spotify, 'Artista A', [single])
    spotify.get(f'{DEEZER_URL}/album/{single["id"]}').respond(json={'id': single['id'], 'upc': '999'})
    search = spotify.get(f'{API_URL}/search', params={'q': 'upc:999'}).respond(json={'albums': {'items': []}})

    runs.run_user(db, user, date(2026, 9, 30))
    # el Run usa una fecha de prueba pero checked_at es la hora real: lo llevamos al dia del primer Run
    db.execute("UPDATE album_links SET checked_at = '2026-09-30T12:00:00-03:00' WHERE deezer_album_id = %s", (single['id'],))
    db.commit()
    runs.run_user(db, user, date(2026, 9, 30))
    runs.run_user(db, user, THURSDAY)

    assert search.call_count == 2
    assert lote_items(db, canal_id) == []


def test_friday_closes_lote_and_later_runs_fill_the_next(db, spotify):
    user = new_user(db, spotify)
    canal_id = new_canal(db, user, {uid('a'): 'Artista A'})
    mock_deezer_artist(spotify, 'Artista A', [])

    runs.run_user(db, user, FRIDAY)
    closed = lotes(db, canal_id)
    runs.run_user(db, user, FRIDAY)

    assert closed == [(date(2026, 9, 26), 'cerrado')]
    assert lotes(db, canal_id) == [(date(2026, 9, 26), 'cerrado'), (date(2026, 10, 3), 'abierto')]


def test_lote_left_open_closes_the_next_week(db, spotify):
    user = new_user(db, spotify)
    canal_id = new_canal(db, user, {uid('a'): 'Artista A'})
    mock_deezer_artist(spotify, 'Artista A', [])

    runs.run_user(db, user, THURSDAY)
    runs.run_user(db, user, date(2026, 10, 4))

    assert lotes(db, canal_id) == [(date(2026, 9, 26), 'cerrado'), (date(2026, 10, 3), 'abierto')]


def test_artist_is_linked_by_isrc_before_name(db, spotify):
    user = new_user(db, spotify)
    a = uid('a')
    canal_id = canales.create_canal(db, user['id'], uid('p'), 'Radar', True)
    canales.add_source(db, canal_id, 'seed_playlist', {a: {'name': 'Myke Towers', 'feat': False, 'isrc': 'US1'}}, uid('seed'))
    db.commit()
    artist_id = deezer_id()
    spotify.get(f'{DEEZER_URL}/track/isrc:US1').respond(json={'id': 1, 'artist': {'id': 1, 'name': 'Hanzel'}, 'contributors': [{'id': 1, 'name': 'Hanzel'}, {'id': artist_id, 'name': 'Myke Towers'}]})
    by_name = spotify.get(f'{DEEZER_URL}/search/artist').respond(json={'data': []})
    discography = spotify.get(f'{DEEZER_URL}/artist/{artist_id}/albums').respond(json={'data': []})

    runs.run_user(db, user, THURSDAY)

    assert discography.called
    assert not by_name.called
    assert db.execute('SELECT matched_by FROM deezer_artists WHERE spotify_artist_id = %s', (a,)).fetchone()[0] == 'isrc'


def test_disconnected_user_fails_the_run(db, spotify):
    user_id = users.save_shared_login(db, uid('u'), 'refresh')['user_id']
    db.commit()
    spotify.post(TOKEN_URL).respond(400, json={'error': 'invalid_grant'})

    with pytest.raises(users.UserDisconnected):
        runs.run_user(db, users.get_user(db, user_id), THURSDAY)

    assert db.execute('SELECT status FROM runs WHERE user_id = %s', (user_id,)).fetchone()[0] == 'fallido'


def test_quota_exceeded_fails_the_run(db, spotify):
    user = new_user(db, spotify)
    a = uid('a')
    canal_id = new_canal(db, user, {a: 'Artista A'})
    single = deezer_album('2026-09-28')
    mock_deezer_artist(spotify, 'Artista A', [single])
    spotify.get(f'{DEEZER_URL}/album/{single["id"]}').respond(json={'id': single['id'], 'upc': '777'})
    spotify.get(f'{API_URL}/search').mock(return_value=httpx.Response(429, headers={'retry-after': '86000'}, json={'error': {'status': 429, 'reason': 'QUOTA_EXCEEDED'}}))

    with pytest.raises(spotify_api.QuotaExceeded):
        runs.run_user(db, user, THURSDAY)

    assert run_status(db, user, THURSDAY) == 'fallido'
    assert lote_items(db, canal_id) == []


def test_run_already_going_is_busy(db, spotify, db_schema):
    user = new_user(db, spotify)

    with connect(search_path=db_schema) as other:
        other.execute('SELECT pg_advisory_lock(%s, %s)', (runs.RUN_LOCK, user['id']))

        with pytest.raises(runs.RunBusy):
            runs.run_user(db, user, THURSDAY)

        other.execute('SELECT pg_advisory_unlock(%s, %s)', (runs.RUN_LOCK, user['id']))
