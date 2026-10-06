import time

import httpx

API_URL = 'https://api.deezer.com'

# Deezer corta con code 4 si pasamos ~50 requests cada 5 s; esperamos la ventana y reintentamos
QUOTA_CODE = 4
QUOTA_WAIT = 5
QUOTA_RETRIES = 3

# code 800 es "no data": un ISRC o un id que Deezer no conoce
NO_DATA_CODE = 800


class DeezerError(Exception):
    pass


def call_api(url, params=None):
    '''
    Llama a la API publica de Deezer, que no pide token y devuelve los errores con status 200.
        Args:
            url (str): path bajo API_URL o url completa (los `next` de la paginacion)
            params (dict): query params
        Returns:
            data (dict): respuesta de Deezer, o None si no conoce lo que pedimos
    '''
    if url.startswith('/'):
        url = f'{API_URL}{url}'

    for attempt in range(QUOTA_RETRIES + 1):
        response = httpx.get(url, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
        error = data.get('error') if isinstance(data, dict) else None

        if not error:
            return data

        if error.get('code') == NO_DATA_CODE:
            return None

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


def find_artist_by_isrc(isrc, name):
    '''
    Busca en Deezer el track de un ISRC y devuelve el artista del track con ese nombre.
        Args:
            isrc (str): ISRC de un Track donde figura el artista
            name (str): nombre del artista en Spotify
        Returns:
            artist_id (int): id de Deezer del artista, o None si no aparece
    '''
    track = call_api(f'/track/isrc:{isrc}')
    if track is None:
        return None

    # el ISRC identifica el track; el artista lo elegimos por nombre entre principal e invitados
    candidates = track.get('contributors') or [track['artist']]
    wanted = normalize_name(name)
    artist_id = next((artist['id'] for artist in candidates if normalize_name(artist['name']) == wanted), None)
    return artist_id


def find_artist_by_name(name):
    '''
    Busca un artista por nombre exacto; entre homonimos gana el de mas fans.
        Args:
            name (str): nombre del artista en Spotify
        Returns:
            artist_id (int): id de Deezer del artista, o None si ninguno coincide
    '''
    data = call_api('/search/artist', {'q': name, 'limit': 10})
    wanted = normalize_name(name)
    exact = [artist for artist in (data or {}).get('data', []) if normalize_name(artist['name']) == wanted]

    if not exact:
        return None

    artist_id = max(exact, key=lambda artist: artist.get('nb_fan', 0))['id']
    return artist_id


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
