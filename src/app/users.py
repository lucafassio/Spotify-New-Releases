from psycopg.rows import dict_row

from app import spotify
from app.config import get_shared_app
from app.crypto import decrypt_text, encrypt_text

USER_COLUMNS = 'id, spotify_user_id, connection_mode, client_id, client_secret_enc, refresh_token_enc, disconnected_at, created_at'


class UserDisconnected(Exception):
    pass


def get_user(conn, user_id):
    '''
    Busca un User por id.
        Args:
            conn (psycopg.Connection): conexion abierta
            user_id (int): id interno del User
        Returns:
            user (dict): fila de users, o None si no existe
    '''
    user = conn.cursor(row_factory=dict_row).execute(f'SELECT {USER_COLUMNS} FROM users WHERE id = %s', (user_id,)).fetchone()
    return user


def save_login(conn, spotify_user_id, refresh_token, own_app=None):
    '''
    Crea o reconecta al User que acaba de autorizar, con su refresh token y su client cifrados.
        Args:
            conn (psycopg.Connection): conexion abierta, el commit queda a cargo del que llama
            spotify_user_id (str): id de la cuenta de Spotify
            refresh_token (str): refresh token en claro recien canjeado
            own_app (dict): client_id y client_secret_enc de su Own App; None si entro por la Shared App
        Returns:
            login (dict): user_id y disconnected_at previo (None si no estaba Desconectado)
    '''
    previous = conn.execute('SELECT disconnected_at FROM users WHERE spotify_user_id = %s', (spotify_user_id,)).fetchone()

    mode = 'own' if own_app else 'shared'
    client_id = own_app['client_id'] if own_app else None
    client_secret_enc = own_app['client_secret_enc'] if own_app else None

    # el refresh token queda atado al client que lo emitio: entrar por el otro modo pisa el modo y las claves
    user_id = conn.execute(
        '''
        INSERT INTO users (spotify_user_id, connection_mode, client_id, client_secret_enc, refresh_token_enc)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (spotify_user_id) DO UPDATE
        SET connection_mode = EXCLUDED.connection_mode, client_id = EXCLUDED.client_id, client_secret_enc = EXCLUDED.client_secret_enc,
            refresh_token_enc = EXCLUDED.refresh_token_enc, disconnected_at = NULL
        RETURNING id
        ''',
        (spotify_user_id, mode, client_id, client_secret_enc, encrypt_text(refresh_token)),
    ).fetchone()[0]

    login = {'user_id': user_id, 'disconnected_at': previous[0] if previous else None}
    return login


def save_shared_login(conn, spotify_user_id, refresh_token):
    '''
    Crea o reconecta al User que entro por la Shared App.
        Args:
            conn (psycopg.Connection): conexion abierta, el commit queda a cargo del que llama
            spotify_user_id (str): id de la cuenta de Spotify
            refresh_token (str): refresh token en claro recien canjeado
        Returns:
            login (dict): user_id y disconnected_at previo (None si no estaba Desconectado)
    '''
    login = save_login(conn, spotify_user_id, refresh_token)
    return login


def find_own_app(conn, client_id):
    '''
    Busca las claves guardadas de una Own App por su client id, para reconocer a quien vuelve (#18).
        Args:
            conn (psycopg.Connection): conexion abierta
            client_id (str): client id de la Own App
        Returns:
            own_app (dict): client_id y client_secret_enc, o None si ningun User la usa
    '''
    row = conn.execute(
        "SELECT client_id, client_secret_enc FROM users WHERE connection_mode = 'own' AND client_id = %s ORDER BY id LIMIT 1",
        (client_id,),
    ).fetchone()
    own_app = {'client_id': row[0], 'client_secret_enc': row[1]} if row else None
    return own_app


def get_client_credentials(user):
    '''
    Devuelve el client con el que se autorizo el User: la Shared App o su Own App.
        Args:
            user (dict): fila de users
        Returns:
            credentials (tuple): client_id y client_secret en claro
    '''
    if user['connection_mode'] == 'own':
        credentials = (user['client_id'], decrypt_text(user['client_secret_enc']))
    else:
        shared_app = get_shared_app()
        credentials = (shared_app['client_id'], shared_app['client_secret'])

    return credentials


def mark_disconnected(conn, user_id):
    '''
    Marca al User como Desconectado y borra su refresh token muerto (enmienda ADR 0006).
        Args:
            conn (psycopg.Connection): conexion abierta
            user_id (int): id interno del User
    '''
    conn.execute('UPDATE users SET disconnected_at = now(), refresh_token_enc = NULL WHERE id = %s', (user_id,))


def get_access_token(conn, user):
    '''
    Consigue un access token vigente para el User refrescando su refresh token guardado.
        Args:
            conn (psycopg.Connection): conexion abierta; commitea lo que escribe para que sobreviva aunque el Run aborte
            user (dict): fila de users
        Returns:
            access_token (str): token para llamar a la Web API
    '''
    if user['refresh_token_enc'] is None:
        raise UserDisconnected()

    client_id, client_secret = get_client_credentials(user)

    try:
        tokens = spotify.refresh_access_token(client_id, client_secret, decrypt_text(user['refresh_token_enc']))
    except spotify.TokenRejected as error:
        # invalid_client de la Shared App es error nuestro: no se castiga al User, que el Tick falle ruidoso (#19)
        if error.error == 'invalid_client' and user['connection_mode'] == 'shared':
            raise RuntimeError('Spotify rechaza el client de la Shared App, revisar SPOTIFY_CLIENT_ID/SECRET') from error

        if error.error in ('invalid_grant', 'invalid_client'):
            mark_disconnected(conn, user['id'])
            conn.commit()
            raise UserDisconnected() from error

        raise

    # si Spotify rota el refresh token y no guardamos el nuevo, el proximo refresh da un invalid_grant falso (#19)
    if tokens.get('refresh_token'):
        conn.execute('UPDATE users SET refresh_token_enc = %s WHERE id = %s', (encrypt_text(tokens['refresh_token']), user['id']))
        conn.commit()

    access_token = tokens['access_token']
    return access_token
