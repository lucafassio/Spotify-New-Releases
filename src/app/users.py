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


def save_shared_login(conn, spotify_user_id, refresh_token):
    '''
    Crea o reconecta al User que entro por la Shared App, con su refresh token cifrado.
        Args:
            conn (psycopg.Connection): conexion abierta, el commit queda a cargo del que llama
            spotify_user_id (str): id de la cuenta de Spotify
            refresh_token (str): refresh token en claro recien canjeado
        Returns:
            login (dict): user_id y disconnected_at previo (None si no estaba Desconectado)
    '''
    previous = conn.execute('SELECT disconnected_at FROM users WHERE spotify_user_id = %s', (spotify_user_id,)).fetchone()

    # el refresh token queda atado al client que lo emitio: si antes entraba por Own App pasa a shared
    user_id = conn.execute(
        '''
        INSERT INTO users (spotify_user_id, connection_mode, refresh_token_enc)
        VALUES (%s, 'shared', %s)
        ON CONFLICT (spotify_user_id) DO UPDATE
        SET connection_mode = 'shared', refresh_token_enc = EXCLUDED.refresh_token_enc, disconnected_at = NULL
        RETURNING id
        ''',
        (spotify_user_id, encrypt_text(refresh_token)),
    ).fetchone()[0]

    login = {'user_id': user_id, 'disconnected_at': previous[0] if previous else None}
    return login


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
