import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.pq import TransactionStatus

from app import db as database
from app import web


def test_pool_reuses_connections_and_rolls_back_after_failure(db_schema):
    # db_schema garantiza que la suite tiene una DB de pruebas configurada
    database.close_web_pool()
    pool = database.get_web_pool()

    try:
        with pool.connection() as conn:
            first_pid = conn.execute('SELECT pg_backend_pid()').fetchone()[0]
            assert conn.execute('SHOW statement_timeout').fetchone()[0] == '5s'
            assert conn.execute('SHOW lock_timeout').fetchone()[0] == '2s'
        with pytest.raises(psycopg.errors.DivisionByZero):
            with pool.connection() as conn:
                conn.execute('SELECT 1 / 0')
        with pool.connection() as conn:
            assert conn.info.transaction_status == TransactionStatus.IDLE
            assert conn.execute('SELECT pg_backend_pid()').fetchone()[0] == first_pid
            assert conn.execute('SELECT 1').fetchone()[0] == 1
    finally:
        database.close_web_pool()


def test_public_login_does_not_open_database(monkeypatch):
    def forbidden():
        raise AssertionError('El login publico no necesita DB')

    monkeypatch.setattr(web, 'get_web_pool', forbidden)
    with TestClient(web.app) as client:
        client.cookies.set('own_app', 'known-client')
        response = client.get('/')
    assert response.status_code == 200
    assert 'Conectar con Spotify' in response.text


def test_database_busy_does_not_redirect_into_a_loop(monkeypatch):
    def busy():
        raise web.PoolTimeout()

    monkeypatch.setattr(web, 'get_web_pool', busy)
    with TestClient(web.app) as client:
        response = client.get('/canales/1', follow_redirects=False)
    assert response.status_code == 503
    assert 'Volver a intentar' in response.text
    assert 'location' not in response.headers
