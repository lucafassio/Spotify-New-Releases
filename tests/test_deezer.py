import httpx
import pytest

from app import deezer
from app.deezer import API_URL


def test_artist_albums_follow_pages(spotify):
    second = f'{API_URL}/artist/7/albums?index=100&limit=100'
    spotify.get(second).respond(json={'data': [{'id': 2}], 'total': 2})
    spotify.get(f'{API_URL}/artist/7/albums').respond(json={'data': [{'id': 1}], 'total': 2, 'next': second})

    assert [album['id'] for album in deezer.get_artist_albums(7)] == [1, 2]


def test_quota_error_waits_and_retries(spotify, monkeypatch):
    waits = []
    monkeypatch.setattr(deezer.time, 'sleep', waits.append)
    spotify.get(f'{API_URL}/album/5').mock(side_effect=[
        deezer_error(4, 'Quota limit exceeded'),
        deezer_error(4, 'Quota limit exceeded'),
        httpx.Response(200, json={'id': 5, 'upc': '123'}),
    ])

    assert deezer.get_album_upc(5) == '123'
    assert waits == [deezer.QUOTA_WAIT, deezer.QUOTA_WAIT]


def test_other_errors_raise(spotify):
    spotify.get(f'{API_URL}/album/5').mock(return_value=deezer_error(300, 'Invalid OAuth'))

    with pytest.raises(deezer.DeezerError):
        deezer.get_album_upc(5)


def test_unknown_isrc_is_none(spotify):
    spotify.get(f'{API_URL}/track/isrc:XX').mock(return_value=deezer_error(800, 'no data'))

    assert deezer.find_artist_by_isrc('XX', 'Duki') is None


def test_isrc_picks_the_artist_by_name_among_contributors(spotify):
    spotify.get(f'{API_URL}/track/isrc:AA').respond(json={
        'id': 1,
        'artist': {'id': 10, 'name': 'Hanzel La H'},
        'contributors': [{'id': 10, 'name': 'Hanzel La H'}, {'id': 20, 'name': 'Myke Towers'}],
    })

    assert deezer.find_artist_by_isrc('AA', 'myke  towers') == 20


def test_name_search_picks_exact_match_with_most_fans(spotify):
    spotify.get(f'{API_URL}/search/artist').respond(json={'data': [
        {'id': 1, 'name': 'Emilia', 'nb_fan': 30},
        {'id': 2, 'name': 'Emilia Mernes', 'nb_fan': 900},
        {'id': 3, 'name': 'EMILIA', 'nb_fan': 500},
    ]})

    assert deezer.find_artist_by_name('Emilia') == 3


def test_name_search_without_exact_match_is_none(spotify):
    spotify.get(f'{API_URL}/search/artist').respond(json={'data': [{'id': 2, 'name': 'Emilia Mernes', 'nb_fan': 900}]})

    assert deezer.find_artist_by_name('Emilia') is None


# Fotos desde la web (#23)

PICTURE = 'https://cdn-images.dzcdn.net/images/artist/abc/56x56-000000-80-0-0.jpg'


def test_web_reuses_one_client_and_never_sleeps(spotify, monkeypatch):
    monkeypatch.setattr(deezer.time, 'sleep', lambda seconds: pytest.fail('la web no duerme esperando a Deezer'))
    route = spotify.get(f'{API_URL}/artist/27').mock(return_value=deezer_error(4, 'Quota limit exceeded'))

    with pytest.raises(deezer.DeezerBusy):
        deezer.find_picture_url('Duki', deezer_id=27)

    assert route.call_count == 1
    assert deezer.get_web_client() is deezer.get_web_client()


def test_linked_artist_picture_comes_from_its_id(spotify):
    spotify.get(f'{API_URL}/artist/27').respond(json={'id': 27, 'name': 'Duki', 'picture_small': PICTURE})

    assert deezer.find_picture_url('Duki', deezer_id=27) == PICTURE


def test_unlinked_artist_falls_back_from_isrc_to_name(spotify):
    spotify.get(f'{API_URL}/track/isrc:AR9').respond(json={'artist': {'id': 1, 'name': 'Otro'}, 'contributors': [{'id': 1, 'name': 'Otro'}]})
    spotify.get(f'{API_URL}/search/artist').respond(json={'data': [{'id': 2, 'name': 'Duki', 'nb_fan': 9, 'picture_small': PICTURE}]})

    assert deezer.find_picture_url('Duki', 'AR9') == PICTURE


def test_empty_picture_is_none(spotify):
    empty = 'https://cdn-images.dzcdn.net/images/artist//56x56-000000-80-0-0.jpg'
    spotify.get(f'{API_URL}/search/artist').respond(json={'data': [{'id': 2, 'name': 'Duki', 'picture_small': empty}]})

    assert deezer.find_picture_url('Duki') is None


def test_quota_stops_the_rest_of_the_batch(spotify, monkeypatch):
    monkeypatch.setattr(deezer, 'PHOTO_WORKERS', 1)
    spotify.get(f'{API_URL}/artist/1').respond(json={'id': 1, 'picture_small': PICTURE})
    spotify.get(f'{API_URL}/artist/2').mock(return_value=deezer_error(4, 'Quota limit exceeded'))
    after = spotify.get(f'{API_URL}/artist/3').respond(json={'id': 3, 'picture_small': PICTURE})
    batch = [{'artist_id': f'a{n}', 'name': 'x', 'deezer_artist_id': n} for n in (1, 2, 3)]

    # lo que se encontro antes del corte vuelve; lo que falta lo pide el navegador mas tarde
    assert deezer.find_pictures(batch) == {'a1': PICTURE}
    assert after.call_count == 0


def test_network_error_stops_the_batch_and_odd_answers_skip_one(spotify, monkeypatch):
    monkeypatch.setattr(deezer, 'PHOTO_WORKERS', 1)
    spotify.get(f'{API_URL}/artist/1').mock(return_value=deezer_error(300, 'raro'))
    spotify.get(f'{API_URL}/artist/2').respond(json={'id': 2, 'picture_small': PICTURE})
    spotify.get(f'{API_URL}/artist/3').mock(side_effect=httpx.ConnectTimeout('lento'))
    after = spotify.get(f'{API_URL}/artist/4').respond(json={'id': 4, 'picture_small': PICTURE})
    batch = [{'artist_id': f'a{n}', 'name': 'x', 'deezer_artist_id': n} for n in (1, 2, 3, 4)]

    assert deezer.find_pictures(batch) == {'a2': PICTURE}
    assert after.call_count == 0


def deezer_error(code, message):
    '''
    Respuesta de error de Deezer, que viene con status 200.
        Returns:
            response (httpx.Response): respuesta con el error en el body
    '''
    return httpx.Response(200, json={'error': {'type': 'Exception', 'message': message, 'code': code}})
