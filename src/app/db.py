from threading import Lock

import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool

from app.config import get_database_url

web_pool = None
pool_lock = Lock()


def configure_web_connection(conn):
    '''Limita consultas y esperas por locks solo en las conexiones interactivas.'''
    conn.execute("SET statement_timeout = '5s'")
    conn.execute("SET lock_timeout = '2s'")
    conn.commit()


def get_web_pool():
    '''
    Abre un pool pequeno solo cuando la web necesita DB, sin mantener Neon despierto en reposo.
        Returns:
            pool (ConnectionPool): hasta cuatro conexiones reutilizables
    '''
    global web_pool
    with pool_lock:
        if web_pool is None:
            web_pool = ConnectionPool(
                get_database_url(), min_size=0, max_size=4, max_idle=60,
                timeout=5, max_waiting=16, open=True,
                kwargs={'connect_timeout': 5}, check=ConnectionPool.check_connection,
                configure=configure_web_connection,
            )
        return web_pool


def close_web_pool():
    '''Libera las conexiones de la web al cerrar el servidor.'''
    global web_pool
    with pool_lock:
        if web_pool is not None:
            web_pool.close()
            web_pool = None


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
