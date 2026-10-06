import json
import uuid
from datetime import date

import httpx
import pytest

from app import canales, entregas, runs, users, web
from app.deezer import API_URL as DEEZER_URL
from app.spotify import ACCOUNTS_URL, API_URL

SATURDAY = date(2026, 9, 26)
FRIDAY = date(2026, 10, 2)


def uid(prefix):
    '''
    Id unico: la Entrega commitea, asi que lo de un test sobrevive al rollback.
        Returns:
            value (str): prefijo mas un sufijo al azar
    '''
    return f'{prefix}_{uuid.uuid4().hex[:10]}'


def new_canal(db, bot=True, retencion_n=None, guardado=False, artists=('a1',)):
    '''
    Canal con artistas a mano y la config pedida.
        Returns:
            canal (dict): fila de canales
    '''
    user_id = users.save_shared_login(db, uid('u'), 'refresh')['user_id']
    canal_id = canales.create_canal(db, user_id, uid('p'), 'Radar', bot)

    for artist_id in artists:
        canales.add_manual_artist(db, canal_id, artist_id, artist_id)

    db.execute('UPDATE canales SET retencion_n = %s, guardado_filter = %s WHERE id = %s', (retencion_n, guardado, canal_id))
    db.commit()
    return canales.get_canal(db, user_id, canal_id)


def item(artist_name='A', album='al1', number=1, release_date='2026-09-30', artist_ids=('a1',), status='pendiente', uri=None):
    '''
    Track de un Lote.
        Returns:
            item (dict): columnas de lote_items
    '''
    return {
        'uri': uri or f'spotify:track:{uid("t")}',
        'album': album,
        'artist_name': artist_name,
        'number': number,
        'release_date': release_date,
        'artist_ids': list(artist_ids) if artist_ids is not None else None,
        'status': status,
    }


def add_lote(db, canal, week_start, items, delivered=False):
    '''
    Lote cerrado del Canal con sus Tracks.
        Returns:
            lote_id (int): id del Lote
    '''
    lote_id = db.execute(
        "INSERT INTO lotes (canal_id, week_start, status, closed_at, delivered_at) VALUES (%s, %s, 'cerrado', now(), CASE WHEN %s THEN now() END) RETURNING id",
        (canal['id'], week_start, delivered),
    ).fetchone()[0]

    for entry in items:
        db.execute(
            '''
            INSERT INTO lote_items (lote_id, track_uri, album_id, artist_name, disc_number, track_number, release_date, status, artist_ids)
            VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s)
            ''',
            (lote_id, entry['uri'], entry['album'], entry['artist_name'], entry['number'], entry['release_date'], entry['status'], entry['artist_ids']),
        )

    db.commit()
    return lote_id


def playlist(canal, total=0):
    '''
    La Target como viene en GET /me/playlists.
        Returns:
            playlist (dict): playlist simplificada
    '''
    return {'id': canal['target_playlist_id'], 'name': canal['target_name'], 'items': {'total': total}}


def mock_post(spotify, canal, *responses):
    '''
    POST de items a la Target; sin respuestas explicitas siempre responde 201.
        Returns:
            route (respx.Route): ruta para leer los bodies
    '''
    route = spotify.post(f'{API_URL}/playlists/{canal["target_playlist_id"]}/items')

    if responses:
        return route.mock(side_effect=list(responses))

    return route.respond(201, json={'snapshot_id': 's'})


def mock_contains(spotify, saved):
    '''
    GET /me/library/contains que responde segun un set de URIs guardados.
        Returns:
            route (respx.Route): ruta para contar llamadas
    '''
    def answer(request):
        uris = request.url.params['uris'].split(',')
        return httpx.Response(200, json=[uri in saved for uri in uris])

    return spotify.get(f'{API_URL}/me/library/contains').mock(side_effect=answer)


def bodies(route):
    return [json.loads(call.request.content) for call in route.calls]


def statuses(db, lote_id):
    return dict(db.execute('SELECT track_uri, status FROM lote_items WHERE lote_id = %s', (lote_id,)).fetchall())


def delivered_at(db, lote_id):
    return db.execute('SELECT delivered_at FROM lotes WHERE id = %s', (lote_id,)).fetchone()[0]


def test_bot_playlist_gets_one_post_on_top_in_lote_order(db, spotify):
    canal = new_canal(db, artists=('a1', 'b1'))
    old_b = item('B', 'b-old', 1, '2026-09-27', ['b1'])
    new_b = [item('B', 'b-new', n, '2026-10-01', ['b1']) for n in (2, 1)]
    a = item('a', 'a', 1, '2026-09-28', ['a1'])
    lote_id = add_lote(db, canal, SATURDAY, [old_b, *new_b, a])
    post = mock_post(spotify, canal)

    entregas.deliver(db, 'token', canal, playlist(canal, total=10))

    # artista asc sin importar mayusculas, sus Releases del mas nuevo al mas viejo, cada disco en su orden
    assert bodies(post) == [{'uris': [a['uri'], new_b[1]['uri'], new_b[0]['uri'], old_b['uri']], 'position': 0}]
    assert set(statuses(db, lote_id).values()) == {'insertado'}
    assert delivered_at(db, lote_id) is not None
    assert db.execute('SELECT target_total FROM canales WHERE id = %s', (canal['id'],)).fetchone()[0] == 14


def test_existing_playlist_appends_in_lote_order(db, spotify):
    canal = new_canal(db, bot=False)
    items = [item(number=n) for n in (1, 2)]
    add_lote(db, canal, SATURDAY, items)
    post = mock_post(spotify, canal)

    entregas.deliver(db, 'token', canal, playlist(canal))

    assert bodies(post) == [{'uris': [items[0]['uri'], items[1]['uri']]}]


def test_bot_playlist_sends_last_chunk_first(db, spotify):
    canal = new_canal(db)
    items = [item(number=n) for n in range(1, 151)]
    add_lote(db, canal, SATURDAY, items)
    post = mock_post(spotify, canal)

    entregas.deliver(db, 'token', canal, playlist(canal))

    uris = [entry['uri'] for entry in items]
    assert bodies(post) == [{'uris': uris[100:], 'position': 0}, {'uris': uris[:100], 'position': 0}]


def test_half_done_entrega_resumes_with_what_is_missing(db, spotify):
    canal = new_canal(db)
    items = [item(number=n) for n in range(1, 151)]
    lote_id = add_lote(db, canal, SATURDAY, items)
    mock_post(spotify, canal, httpx.Response(201, json={}), httpx.Response(500))

    with pytest.raises(httpx.HTTPStatusError):
        entregas.deliver(db, 'token', canal, playlist(canal))

    assert delivered_at(db, lote_id) is None
    assert list(statuses(db, lote_id).values()).count('insertado') == 50

    # respx devuelve la misma ruta con la respuesta nueva; reset limpia las llamadas del primer intento
    post = mock_post(spotify, canal)
    post.reset()
    entregas.deliver(db, 'token', canal, playlist(canal, total=50))

    assert bodies(post) == [{'uris': [entry['uri'] for entry in items[:100]], 'position': 0}]
    assert delivered_at(db, lote_id) is not None
    assert db.execute('SELECT attempt_count FROM lotes WHERE id = %s', (lote_id,)).fetchone()[0] == 2


def test_release_fully_liked_is_skipped(db, spotify):
    canal = new_canal(db, guardado=True)
    single = item(album='single')
    album = [item(album='album', number=n) for n in (1, 2)]
    lote_id = add_lote(db, canal, SATURDAY, [single, *album])
    mock_contains(spotify, {single['uri'], album[0]['uri']})
    post = mock_post(spotify, canal)

    entregas.deliver(db, 'token', canal, playlist(canal))

    # un Release se saltea solo si todos sus Tracks estan guardados
    assert bodies(post) == [{'uris': [album[0]['uri'], album[1]['uri']], 'position': 0}]
    assert statuses(db, lote_id)[single['uri']] == 'descartado'


def test_guardado_off_does_not_ask_spotify(db, spotify):
    canal = new_canal(db, guardado=False)
    add_lote(db, canal, SATURDAY, [item()])
    contains = mock_contains(spotify, set())
    mock_post(spotify, canal)

    entregas.deliver(db, 'token', canal, playlist(canal))

    assert contains.call_count == 0


def test_artist_out_of_whitelist_is_dropped(db, spotify):
    canal = new_canal(db, artists=('a1',))
    kept = item(artist_ids=['a1', 'x'])
    gone = item(number=2, artist_ids=['b1'])
    legacy = item(number=3, artist_ids=None)
    lote_id = add_lote(db, canal, SATURDAY, [kept, gone, legacy])
    mock_post(spotify, canal)

    entregas.deliver(db, 'token', canal, playlist(canal))

    # sin artist_ids (filas de antes de #16) no se filtra
    assert statuses(db, lote_id) == {kept['uri']: 'insertado', gone['uri']: 'descartado', legacy['uri']: 'insertado'}


def test_missing_target_deletes_the_canal(db, spotify):
    canal = new_canal(db)
    add_lote(db, canal, SATURDAY, [item()])
    mock_contains(spotify, set())
    post = mock_post(spotify, canal)

    entregas.deliver(db, 'token', canal, None)

    assert post.call_count == 0
    assert db.execute('SELECT 1 FROM canales WHERE id = %s', (canal['id'],)).fetchone() is None


def test_target_out_of_the_list_but_in_library_is_delivered(db, spotify):
    canal = new_canal(db)
    lote_id = add_lote(db, canal, SATURDAY, [item()])
    mock_contains(spotify, {f'spotify:playlist:{canal["target_playlist_id"]}'})
    mock_post(spotify, canal)

    entregas.deliver(db, 'token', canal, None)

    assert delivered_at(db, lote_id) is not None


def test_empty_lote_is_delivered_without_spotify(db, spotify):
    canal = new_canal(db)
    lote_id = add_lote(db, canal, SATURDAY, [])

    entregas.deliver(db, 'token', canal, None)

    assert delivered_at(db, lote_id) is not None


def test_undelivered_lotes_go_out_together(db, spotify):
    canal = new_canal(db)
    first = [item(number=1, status='insertado'), item(number=2)]
    second = [item(number=3)]
    old_id = add_lote(db, canal, date(2026, 9, 19), first)
    lote_id = add_lote(db, canal, SATURDAY, second)
    post = mock_post(spotify, canal)

    entregas.deliver(db, 'token', canal, playlist(canal))

    assert db.execute('SELECT 1 FROM lotes WHERE id = %s', (old_id,)).fetchone() is None
    assert bodies(post) == [{'uris': [first[1]['uri'], second[0]['uri']], 'position': 0}]
    assert set(statuses(db, lote_id).values()) == {'insertado'}


def test_retencion_retires_old_lotes_but_not_uris_still_kept(db, spotify):
    canal = new_canal(db, retencion_n=1)
    shared = 'spotify:track:shared'
    old_items = [item(status='insertado'), item(number=2, status='insertado', uri=shared)]
    old_id = add_lote(db, canal, date(2026, 9, 19), old_items, delivered=True)
    lote_id = add_lote(db, canal, SATURDAY, [item(number=3, uri=shared)])
    mock_post(spotify, canal)
    delete = spotify.delete(f'{API_URL}/playlists/{canal["target_playlist_id"]}/items').respond(200, json={'snapshot_id': 's'})

    entregas.deliver(db, 'token', canal, playlist(canal, total=2))

    # DELETE borra todas las copias: el URI que sigue en el Lote nuevo no se manda
    assert bodies(delete) == [{'items': [{'uri': old_items[0]['uri']}]}]
    assert set(statuses(db, old_id).values()) == {'retirado'}
    assert statuses(db, lote_id) == {shared: 'insertado'}


def test_lote_where_nothing_entered_does_not_count_for_retencion(db, spotify):
    canal = new_canal(db, retencion_n=1, artists=('a1',))
    old_id = add_lote(db, canal, date(2026, 9, 19), [item(status='insertado')], delivered=True)
    add_lote(db, canal, SATURDAY, [item(artist_ids=['gone'])])
    delete = spotify.delete(f'{API_URL}/playlists/{canal["target_playlist_id"]}/items').respond(200, json={})

    entregas.deliver(db, 'token', canal, playlist(canal, total=1))

    assert delete.call_count == 0
    assert set(statuses(db, old_id).values()) == {'insertado'}


def test_existing_playlist_drops_what_does_not_fit(db, spotify):
    canal = new_canal(db, bot=False)
    items = [item(number=n) for n in (1, 2)]
    lote_id = add_lote(db, canal, SATURDAY, items)
    post = mock_post(spotify, canal)

    entregas.deliver(db, 'token', canal, playlist(canal, total=9999))

    assert bodies(post) == [{'uris': [items[0]['uri']]}]
    assert statuses(db, lote_id)[items[1]['uri']] == 'descartado'


def test_bot_playlist_is_trimmed_from_its_oldest_lote(db, spotify):
    canal = new_canal(db)
    oldest = add_lote(db, canal, date(2026, 9, 12), [item(status='insertado') for _ in range(5)], delivered=True)
    middle = add_lote(db, canal, date(2026, 9, 19), [item(status='insertado') for _ in range(5)], delivered=True)
    add_lote(db, canal, SATURDAY, [item() for _ in range(3)])
    mock_post(spotify, canal)
    spotify.delete(f'{API_URL}/playlists/{canal["target_playlist_id"]}/items').respond(200, json={})

    entregas.deliver(db, 'token', canal, playlist(canal, total=entregas.BOT_CAP))

    assert set(statuses(db, oldest).values()) == {'retirado'}
    assert set(statuses(db, middle).values()) == {'insertado'}
    assert db.execute('SELECT target_total FROM canales WHERE id = %s', (canal['id'],)).fetchone()[0] == entregas.BOT_CAP - 2


def test_failed_entrega_fails_the_run_without_blocking_other_canales(db, spotify):
    canal = new_canal(db)
    other_id = canales.create_canal(db, canal['user_id'], uid('p'), 'Otro', True)
    other = canales.get_canal(db, canal['user_id'], other_id)
    db.commit()
    broken = add_lote(db, canal, SATURDAY, [item()])
    fine = add_lote(db, other, SATURDAY, [item()])
    spotify.post(f'{ACCOUNTS_URL}/api/token').respond(json={'access_token': 'token'})
    spotify.get(f'{DEEZER_URL}/search/artist').respond(json={'data': []})
    spotify.get(f'{API_URL}/me/playlists').respond(json={'items': [playlist(canal), playlist(other)], 'next': None})
    mock_post(spotify, canal, httpx.Response(500))
    mock_post(spotify, other)

    runs.run_user(db, users.get_user(db, canal['user_id']), FRIDAY)

    assert delivered_at(db, broken) is None
    assert delivered_at(db, fine) is not None
    assert db.execute('SELECT status FROM runs WHERE user_id = %s AND date = %s', (canal['user_id'], FRIDAY)).fetchone()[0] == 'fallido'


def test_cap_warnings():
    def canal(total, bot):
        return {'target_name': 'Radar', 'target_total': total, 'target_created_by_bot': bot}

    warnings = web.get_cap_warnings([canal(8999, False), canal(9000, False), canal(9600, False), canal(9025, True)])

    assert [warning['level'] for warning in warnings] == ['warn', 'crit', 'crit']
    assert warnings[0]['text'].startswith('Radar esta al 90% del tope de 10.000')
