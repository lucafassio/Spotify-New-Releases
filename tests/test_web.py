from fastapi.testclient import TestClient

from app.web import app

client = TestClient(app)


def test_home_renders_with_htmx():
    response = client.get('/')

    assert response.status_code == 200
    assert 'Spotify New Releases' in response.text
    assert 'htmx.org' in response.text


def test_health_is_ok():
    response = client.get('/health')

    assert response.status_code == 200
    assert response.text == 'ok'
