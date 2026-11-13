import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

API_URL = 'https://api.deezer.com'

# Deezer corta con code 4 si pasamos ~50 requests cada 5 s; esperamos la ventana y reintentamos
QUOTA_CODE = 4
QUOTA_WAIT = 5
QUOTA_RETRIES = 3

# code 800 es "no data": un ISRC o un id que Deezer no conoce
NO_DATA_CODE = 800

# la web nunca espera a Deezer (#23): conexion reutilizada, timeout corto, sin reintentos y pocas fotos a la vez
WEB_TIMEOUT = 4
PHOTO_WORKERS = 4
web_client = None
web_client_lock = threading.Lock()


# requests hechos por este proceso desde varios hilos; el Tick loguea cuanto gasto cada Run (#17)
api_calls = 0
api_calls_lock = threading.Lock()


class DeezerError(Exception):
    pass


class DeezerBusy(DeezerError):
    '''Deezer limito un pedido de la web; se reintenta mas tarde en vez de dormir el request.'''


def get_web_client():
    '''
    Cliente HTTP compartido por los requests de la web, para no abrir una conexion por foto.
        Returns:
            client (httpx.Client): cliente con keepalive y timeout corto
    '''
    global web_client
    with web_client_lock:
        if web_client is None:
            web_client = httpx.Client(timeout=WEB_TIMEOUT, limits=httpx.Limits(max_connections=PHOTO_WORKERS))
        return web_client


def call_api(url, params=None, web=False):
    '''
    Llama a la API publica de Deezer, que no pide token y devuelve los errores con status 200.
        Args:
            url (str): path bajo API_URL o url completa (los `next` de la paginacion)
            params (dict): query params
            web (bool): True desde la web: cliente compartido y DeezerBusy en vez de esperar la cuota
        Returns:
            data (dict): respuesta de Deezer, o None si no conoce lo que pedimos
    '''
    global api_calls

    if url.startswith('/'):
        url = f'{API_URL}{url}'

    for attempt in range(QUOTA_RETRIES + 1):
        with api_calls_lock:
            api_calls += 1

        response = get_web_client().get(url, params=params) if web else httpx.get(url, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
        error = data.get('error') if isinstance(data, dict) else None

        if not error:
            return data

        if error.get('code') == NO_DATA_CODE:
            return None

        if error.get('code') == QUOTA_CODE and web:
            raise DeezerBusy(f'{error.get("code")} {error.get("message")}')

        if error.get('code') == QUOTA_CODE and attempt < QUOTA_RETRIES:
            time.sleep(QUOTA_WAIT)
            continue

        raise DeezerError(f'{error.get("code")} {error.get("message")}')


def get_pages(path, params):
    '''
    Recorre una paginacion de Deezer siguiendo `next` hasta el final.
        Args:
            path (str): path de la primera pagina
            params (dict): params de la primera pagina; las siguientes los traen en `next`
        Returns:
            items (list): items de todas las paginas
    '''
    items = []
    url = path

    while url:
        page = call_api(url, params)

        if page is None:
            break

        items.extend(page.get('data', []))
        url = page.get('next')
        params = None

    return items


def get_artist_albums(artist_id):
    '''
    Lista todo lo que Deezer publica de un artista: discos, EPs, singles y discos ajenos donde esta acreditado.
        Args:
            artist_id (int): id de Deezer del artista
        Returns:
            albums (list): albums con id, title, release_date, record_type y explicit_lyrics
    '''
    # el orden viene agrupado por tipo y no siempre por fecha, asi que leemos todas las paginas
    albums = get_pages(f'/artist/{artist_id}/albums', {'limit': 100})
    return albums


def get_album_upc(album_id):
    '''
    Devuelve el UPC de un album, que es lo que lo identifica en Spotify.
        Args:
            album_id (int): id de Deezer del album
        Returns:
            upc (str): codigo de barras, o None si Deezer no lo tiene
    '''
    album = call_api(f'/album/{album_id}')
    upc = album.get('upc') if album else None
    return upc


def match_artist_by_isrc(isrc, name, web=False):
    '''
    Busca en Deezer el track de un ISRC y devuelve el artista del track con ese nombre.
        Args:
            isrc (str): ISRC de un Track donde figura el artista
            name (str): nombre del artista en Spotify
            web (bool): True si la llama la web, ver call_api
        Returns:
            artist (dict): artista de Deezer con id y fotos, o None si no aparece
    '''
    track = call_api(f'/track/isrc:{isrc}', web=web)
    if track is None:
        return None

    # el ISRC identifica el track; el artista lo elegimos por nombre entre principal e invitados
    candidates = track.get('contributors') or [track['artist']]
    wanted = normalize_name(name)
    artist = next((artist for artist in candidates if normalize_name(artist['name']) == wanted), None)
    return artist


def match_artist_by_name(name, web=False):
    '''
    Busca un artista por nombre exacto; entre homonimos gana el de mas fans.
        Args:
            name (str): nombre del artista en Spotify
            web (bool): True si la llama la web, ver call_api
        Returns:
            artist (dict): artista de Deezer con id y fotos, o None si ninguno coincide
    '''
    data = call_api('/search/artist', {'q': name, 'limit': 10}, web=web)
    wanted = normalize_name(name)
    exact = [artist for artist in (data or {}).get('data', []) if normalize_name(artist['name']) == wanted]

    if not exact:
        return None

    artist = max(exact, key=lambda artist: artist.get('nb_fan', 0))
    return artist


def find_artist_by_isrc(isrc, name):
    '''
    Id de Deezer del artista de un ISRC con ese nombre.
        Args:
            isrc (str): ISRC de un Track donde figura el artista
            name (str): nombre del artista en Spotify
        Returns:
            artist_id (int): id de Deezer del artista, o None si no aparece
    '''
    artist = match_artist_by_isrc(isrc, name)
    return artist['id'] if artist else None


def find_artist_by_name(name):
    '''
    Id de Deezer del artista con ese nombre exacto.
        Args:
            name (str): nombre del artista en Spotify
        Returns:
            artist_id (int): id de Deezer del artista, o None si ninguno coincide
    '''
    artist = match_artist_by_name(name)
    return artist['id'] if artist else None


def find_picture_url(name, isrc=None, deezer_id=None):
    '''
    Foto chica de un artista para la web: por su id si el Run ya lo vinculo, si no buscandolo como el Run, ISRC y despues nombre.
        Args:
            name (str): nombre del artista en Spotify
            isrc (str): ISRC de un Track suyo, o None
            deezer_id (int): id de Deezer si el Run lo vinculo, o None
        Returns:
            url (str): url final del CDN de la foto de 56 px, o None si no aparece o no tiene foto
    '''
    if deezer_id:
        artist = call_api(f'/artist/{deezer_id}', web=True)
    else:
        artist = (match_artist_by_isrc(isrc, name, web=True) if isrc else None) or match_artist_by_name(name, web=True)
    url = (artist or {}).get('picture_small')

    # sin foto Deezer arma la url con el hash vacio, que muestra una silueta generica
    if not url or '/artist//' in url:
        return None
    return url


def find_picture_or_stop(artist, stop):
    '''
    Busca la foto de un artista de una tanda, salvo que la tanda ya se haya cortado.
        Args:
            artist (dict): artist_id, name y opcionales isrc y deezer_artist_id
            stop (threading.Event): se prende si Deezer limita o no responde, para cortar el resto
        Returns:
            found (tuple): (artist_id, url o None), o None si no se pudo buscar
    '''
    if stop.is_set():
        return None

    try:
        url = find_picture_url(artist['name'], artist.get('isrc'), artist.get('deezer_artist_id'))
    except (DeezerBusy, httpx.HTTPError):
        stop.set()
        return None
    except (DeezerError, KeyError, TypeError, ValueError):
        # una respuesta rara de un artista no corta la tanda; ese queda sin buscar
        return None

    found = (artist['artist_id'], url)
    return found


def find_pictures(artists):
    '''
    Busca las fotos de una tanda de artistas, de a PHOTO_WORKERS a la vez; si Deezer limita o no responde, corta y devuelve lo que haya.
        Args:
            artists (list): dicts con artist_id, name y opcionales isrc y deezer_artist_id
        Returns:
            pictures (dict): artist_id -> url, o None si Deezer no tiene foto; los que no se llegaron a buscar no estan
    '''
    stop = threading.Event()

    with ThreadPoolExecutor(PHOTO_WORKERS) as pool:
        found = list(pool.map(lambda artist: find_picture_or_stop(artist, stop), artists))

    pictures = dict(item for item in found if item is not None)
    return pictures


def normalize_name(name):
    '''
    Normaliza un nombre de artista para comparar entre Spotify y Deezer.
        Args:
            name (str): nombre tal como viene
        Returns:
            normalized (str): en minuscula y sin espacios de mas
    '''
    normalized = ' '.join(name.casefold().split())
    return normalized
