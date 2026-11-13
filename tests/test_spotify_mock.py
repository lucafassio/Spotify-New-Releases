import time
from threading import Lock

import httpx
import pytest
import respx

from app import spotify as spotify_api
from app.spotify import API_URL


def test_mocked_route_answers(spotify):
    spotify.get(f'{API_URL}/me').respond(json={'id': 'luca'})

    response = httpx.get(f'{API_URL}/me')

    assert response.json() == {'id': 'luca'}


def test_unmocked_request_fails(spotify):
    with pytest.raises(respx.models.AllMockedAssertionError):
        httpx.get(f'{API_URL}/artists/x/albums')


# Lectura de playlists por pagina (#24)


def page_route(spotify, playlist_id, total, fail=None, delay=0):
    '''
    Mockea los items de una playlist de `total` canciones, respondiendo cada pagina segun su offset.
        Args:
            fail (dict): offset -> respuesta o excepcion para esa pagina
            delay (float): segundos que tarda cada pagina, para medir la concurrencia
        Returns:
            state (dict): route, offsets pedidos y maximo de paginas en vuelo a la vez
    '''
    state = {'offsets': [], 'running': 0, 'max_running': 0}
    lock = Lock()

    def answer(request):
        offset = int(request.url.params.get('offset', 0))
        with lock:
            state['offsets'].append(offset)
            state['running'] += 1
            state['max_running'] = max(state['max_running'], state['running'])
        try:
            time.sleep(delay)
            failure = (fail or {}).get(offset)
            if isinstance(failure, Exception):
                raise failure
            if failure is not None:
                return failure
            items = [{'item': {'type': 'track', 'uri': f'spotify:track:t{n}', 'artists': [{'id': f'a{n}', 'name': f'A{n}'}]}} for n in range(offset, min(offset + 50, total))]
            following = f'{API_URL}/playlists/{playlist_id}/items?offset={offset + 50}&limit=50' if offset + 50 < total else None
            return httpx.Response(200, json={'items': items, 'next': following, 'total': total})
        finally:
            with lock:
                state['running'] -= 1

    state['route'] = spotify.get(f'{API_URL}/playlists/{playlist_id}/items').mock(side_effect=answer)
    return state


def uris(entries):
    '''
    URIs de items de playlist, para comparar el orden.
        Returns:
            uris (list): uri de cada item
    '''
    return [spotify_api.get_entry_uri(entry) for entry in entries]


def test_web_reads_pages_by_offset_four_at_a_time(spotify):
    state = page_route(spotify, 'grande', 430, delay=0.05)

    with spotify_api.request_context():
        entries = spotify_api.get_playlist_entries('token', 'grande', total=430)

    assert uris(entries) == [f'spotify:track:t{n}' for n in range(430)]
    assert sorted(state['offsets']) == list(range(0, 430, 50))
    assert 1 < state['max_running'] <= spotify_api.PAGE_WORKERS == 4


def test_web_reads_from_an_offset_in_parallel(spotify):
    state = page_route(spotify, 'grande', 230)

    with spotify_api.request_context():
        entries = spotify_api.get_playlist_entries('token', 'grande', 99, total=230)

    assert uris(entries) == [f'spotify:track:t{n}' for n in range(99, 230)]
    assert sorted(state['offsets']) == [99, 149, 199]


def test_run_reads_pages_in_line_without_deadline(spotify):
    state = page_route(spotify, 'grande', 230)

    entries = spotify_api.get_playlist_entries('token', 'grande', total=230)

    assert len(entries) == 230
    assert state['offsets'] == [0, 50, 100, 150, 200]
    assert state['max_running'] == 1


def test_web_follows_next_when_the_playlist_grew(spotify):
    state = page_route(spotify, 'grande', 120)

    with spotify_api.request_context():
        entries = spotify_api.get_playlist_entries('token', 'grande', total=60)

    assert len(entries) == 120
    assert sorted(state['offsets']) == [0, 50, 100]


@pytest.mark.parametrize('body,error', [
    ({'error': {'status': 429}}, spotify_api.RateLimited),
    ({'error': {'status': 429, 'reason': 'QUOTA_EXCEEDED'}}, spotify_api.QuotaExceeded),
])
def test_rate_limit_stops_the_whole_read(spotify, body, error):
    limited = httpx.Response(429, headers={'Retry-After': '30'}, json=body)
    state = page_route(spotify, 'grande', 1000, fail={100: limited}, delay=0.02)
    spotify_api.clear_cooldowns()

    try:
        with spotify_api.request_context(), pytest.raises(error):
            spotify_api.get_playlist_entries('token', 'grande', total=1000)
    finally:
        spotify_api.clear_cooldowns()

    # las paginas que ya estaban en vuelo terminan, las demas ni salen
    assert len(state['offsets']) < 20


def test_deadline_keeps_the_complete_pages_in_order(spotify):
    state = page_route(spotify, 'grande', 300, fail={100: httpx.ReadTimeout('lento')})

    with spotify_api.request_context(), pytest.raises(spotify_api.PartialRead) as cut:
        spotify_api.get_playlist_entries('token', 'grande', total=300)

    # la pagina 150 pudo llegar, pero sin la 100 en el medio no se puede seguir desde ahi
    assert uris(cut.value.entries) == [f'spotify:track:t{n}' for n in range(100)]
    assert isinstance(cut.value, httpx.TimeoutException)
    assert 0 in state['offsets']


def test_expired_deadline_reads_nothing(spotify):
    state = page_route(spotify, 'grande', 300)

    with spotify_api.request_context():
        spotify_api.request_deadline.set(time.monotonic() - 1)
        with pytest.raises(spotify_api.PartialRead) as cut:
            spotify_api.get_playlist_entries('token', 'grande', total=300)

    assert cut.value.entries == []
    assert state['offsets'] == []
