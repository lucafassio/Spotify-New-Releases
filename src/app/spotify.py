import atexit
import hashlib
import time
from collections import OrderedDict
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from threading import Lock
from urllib.parse import urlencode

import httpx

API_URL = 'https://api.spotify.com/v1'
ACCOUNTS_URL = 'https://accounts.spotify.com'

# playlists (ADR 0003/0005), Guardado y borrar la Target al desvincular (ADR 0005)
SCOPES = [
    'playlist-read-private',
    'playlist-read-collaborative',
    'playlist-modify-private',
    'playlist-modify-public',
    'user-library-read',
    'user-library-modify',
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


class QuotaExceeded(Exception):
    pass


class RateLimited(Exception):
    pass


class PartialRead(httpx.TimeoutException):

    def __init__(self, entries):
        '''
        El tope de una accion web corto la lectura de una playlist; quien no la espera la ve como cualquier timeout.
            Args:
                entries (list): items de las paginas completas leidas, seguidas desde el offset pedido
        '''
        super().__init__('Spotify tardo demasiado. Proba de nuevo.')
        self.entries = entries


request_deadline = ContextVar('spotify_request_deadline', default=None)
client_lock = Lock()
http_client = None
cooldowns = OrderedDict()
cooldown_lock = Lock()


@contextmanager
def request_context(interactive=True):
    '''
    Limita el tiempo de las lecturas interactivas sin cambiar los reintentos del Tick.
        Args:
            interactive (bool): True para requests web, False para Runs
    '''
    token = request_deadline.set(time.monotonic() + 8 if interactive else None)
    try:
        yield
    finally:
        request_deadline.reset(token)


def get_http_client():
    '''
    Reutiliza las conexiones HTTPS sin guardar autorizacion compartida entre Users.
        Returns:
            client (httpx.Client): cliente del proceso
    '''
    global http_client
    with client_lock:
        if http_client is None or http_client.is_closed:
            http_client = httpx.Client(timeout=5, limits=httpx.Limits(max_connections=20, max_keepalive_connections=10))
        return http_client


def close_http_client():
    '''Cierra las conexiones HTTP del proceso.'''
    global http_client
    with client_lock:
        if http_client is not None:
            http_client.close()
            http_client = None


atexit.register(close_http_client)


def clear_cooldowns():
    '''Descarta los tiempos de espera locales al cerrar la web.'''
    with cooldown_lock:
        cooldowns.clear()


def request_timeout():
    '''
    Calcula el timeout restante de una accion web antes de iniciar otra pagina.
        Returns:
            timeout (float): segundos disponibles para la siguiente peticion
    '''
    deadline = request_deadline.get()
    if deadline is None:
        return 5
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise httpx.TimeoutException('Spotify tardo demasiado. Proba de nuevo.')
    return min(5, remaining)


def retry_after(response):
    '''
    Lee los segundos de espera sin dejar que un header invalido rompa la web.
        Args:
            response (httpx.Response): respuesta limitada
        Returns:
            seconds (int): espera no negativa
    '''
    try:
        return max(1, int(response.headers.get('retry-after', 1)))
    except (ValueError, TypeError):
        return 1


# un 429 comun es el rate limit de 30 s y se espera; uno con QUOTA_EXCEEDED es la quota diaria de la cuenta de developer (ADR 0007)
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_MAX_WAIT = 30

# topes por request de GET /me/library/contains y de POST/DELETE /playlists/{id}/items
LIBRARY_CHUNK = 40
ITEMS_CHUNK = 100

# items por pagina de GET /playlists/{id}/items y paginas en vuelo a la vez en la web (#24)
PAGE_SIZE = 50
PAGE_WORKERS = 4

# requests a la Web API hechos por este proceso, reintentos incluidos; el Tick loguea cuanto gasto cada Run (#17)
api_calls = 0


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
    response = get_http_client().post(f'{ACCOUNTS_URL}/api/token', data=form, auth=(client_id, client_secret), timeout=request_timeout())

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


def check_client(client_id, client_secret):
    '''
    Prueba que un client id y su secret sean validos antes de mandar al User a autorizar; no gasta quota de la Web API.
        Args:
            client_id (str): client id de la Spotify App
            client_secret (str): client secret de la Spotify App
        Returns:
            valid (bool): False si Spotify rechaza el client (invalid_client)
    '''
    try:
        request_token(client_id, client_secret, {'grant_type': 'client_credentials'})
    except TokenRejected as error:
        if error.error != 'invalid_client':
            raise
        return False

    return True


def get_current_user(access_token):
    '''
    Lee el perfil del User duenio del token.
        Args:
            access_token (str): access token vigente
        Returns:
            profile (dict): respuesta de GET /me (id, display_name, images)
    '''
    response = get_http_client().get(f'{API_URL}/me', headers={'Authorization': f'Bearer {access_token}'}, timeout=request_timeout())

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
    global api_calls

    if url.startswith('/'):
        url = f'{API_URL}{url}'

    interactive = request_deadline.get() is not None
    token_key = hashlib.sha256(access_token.encode()).digest()
    if interactive:
        with cooldown_lock:
            cached = cooldowns.get(token_key)
            if cached and cached[0] > time.monotonic():
                raise cached[1](max(1, int(cached[0] - time.monotonic())))
            cooldowns.pop(token_key, None)

    for attempt in range(RATE_LIMIT_RETRIES + 1):
        api_calls += 1
        response = get_http_client().request(method, url, headers={'Authorization': f'Bearer {access_token}'}, timeout=request_timeout(), **kwargs)

        if response.status_code != 429:
            break

        quota_exceeded = is_quota_exceeded(response)
        wait = retry_after(response)
        if interactive:
            error_class = QuotaExceeded if quota_exceeded else RateLimited
            with cooldown_lock:
                cooldowns[token_key] = (time.monotonic() + wait, error_class)
                cooldowns.move_to_end(token_key)

                while len(cooldowns) > 128:
                    cooldowns.popitem(last=False)
            raise error_class(wait)

        if quota_exceeded:
            raise QuotaExceeded(response.headers.get('retry-after'))

        if attempt < RATE_LIMIT_RETRIES:
            time.sleep(min(wait, RATE_LIMIT_MAX_WAIT))

    response.raise_for_status()
    return response


def is_quota_exceeded(response):
    '''
    Distingue la quota agotada (no sirve reintentar en el dia) del rate limit comun.
        Args:
            response (httpx.Response): respuesta 429 de Spotify
        Returns:
            exceeded (bool): True si el body trae reason QUOTA_EXCEEDED
    '''
    try:
        exceeded = response.json().get('error', {}).get('reason') == 'QUOTA_EXCEEDED'
    except ValueError:
        exceeded = False

    return exceeded


def get_pages(access_token, path, params):
    '''
    Recorre una paginacion de Spotify siguiendo `next` hasta el final.
        Args:
            access_token (str): access token vigente
            path (str): path de la primera pagina
            params (dict): params de la primera pagina; las siguientes los traen en `next`
        Returns:
            items (list): items de todas las paginas
    '''
    items = []
    url = path

    while url:
        page = call_api('GET', access_token, url, params=params).json()
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


def get_playlist_entries(access_token, playlist_id, offset=0, total=None):
    '''
    Lee los items de una playlist propia o colaborativa, desde el principio o desde un offset; en la web, con el total conocido, pide las paginas en paralelo.
        Args:
            access_token (str): access token vigente
            playlist_id (str): id de Spotify de la playlist
            offset (int): posicion del primer item a leer
            total (int): canciones de la playlist segun GET /me/playlists, o None si no se sabe
        Returns:
            entries (list): items de GET /playlists/{id}/items
    '''
    path = f'/playlists/{playlist_id}/items'

    # el Run no tiene tope de tiempo: sigue leyendo en fila, con sus reintentos
    if total is None or request_deadline.get() is None:
        params = {'limit': PAGE_SIZE, 'offset': offset} if offset else {'limit': PAGE_SIZE}
        entries = get_pages(access_token, path, params)
        return entries

    entries = get_pages_by_offset(access_token, path, offset, total)
    return entries


def get_page(access_token, path, offset):
    '''
    Pide una pagina de items por su offset.
        Args:
            access_token (str): access token vigente
            path (str): path de la paginacion
            offset (int): posicion del primer item de la pagina
        Returns:
            page (dict): pagina de Spotify con items y next
    '''
    params = {'limit': PAGE_SIZE, 'offset': offset} if offset else {'limit': PAGE_SIZE}
    page = call_api('GET', access_token, path, params=params).json()
    return page


def get_pages_by_offset(access_token, path, offset, total):
    '''
    Pide las paginas de a PAGE_WORKERS a la vez; si el tope corta, conserva las completas seguidas desde el principio.
        Args:
            access_token (str): access token vigente
            path (str): path de la paginacion
            offset (int): posicion del primer item a leer
            total (int): canciones que se espera que tenga la playlist
        Returns:
            entries (list): items de todas las paginas, en orden
    '''
    offsets = range(offset, max(total, offset + 1), PAGE_SIZE)
    pool = ThreadPoolExecutor(max_workers=PAGE_WORKERS)

    try:
        # request_deadline es un ContextVar: cada pagina corre con una copia del contexto del request
        futures = [pool.submit(copy_context().run, get_page, access_token, path, page_offset) for page_offset in offsets]
        wait(futures, return_when=FIRST_EXCEPTION)
    finally:
        # ante el primer error las que no salieron no salen; las que estan en vuelo terminan solas por el tope o el cooldown
        pool.shutdown(wait=True, cancel_futures=True)

    entries = []
    for future in futures:
        if future.cancelled() or future.exception() is not None:
            break
        page = future.result()
        entries.extend(page['items'])

    errors = [future.exception() for future in futures if not future.cancelled() and future.exception() is not None]

    # un 429 corta todo y manda sobre el resto de los errores, como en la lectura en fila
    for error in errors:
        if isinstance(error, (RateLimited, QuotaExceeded)):
            raise error
    for error in errors:
        if not isinstance(error, httpx.TimeoutException):
            raise error
    if errors or any(future.cancelled() for future in futures):
        raise PartialRead(entries)

    # la playlist pudo crecer despues de leer la biblioteca: lo que sobra sigue por next
    url = page.get('next')
    while url:
        try:
            page = call_api('GET', access_token, url).json()
        except httpx.TimeoutException:
            raise PartialRead(entries) from None
        entries.extend(page['items'])
        url = page.get('next')

    return entries


def get_entry_track(entry):
    '''
    Track de un item de playlist; feb 2026 renombro track a item.
        Args:
            entry (dict): item de GET /playlists/{id}/items
        Returns:
            track (dict): track o episodio, o None si Spotify no lo devuelve
    '''
    track = entry.get('item') or entry.get('track')
    return track


def get_entry_uri(entry):
    '''
    URI de un item de playlist, para reconocerlo despues aunque no aporte artistas.
        Args:
            entry (dict): item de GET /playlists/{id}/items
        Returns:
            uri (str): uri del track o episodio, o None si Spotify no lo devuelve
    '''
    track = get_entry_track(entry)
    uri = track.get('uri') if track else None
    return uri


def collect_artists(entries, artists=None):
    '''
    Junta los artistas de items de playlist: el primero de cada track es principal, el resto feats.
        Args:
            entries (list): items de GET /playlists/{id}/items
            artists (dict): artistas ya conocidos, a los que se suman estos
        Returns:
            artists (dict): artist_id -> {name, feat, isrc}; feat es False si fue principal en algun track, isrc es de un track donde figura
    '''
    artists = {} if artists is None else artists

    for entry in entries:
        # episodios y archivos locales no aportan artistas
        track = get_entry_track(entry)
        if not track or track.get('type') != 'track' or track.get('is_local'):
            continue

        for position, artist in enumerate(track['artists']):
            if not artist.get('id'):
                continue

            feat = position > 0
            isrc = track.get('external_ids', {}).get('isrc')
            known = artists.get(artist['id'])

            if known is None or (known['feat'] and not feat):
                # el ISRC sirve para vincular al artista con Deezer (ADR 0007), cualquier track donde figure alcanza
                artists[artist['id']] = {'name': artist['name'], 'feat': feat, 'isrc': isrc or (known or {}).get('isrc')}
            elif known['isrc'] is None:
                known['isrc'] = isrc

    return artists


def search_artists(access_token, query):
    '''
    Busca artistas por nombre para la lista manual.
        Args:
            access_token (str): access token vigente
            query (str): texto que escribio el User
        Returns:
            artists (list): hasta 10 artistas con id, name e image (tope de /search desde feb 2026)
    '''
    data = call_api('GET', access_token, '/search', params={'q': query, 'type': 'artist', 'limit': 10}).json()
    artists = [{'id': artist['id'], 'name': artist['name'], 'image': get_image_url(artist.get('images'), 52)} for artist in data['artists']['items']]
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


def find_album_by_upc(access_token, upc):
    '''
    Busca en Spotify el album de un UPC; es como pasamos un Release de Deezer a Spotify (ADR 0007).
        Args:
            access_token (str): access token vigente
            upc (str): codigo de barras del album
        Returns:
            album (dict): album simplificado con id, name y artists, o None si Spotify no lo tiene
    '''
    data = call_api('GET', access_token, '/search', params={'q': f'upc:{upc}', 'type': 'album', 'limit': 1}).json()
    items = data['albums']['items']
    album = items[0] if items else None
    return album


def get_album(access_token, album_id):
    '''
    Lee un album con todos sus tracks en su orden original.
        Args:
            access_token (str): access token vigente
            album_id (str): id de Spotify del album
        Returns:
            album (dict): album con artists y tracks (lista de tracks simplificados con uri, artists, disc_number y track_number)
    '''
    album = call_api('GET', access_token, f'/albums/{album_id}').json()
    tracks = album['tracks']['items']

    # GET /albums trae los primeros 50 tracks; un album mas largo sigue paginando
    if album['tracks'].get('next'):
        tracks = tracks + get_pages(access_token, album['tracks']['next'], None)

    album['tracks'] = tracks
    return album


def get_track_total(playlist):
    '''
    Cantidad de canciones de una playlist simplificada; feb 2026 renombro tracks a items.
        Args:
            playlist (dict): playlist de GET /me/playlists
        Returns:
            total (int): canciones de la playlist
    '''
    collection = playlist.get('items') or playlist.get('tracks') or {}
    total = collection.get('total', 0)
    return total


def get_image_url(images, size):
    '''
    Elige la imagen mas chica que no se ve borrosa al tamanio pedido; Spotify las lista de la mas grande a la mas chica.
        Args:
            images (list): images de un artista o una playlist, puede venir None
            size (int): lado minimo en px
        Returns:
            url (str): url de la imagen, o None si no hay ninguna
    '''
    if not images:
        return None

    # una portada subida por el User viene sola y sin medidas
    fits = [image for image in images if (image.get('width') or 0) >= size]
    image = min(fits, key=lambda image: image['width']) if fits else images[0]
    return image['url']


def check_library(access_token, uris):
    '''
    Pregunta que URIs estan en la biblioteca del User: tracks en liked, playlists que sigue o creo.
        Args:
            access_token (str): access token vigente
            uris (list): URIs de Spotify, de a 40 por llamada (tope de GET /me/library/contains)
        Returns:
            saved (dict): uri -> True si esta en la biblioteca
    '''
    saved = {}

    for start in range(0, len(uris), LIBRARY_CHUNK):
        chunk = uris[start:start + LIBRARY_CHUNK]
        flags = call_api('GET', access_token, '/me/library/contains', params={'uris': ','.join(chunk)}).json()
        saved.update(zip(chunk, flags))

    return saved


def add_items(access_token, playlist_id, uris, position=None):
    '''
    Agrega hasta 100 Tracks a una playlist en un solo POST; los que entran juntos se ordenan por posicion en todas las vistas.
        Args:
            access_token (str): access token vigente
            playlist_id (str): id de Spotify de la playlist
            uris (list): hasta 100 URIs en el orden en que deben quedar
            position (int): donde insertar; None agrega al final
    '''
    body = {'uris': uris}
    if position is not None:
        body['position'] = position

    call_api('POST', access_token, f'/playlists/{playlist_id}/items', json=body)


def remove_items(access_token, playlist_id, uris):
    '''
    Saca hasta 100 URIs de una playlist; Spotify borra todas las copias de cada uno.
        Args:
            access_token (str): access token vigente
            playlist_id (str): id de Spotify de la playlist
            uris (list): hasta 100 URIs
    '''
    call_api('DELETE', access_token, f'/playlists/{playlist_id}/items', json={'items': [{'uri': uri} for uri in uris]})
