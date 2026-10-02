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


def call_api(method, access_token, url, **kwargs):
    '''
    Llama a la Web API con el token del User y falla si Spotify no responde 2xx.
        Args:
            method (str): verbo HTTP
            access_token (str): access token vigente
            url (str): path bajo API_URL o url completa (los `next` de la paginacion)
            **kwargs: params o json para httpx
        Returns:
            response (httpx.Response): respuesta exitosa
    '''
    if url.startswith('/'):
        url = f'{API_URL}{url}'

    response = httpx.request(method, url, headers={'Authorization': f'Bearer {access_token}'}, **kwargs)
    response.raise_for_status()
    return response


def get_pages(access_token, path, params, key=None):
    '''
    Recorre una paginacion de Spotify siguiendo `next` hasta el final.
        Args:
            access_token (str): access token vigente
            path (str): path de la primera pagina
            params (dict): params de la primera pagina; las siguientes los traen en `next`
            key (str): clave que envuelve al paging object, ej: artists en /me/following
        Returns:
            items (list): items de todas las paginas
    '''
    items = []
    url = path

    while url:
        page = call_api('GET', access_token, url, params=params).json()
        page = page[key] if key else page
        items.extend(page['items'])
        url = page.get('next')
        params = None

    return items


def get_my_playlists(access_token):
    '''
    Lista las playlists de la biblioteca del User, propias y ajenas.
        Args:
            access_token (str): access token vigente
        Returns:
            playlists (list): playlists simplificadas de GET /me/playlists
    '''
    playlists = get_pages(access_token, '/me/playlists', {'limit': 50})
    return playlists


def get_playlist_artists(access_token, playlist_id):
    '''
    Junta los artistas de una playlist propia o colaborativa: el primero de cada track es principal, el resto feats.
        Args:
            access_token (str): access token vigente
            playlist_id (str): id de Spotify de la playlist
        Returns:
            artists (dict): artist_id -> {name, feat}; feat es False si fue principal en algun track
    '''
    artists = {}

    for entry in get_pages(access_token, f'/playlists/{playlist_id}/items', {'limit': 50}):
        # feb 2026 renombro track a item; episodios y archivos locales no aportan artistas
        track = entry.get('item') or entry.get('track')
        if not track or track.get('type') != 'track' or track.get('is_local'):
            continue

        for position, artist in enumerate(track['artists']):
            if not artist.get('id'):
                continue

            feat = position > 0
            known = artists.get(artist['id'])
            if known is None or (known['feat'] and not feat):
                artists[artist['id']] = {'name': artist['name'], 'feat': feat}

    return artists


def get_followed_artists(access_token):
    '''
    Lista los artistas que el User sigue en Spotify.
        Args:
            access_token (str): access token vigente
        Returns:
            artists (dict): artist_id -> {name, feat}; feat siempre False
    '''
    items = get_pages(access_token, '/me/following', {'type': 'artist', 'limit': 50}, key='artists')
    artists = {artist['id']: {'name': artist['name'], 'feat': False} for artist in items}
    return artists


def search_artists(access_token, query):
    '''
    Busca artistas por nombre para la lista manual.
        Args:
            access_token (str): access token vigente
            query (str): texto que escribio el User
        Returns:
            artists (list): hasta 10 artistas con id y name (tope de /search desde feb 2026)
    '''
    data = call_api('GET', access_token, '/search', params={'q': query, 'type': 'artist', 'limit': 10}).json()
    artists = [{'id': artist['id'], 'name': artist['name']} for artist in data['artists']['items']]
    return artists


def create_playlist(access_token, name):
    '''
    Crea una playlist privada en la biblioteca del User para usarla de Target.
        Args:
            access_token (str): access token vigente
            name (str): nombre que eligio el User
        Returns:
            playlist (dict): playlist creada, con id y name
    '''
    body = {'name': name, 'public': False, 'description': 'Lo nuevo de tus artistas, cada viernes. Radar de Viernes.'}
    playlist = call_api('POST', access_token, '/me/playlists', json=body).json()
    return playlist


def remove_playlist(access_token, playlist_id):
    '''
    Saca una playlist de la biblioteca del User, que en Spotify es como borrarla (ADR 0005).
        Args:
            access_token (str): access token vigente
            playlist_id (str): id de Spotify de la playlist
    '''
    call_api('DELETE', access_token, '/me/library', params={'uris': f'spotify:playlist:{playlist_id}'})
