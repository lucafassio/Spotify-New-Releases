import unicodedata

import httpx
from psycopg.rows import dict_row

from app import spotify

COPY_COLUMNS = 'playlist_id, position, name, owner_id, collaborative, snapshot_id, total, image_url, artists_snapshot_id, artists_total, last_track_uri'

# lado en px de la Portada mas grande, al doble para pantallas de alta densidad
COVER_SIZE = 112

# lo que puede fallar al leer de Spotify; el refresco lo devuelve como resultado para que la web siga con la copia
READ_ERRORS = (httpx.HTTPError, spotify.RateLimited, spotify.QuotaExceeded)


def artist_sort_key(name):
    '''
    Clave del orden alfabetico de los artistas, sin distinguir mayusculas ni acentos.
        Args:
            name (str): nombre del artista
        Returns:
            key (str): texto comparable
    '''
    key = ''.join(char for char in unicodedata.normalize('NFKD', name or '') if not unicodedata.combining(char)).casefold()
    return key


def sorted_artists(artists):
    '''
    Pasa los artistas de una copia a una lista en orden alfabetico.
        Args:
            artists (dict): artist_id -> {name, feat, isrc}
        Returns:
            rows (list): dicts con artist_id, name, feat e isrc
    '''
    rows = sorted(({'artist_id': artist_id, **artist} for artist_id, artist in artists.items()), key=lambda row: artist_sort_key(row['name']))
    return rows


def save_library(conn, user_id, playlists):
    '''
    Deja las copias iguales a la biblioteca del User, sin escribir las filas que no cambiaron y sin tocar los artistas leidos.
        Args:
            conn (psycopg.Connection): conexion abierta
            user_id (int): id interno del User
            playlists (list): playlists de GET /me/playlists, en el orden de Spotify
    '''
    # una biblioteca que cambia mientras se pagina puede repetir una playlist, y el upsert no acepta dos veces la misma fila
    unique = list({playlist['id']: playlist for playlist in reversed(playlists)}.values())[::-1]
    position = {playlist['id']: index for index, playlist in enumerate(unique)}

    conn.execute(
        '''
        INSERT INTO playlist_copies (user_id, playlist_id, position, name, owner_id, collaborative, snapshot_id, total, image_url)
        SELECT %s, * FROM unnest(%s::text[], %s::integer[], %s::text[], %s::text[], %s::boolean[], %s::text[], %s::integer[], %s::text[])
        ON CONFLICT (user_id, playlist_id) DO UPDATE SET
            position = EXCLUDED.position, name = EXCLUDED.name, owner_id = EXCLUDED.owner_id,
            collaborative = EXCLUDED.collaborative, snapshot_id = EXCLUDED.snapshot_id, total = EXCLUDED.total, image_url = EXCLUDED.image_url
        WHERE (playlist_copies.position, playlist_copies.name, playlist_copies.owner_id, playlist_copies.collaborative, playlist_copies.snapshot_id, playlist_copies.total, playlist_copies.image_url)
            IS DISTINCT FROM (EXCLUDED.position, EXCLUDED.name, EXCLUDED.owner_id, EXCLUDED.collaborative, EXCLUDED.snapshot_id, EXCLUDED.total, EXCLUDED.image_url)
        ''',
        (
            user_id,
            [playlist['id'] for playlist in unique],
            [position[playlist['id']] for playlist in unique],
            [playlist['name'] for playlist in unique],
            [playlist['owner']['id'] for playlist in unique],
            [bool(playlist.get('collaborative')) for playlist in unique],
            [playlist.get('snapshot_id') for playlist in unique],
            [spotify.get_track_total(playlist) for playlist in unique],
            [spotify.get_image_url(playlist.get('images'), COVER_SIZE) for playlist in unique],
        ),
    )
    conn.execute('DELETE FROM playlist_copies WHERE user_id = %s AND NOT (playlist_id = ANY(%s))', (user_id, list(position)))


def list_copies(conn, user_id):
    '''
    Lista la biblioteca guardada del User, en el orden de Spotify.
        Args:
            conn (psycopg.Connection): conexion abierta
            user_id (int): id interno del User
        Returns:
            copies (list): filas de playlist_copies
    '''
    copies = conn.cursor(row_factory=dict_row).execute(f'SELECT {COPY_COLUMNS} FROM playlist_copies WHERE user_id = %s ORDER BY position', (user_id,)).fetchall()
    return copies


def get_copy(conn, user_id, playlist_id):
    '''
    Busca la fila de una playlist de la biblioteca guardada.
        Args:
            conn (psycopg.Connection): conexion abierta
            user_id (int): id interno del User
            playlist_id (str): id de Spotify de la playlist
        Returns:
            copy (dict): fila de playlist_copies, o None si no esta en la biblioteca
    '''
    copy = conn.cursor(row_factory=dict_row).execute(f'SELECT {COPY_COLUMNS} FROM playlist_copies WHERE user_id = %s AND playlist_id = %s', (user_id, playlist_id)).fetchone()
    return copy


def get_artists(conn, user_id, playlist_id):
    '''
    Lee los artistas guardados de una playlist.
        Args:
            conn (psycopg.Connection): conexion abierta
            user_id (int): id interno del User
            playlist_id (str): id de Spotify de la playlist
        Returns:
            artists (dict): artist_id -> {name, feat, isrc}
    '''
    rows = conn.execute('SELECT artist_id, name, feat, isrc FROM playlist_copy_artists WHERE user_id = %s AND playlist_id = %s', (user_id, playlist_id)).fetchall()
    artists = {row[0]: {'name': row[1], 'feat': row[2], 'isrc': row[3]} for row in rows}
    return artists


def get_playlist_artist(conn, user_id, playlist_id, artist_id):
    '''
    Lee un solo artista guardado de una playlist, con su id de Deezer para la foto.
        Args:
            conn (psycopg.Connection): conexion abierta
            user_id (int): id interno del User
            playlist_id (str): id de Spotify de la playlist
            artist_id (str): id de Spotify del artista
        Returns:
            artist (dict): artist_id, name, feat, isrc y deezer_artist_id, o None si no esta en la copia
    '''
    artist = conn.cursor(row_factory=dict_row).execute(
        '''
        SELECT a.artist_id, a.name, a.feat, a.isrc, d.deezer_artist_id
        FROM playlist_copy_artists a LEFT JOIN deezer_artists d ON d.spotify_artist_id = a.artist_id
        WHERE a.user_id = %s AND a.playlist_id = %s AND a.artist_id = %s
        ''',
        (user_id, playlist_id, artist_id),
    ).fetchone()
    return artist


def count_unchosen(conn, user_id, playlist_id, canal_id):
    '''
    Cuenta los artistas de una playlist que todavia no entran en la Whitelist del Canal: en cero va "Ya estan todos".
        Args:
            conn (psycopg.Connection): conexion abierta
            user_id (int): id interno del User
            playlist_id (str): id de Spotify de la playlist
            canal_id (int): id del Canal
        Returns:
            count (int): artistas que quedan para elegir
    '''
    count = conn.execute(
        '''
        SELECT count(*) FROM playlist_copy_artists a
        WHERE a.user_id = %s AND a.playlist_id = %s
            AND NOT EXISTS (SELECT 1 FROM whitelist_artists w WHERE w.canal_id = %s AND w.artist_id = a.artist_id)
        ''',
        (user_id, playlist_id, canal_id),
    ).fetchone()[0]
    return count


def add_deezer_ids(conn, artists):
    '''
    Suma a cada artista su id de Deezer, para la foto; uno que el Run nunca vinculo queda en None.
        Args:
            conn (psycopg.Connection): conexion abierta
            artists (list): dicts con artist_id, se modifican en el lugar
        Returns:
            artists (list): los mismos dicts con deezer_artist_id
    '''
    rows = conn.execute(
        'SELECT spotify_artist_id, deezer_artist_id FROM deezer_artists WHERE spotify_artist_id = ANY(%s)',
        ([artist['artist_id'] for artist in artists],),
    ).fetchall()
    deezer_ids = dict(rows)

    for artist in artists:
        artist['deezer_artist_id'] = deezer_ids.get(artist['artist_id'])
    return artists


def read_changes(access_token, playlist, copy, known):
    '''
    Lee de Spotify lo minimo para poner al dia los artistas: solo la cola si fueron solo agregados, si no toda la playlist (ADR 0008).
        Args:
            access_token (str): access token vigente
            playlist (dict): playlist de GET /me/playlists
            copy (dict): fila de playlist_copies
            known (dict): artistas guardados
        Returns:
            read (dict): artists, last_track_uri y total de items leidos
    '''
    old_total = copy['artists_total']

    # si el ultimo Track conocido sigue en su lugar, lo que crecio son agregados; alcanza con leer desde el
    if copy['last_track_uri'] and old_total and spotify.get_track_total(playlist) > old_total:
        entries = spotify.get_playlist_entries(access_token, playlist['id'], old_total - 1)

        if entries and spotify.get_entry_uri(entries[0]) == copy['last_track_uri']:
            artists = spotify.collect_artists(entries[1:], {artist_id: artist.copy() for artist_id, artist in known.items()})
            read = {'artists': artists, 'last_track_uri': spotify.get_entry_uri(entries[-1]), 'total': old_total - 1 + len(entries)}
            return read

    entries = spotify.get_playlist_entries(access_token, playlist['id'])
    read = {'artists': spotify.collect_artists(entries), 'last_track_uri': spotify.get_entry_uri(entries[-1]) if entries else None, 'total': len(entries)}
    return read


def failed_refresh(error):
    '''
    Resultado de un refresco que no pudo leer de Spotify.
        Args:
            error (Exception): lo que fallo
        Returns:
            result (dict): resultado de refresh con status failed
    '''
    result = {'status': 'failed', 'first_read': False, 'added': [], 'removed': [], 'error': error}
    return result


def refresh(conn, access_token, user_id, playlist):
    '''
    Confirma si la copia de una playlist sigue vigente y la pone al dia gastando lo minimo; la fila ya tiene que estar guardada con save_library.
        Args:
            conn (psycopg.Connection): conexion abierta, el commit queda a cargo del que llama
            access_token (str): access token del User
            user_id (int): id interno del User
            playlist (dict): playlist de GET /me/playlists
        Returns:
            result (dict): status (same, changed o failed), first_read, added (lista de artistas), removed (ids) y error si fallo
    '''
    copy = get_copy(conn, user_id, playlist['id'])
    first_read = copy['artists_snapshot_id'] is None

    if not first_read and playlist.get('snapshot_id') == copy['artists_snapshot_id']:
        result = {'status': 'same', 'first_read': False, 'added': [], 'removed': []}
        return result

    known = get_artists(conn, user_id, playlist['id'])

    try:
        read = read_changes(access_token, playlist, copy, known)
    except READ_ERRORS as error:
        return failed_refresh(error)

    artists = read['artists']
    conn.execute('DELETE FROM playlist_copy_artists WHERE user_id = %s AND playlist_id = %s AND NOT (artist_id = ANY(%s))', (user_id, playlist['id'], list(artists)))

    with conn.cursor() as cur:
        cur.executemany(
            '''
            INSERT INTO playlist_copy_artists (user_id, playlist_id, artist_id, name, feat, isrc) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, playlist_id, artist_id) DO UPDATE SET name = EXCLUDED.name, feat = EXCLUDED.feat, isrc = EXCLUDED.isrc
            ''',
            [(user_id, playlist['id'], artist_id, artist['name'], artist['feat'], artist['isrc']) for artist_id, artist in artists.items() if known.get(artist_id) != artist],
        )

    conn.execute(
        'UPDATE playlist_copies SET artists_snapshot_id = %s, artists_total = %s, last_track_uri = %s WHERE user_id = %s AND playlist_id = %s',
        (playlist.get('snapshot_id'), read['total'], read['last_track_uri'], user_id, playlist['id']),
    )

    result = {
        'status': 'changed',
        'first_read': first_read,
        'added': sorted_artists({artist_id: artist for artist_id, artist in artists.items() if artist_id not in known}),
        'removed': [artist_id for artist_id in known if artist_id not in artists],
    }
    return result
