import httpx
import pytest
import respx

from app.spotify import API_URL


def test_mocked_route_answers(spotify):
    spotify.get(f'{API_URL}/me').respond(json={'id': 'luca'})

    response = httpx.get(f'{API_URL}/me')

    assert response.json() == {'id': 'luca'}


def test_unmocked_request_fails(spotify):
    with pytest.raises(respx.models.AllMockedAssertionError):
        httpx.get(f'{API_URL}/artists/x/albums')
