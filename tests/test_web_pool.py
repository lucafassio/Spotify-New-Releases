import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.pq import TransactionStatus

from app import db as database
from app import web
from test_web_performance import round_trips  # noqa: F401


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

    monkeypatch.setattr(database, 'get_web_pool', forbidden)
    with TestClient(web.app) as client:
        client.cookies.set('own_app', 'known-client')
        response = client.get('/')
    assert response.status_code == 200
    assert 'Conectar con Spotify' in response.text


def test_database_busy_does_not_redirect_into_a_loop(monkeypatch):
    def busy():
        raise web.PoolTimeout()

    monkeypatch.setattr(database, 'get_web_pool', busy)
    with TestClient(web.app) as client:
        response = client.get('/canales/1', follow_redirects=False)
    assert response.status_code == 503
    assert 'Volver a intentar' in response.text
    assert 'location' not in response.headers


def test_new_connection_costs_no_setup_statements(db_schema, round_trips):  # noqa: F811
    database.close_web_pool()
    pool = database.get_web_pool()

    try:
        with pool.connection() as conn:
            opened = list(round_trips)
            timeouts = [conn.execute('SHOW statement_timeout').fetchone()[0], conn.execute('SHOW lock_timeout').fetchone()[0]]
    finally:
        database.close_web_pool()

    assert opened == []
    assert timeouts == ['5s', '2s']
    assert pool.max_idle == 300


def test_pool_checks_only_connections_left_idle(db_schema, round_trips):  # noqa: F811
    database.close_web_pool()

    try:
        with database.web_connection() as conn:
            conn.execute('SELECT 1')
        round_trips.clear()
        with database.web_connection() as conn:
            recent = list(round_trips)
        # como si hubiera quedado quieta mas que CHECK_AFTER: Neon pudo cortarla al dormir
        conn.last_used = 0
        with database.web_connection() as conn:
            idle = list(round_trips)
    finally:
        database.close_web_pool()

    assert recent == []
    assert idle == ['']


def test_read_only_connection_goes_back_transactional(db_schema):
    database.close_web_pool()

    try:
        with database.web_connection(autocommit=True) as conn:
            conn.execute('SELECT 1')
        with database.get_web_pool().connection() as conn:
            autocommit = conn.autocommit
    finally:
        database.close_web_pool()

    assert autocommit is False


@pytest.mark.parametrize('autocommit', [True, False])
def test_atomic_keeps_writes_together(db, autocommit):
    db.execute('CREATE TEMP TABLE atomic_probe (n int)')
    db.commit()
    db.autocommit = autocommit

    with pytest.raises(psycopg.errors.DivisionByZero):
        with database.atomic(db):
            db.execute('INSERT INTO atomic_probe VALUES (1)')
            db.execute('SELECT 1 / 0')
    if not autocommit:
        db.rollback()
    with database.atomic(db):
        db.execute('INSERT INTO atomic_probe VALUES (2)')

    # atomic ya dejo todo commiteado: la conexion queda sin transaccion abierta
    assert db.info.transaction_status == TransactionStatus.IDLE
    assert db.execute('SELECT array_agg(n) FROM atomic_probe').fetchone()[0] == [2]


def test_neon_pooler_sets_the_limits_with_set():
    direct = database.get_connection_settings('postgresql://u:p@ep-x-1.us-east-2.aws.neon.tech/db?sslmode=require')
    pooled = database.get_connection_settings('postgresql://u:p@ep-x-1-pooler.us-east-2.aws.neon.tech/db?sslmode=require')

    assert direct == ({'connect_timeout': 5, 'options': database.WEB_OPTIONS}, database.mark_used)
    assert pooled == ({'connect_timeout': 5}, database.configure_pooled_connection)


def test_pooled_setup_leaves_the_limits_and_no_transaction(db):
    db.commit()
    database.configure_pooled_connection(db)

    assert db.info.transaction_status == TransactionStatus.IDLE
    assert [db.execute('SHOW statement_timeout').fetchone()[0], db.execute('SHOW lock_timeout').fetchone()[0]] == ['5s', '2s']
