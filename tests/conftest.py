import os
import uuid

import pytest
import respx
from psycopg import sql

from app.db import connect
from app.migrate import apply_migrations


@pytest.fixture(scope='session')
def db_schema():
    '''
    Crea un schema efimero en la DB de DATABASE_URL con las migraciones aplicadas y lo borra al terminar la sesion.
        Returns:
            schema (str): nombre del schema efimero
    '''
    # sin DB configurada (ej: CI sin secret) los tests de DB se saltean en vez de fallar
    if not os.environ.get('DATABASE_URL'):
        pytest.skip('sin DATABASE_URL')

    schema = f'test_{uuid.uuid4().hex[:12]}'
    schema_id = sql.Identifier(schema)

    with connect() as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(schema_id))

    try:
        with connect(search_path=schema) as conn:
            apply_migrations(conn)
        yield schema
    finally:
        with connect() as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(schema_id))


@pytest.fixture
def db(db_schema):
    '''
    Conexion al schema efimero; todo lo que hace el test se descarta con rollback al final.
        Returns:
            conn (psycopg.Connection): conexion sin commitear
    '''
    conn = connect(search_path=db_schema)

    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def spotify():
    '''
    Intercepta todo request HTTP de httpx; un request sin ruta definida en el test falla.
        Returns:
            router (respx.Router): router donde cada test define las respuestas de Spotify
    '''
    with respx.mock(assert_all_called=False) as router:
        yield router
