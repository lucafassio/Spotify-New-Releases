from urllib.parse import urlencode

import httpx

API_URL = 'https://api.spotify.com/v1'
ACCOUNTS_URL = 'https://accounts.spotify.com'

# playlists (ADR 0003/0005), Guardado y borrar la Target al desvincular (ADR 0005), follows como Artist Source
SCOPES = [
    'playlist-read-private',
    'playlist-read-collaborative',
    'playlist-modify-private',
    'playlist-modify-public',
    'user-library-read',
    'user-library-modify',
    'user-follow-read',
]


class TokenRejected(Exception):

    def __init__(self, error):
        '''
        Error del token endpoint de Spotify con su codigo OAuth.
            Args:
                error (str): codigo devuelto por Spotify, ej: invalid_grant
        '''
        super().__init__(error)
        self.error = error


class NotRegistered(Exception):
    pass


def build_authorize_url(client_id, redirect_uri, state):
    '''
    Arma la URL de autorizacion de Spotify a la que mandamos al User.
        Args:
            client_id (str): client id de la Spotify App
            redirect_uri (str): callback del servidor
            state (str): valor anti CSRF que guardamos en la sesion
        Returns:
            authorize_url (str): url de accounts.spotify.com
    '''
    params = {
        'response_type': 'code',
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'scope': ' '.join(SCOPES),
        'state': state,
    }
    authorize_url = f'{ACCOUNTS_URL}/authorize?{urlencode(params)}'
    return authorize_url


def request_token(client_id, client_secret, form):
    '''
    Pide tokens al token endpoint y traduce los rechazos OAuth a TokenRejected.
        Args:
            client_id (str): client id de la Spotify App
            client_secret (str): client secret de la Spotify App
            form (dict): cuerpo del request segun el grant_type
        Returns:
            tokens (dict): respuesta de Spotify con access_token y a veces refresh_token
    '''
    response = httpx.post(f'{ACCOUNTS_URL}/api/token', data=form, auth=(client_id, client_secret))

    # 400 y 401 traen un codigo OAuth que decide si el User queda Desconectado (#19); el resto es transitorio
    if response.status_code in (400, 401):
        try:
            error = response.json().get('error', 'unknown')
        except ValueError:
            error = 'unknown'
        raise TokenRejected(error)

    response.raise_for_status()
    tokens = response.json()
    return tokens


def exchange_code(client_id, client_secret, code, redirect_uri):
    '''
    Canjea el code del callback por tokens.
        Args:
            client_id (str): client id de la Spotify App
            client_secret (str): client secret de la Spotify App
            code (str): code que Spotify manda al callback
            redirect_uri (str): el mismo redirect usado al autorizar
        Returns:
            tokens (dict): access_token y refresh_token
    '''
    form = {'grant_type': 'authorization_code', 'code': code, 'redirect_uri': redirect_uri}
    tokens = request_token(client_id, client_secret, form)
    return tokens


def refresh_access_token(client_id, client_secret, refresh_token):
    '''
    Pide un access token nuevo a partir del refresh token.
        Args:
            client_id (str): client id de la Spotify App
            client_secret (str): client secret de la Spotify App
            refresh_token (str): refresh token en claro
        Returns:
            tokens (dict): access_token y, si Spotify lo rota, un refresh_token nuevo
    '''
    form = {'grant_type': 'refresh_token', 'refresh_token': refresh_token}
    tokens = request_token(client_id, client_secret, form)
    return tokens


def get_current_user(access_token):
    '''
    Lee el perfil del User duenio del token.
        Args:
            access_token (str): access token vigente
        Returns:
            profile (dict): respuesta de GET /me (id, display_name, images)
    '''
    response = httpx.get(f'{API_URL}/me', headers={'Authorization': f'Bearer {access_token}'})

    # en development mode Spotify responde 403 a quien no esta en la allowlist de la Spotify App
    if response.status_code == 403:
        raise NotRegistered()

    response.raise_for_status()
    profile = response.json()
    return profile
