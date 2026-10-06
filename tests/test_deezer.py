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


def deezer_error(code, message):
    '''
    Respuesta de error de Deezer, que viene con status 200.
        Returns:
            response (httpx.Response): respuesta con el error en el body
    '''
    return httpx.Response(200, json={'error': {'type': 'Exception', 'message': message, 'code': code}})
