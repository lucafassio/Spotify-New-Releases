import psycopg
from psycopg import sql

from app.config import get_database_url


def connect(database_url=None, search_path=None):
    '''
    Abre una conexion a Postgres, opcionalmente apuntada a un schema que no es public.
        Args:
            database_url (str): url de conexion, si falta se lee del entorno
            search_path (str): schema a usar en vez de public, lo usan los tests
        Returns:
            conn (psycopg.Connection): conexion abierta
    '''
    conn = psycopg.connect(database_url or get_database_url())

    # SET en vez de options=-c porque el pooler de Neon rechaza parametros de arranque
    if search_path:
        conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(search_path)))
        conn.commit()

    return conn
