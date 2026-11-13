import time
from contextlib import contextmanager
from threading import Lock

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.pq import TransactionStatus
from psycopg_pool import ConnectionPool

from app.config import get_database_url

web_pool = None
pool_lock = Lock()

# limites de las conexiones interactivas, como parametros de arranque: el host directo de Neon los acepta y no cuestan sentencias al abrir
WEB_OPTIONS = '-c statement_timeout=5000 -c lock_timeout=2000'
# Neon duerme tras 5 min sin consultas y una conexion abierta sin transaccion no lo despierta: el pool la guarda ese tiempo
WEB_MAX_IDLE = 300
# una conexion usada hace menos que esto sigue viva: se presta sin ir a la base a comprobarlo
CHECK_AFTER = 60


def mark_used(conn):
    '''
    Anota cuando se uso la conexion por ultima vez, para saber si hace falta comprobarla al prestarla.
        Args:
            conn (psycopg.Connection): conexion recien abierta o recien usada
    '''
    conn.last_used = time.monotonic()


def configure_pooled_connection(conn):
    '''
    Fija los limites con SET al abrir, para el pooler de Neon que rechaza parametros de arranque.
        Args:
            conn (psycopg.Connection): conexion recien abierta
    '''
    conn.autocommit = True
    conn.execute("SET statement_timeout = '5s'; SET lock_timeout = '2s'")
    conn.autocommit = False
    mark_used(conn)


def get_connection_settings(database_url):
    '''
    Elige como fijar los limites de las conexiones interactivas segun el host.
        Args:
            database_url (str): url de conexion
        Returns:
            kwargs (dict): parametros de conexion
            configure (callable): lo que corre el pool al abrir cada conexion
    '''
    if '-pooler' in (conninfo_to_dict(database_url).get('host') or ''):
        return {'connect_timeout': 5}, configure_pooled_connection
    return {'connect_timeout': 5, 'options': WEB_OPTIONS}, mark_used


def check_web_connection(conn):
    '''
    Comprueba una conexion al prestarla solo si estuvo quieta un rato: Neon pudo haberla cortado al dormir.
        Args:
            conn (psycopg.Connection): conexion que sale del pool
    '''
    if time.monotonic() - getattr(conn, 'last_used', 0) < CHECK_AFTER:
        return
    ConnectionPool.check_connection(conn)


def get_web_pool():
    '''
    Abre un pool pequeno solo cuando la web necesita DB, sin mantener Neon despierto en reposo.
        Returns:
            pool (ConnectionPool): hasta cuatro conexiones reutilizables
    '''
    global web_pool
    with pool_lock:
        if web_pool is None:
            database_url = get_database_url()
            kwargs, configure = get_connection_settings(database_url)
            web_pool = ConnectionPool(
                database_url, min_size=0, max_size=4, max_idle=WEB_MAX_IDLE,
                timeout=5, max_waiting=16, open=True,
                kwargs=kwargs, check=check_web_connection, configure=configure,
            )
        return web_pool


@contextmanager
def web_connection(autocommit=False):
    '''
    Presta una conexion del pool para un request; commitea al terminar si no hubo error.
        Args:
            autocommit (bool): True para un request que solo lee: no paga BEGIN ni COMMIT
        Returns:
            conn (psycopg.Connection): conexion abierta
    '''
    with get_web_pool().connection() as conn:
        conn.autocommit = autocommit
        try:
            yield conn
        finally:
            mark_used(conn)
            # vuelve al pool en modo transaccional, el que espera cualquier otro codigo que la tome
            if conn.autocommit and conn.info.transaction_status == TransactionStatus.IDLE:
                conn.autocommit = False


def close_web_pool():
    '''Libera las conexiones de la web al cerrar el servidor.'''
    global web_pool
    with pool_lock:
        if web_pool is not None:
            web_pool.close()
            web_pool = None


@contextmanager
def atomic(conn):
    '''
    Agrupa escrituras que tienen que quedar juntas, tambien en una conexion de solo lectura en autocommit.
        Args:
            conn (psycopg.Connection): conexion del request
    '''
    if conn.autocommit:
        with conn.transaction():
            yield
        return

    yield
    conn.commit()


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
