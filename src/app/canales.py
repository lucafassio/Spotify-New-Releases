from psycopg.rows import dict_row

from app import spotify

# la quota de la Shared App la comparten todos los Users (ADR 0005)
MAX_CANALES = 5

CANAL_COLUMNS = 'id, user_id, target_playlist_id, target_name, target_created_by_bot, retencion_n, guardado_filter, created_at'


class CanalError(Exception):
    pass


def list_canales(conn, user_id):
    '''
    Lista los Canales del User con el tamanio de su Whitelist, en orden de creacion.
        Args:
            conn (psycopg.Connection): conexion abierta
            user_id (int): id interno del User
        Returns:
            canales (list): filas de canales con whitelist_count
    '''
    canales = conn.cursor(row_factory=dict_row).execute(
        f'''
        SELECT {CANAL_COLUMNS}, (SELECT count(*) FROM whitelist_artists w WHERE w.canal_id = canales.id) AS whitelist_count
        FROM canales WHERE user_id = %s ORDER BY created_at, id
        ''',
        (user_id,),
    ).fetchall()
    return canales


def get_canal(conn, user_id, canal_id):
    '''
    Busca un Canal del User; el user_id evita que un User toque Canales ajenos.
        Args:
            conn (psycopg.Connection): conexion abierta
            user_id (int): id interno del User
            canal_id (int): id del Canal
        Returns:
            canal (dict): fila de canales, o None si no existe o es de otro User
    '''
    canal = conn.cursor(row_factory=dict_row).execute(f'SELECT {CANAL_COLUMNS} FROM canales WHERE id = %s AND user_id = %s', (canal_id, user_id)).fetchone()
    return canal


def create_canal(conn, user_id, target_playlist_id, target_name, created_by_bot):
    '''
    Crea un Canal atado a su Target Playlist, con Retencion Acumulativa y filtro de Guardado prendido.
        Args:
            conn (psycopg.Connection): conexion abierta, el commit queda a cargo del que llama
            user_id (int): id interno del User
            target_playlist_id (str): id de Spotify de la Target Playlist
            target_name (str): nombre de la playlist para la Barra lateral
            created_by_bot (bool): si la playlist la creo el bot (habilita Retencion N)
        Returns:
            canal_id (int): id del Canal nuevo
    '''
    count = conn.execute('SELECT count(*) FROM canales WHERE user_id = %s', (user_id,)).fetchone()[0]
    if count >= MAX_CANALES:
        raise CanalError(f'Ya tenes {MAX_CANALES} Canales, que es el maximo.')

    # una playlist es Target de un solo Canal (ADR 0005)
    taken = conn.execute('SELECT 1 FROM canales WHERE user_id = %s AND target_playlist_id = %s', (user_id, target_playlist_id)).fetchone()
    if taken:
        raise CanalError('Esa playlist ya es la de otro Canal.')

    canal_id = conn.execute(
        'INSERT INTO canales (user_id, target_playlist_id, target_name, target_created_by_bot) VALUES (%s, %s, %s, %s) RETURNING id',
        (user_id, target_playlist_id, target_name, created_by_bot),
    ).fetchone()[0]
    return canal_id


def sync_target_names(conn, user_id, playlists):
    '''
    Copia a los Canales el nombre actual de sus Target Playlists, por si el User las renombro en Spotify.
        Args:
            conn (psycopg.Connection): conexion abierta
            user_id (int): id interno del User
            playlists (list): playlists de GET /me/playlists
    '''
    for playlist in playlists:
        conn.execute(
            'UPDATE canales SET target_name = %s WHERE user_id = %s AND target_playlist_id = %s AND target_name <> %s',
            (playlist['name'], user_id, playlist['id'], playlist['name']),
        )


def set_retencion(conn, canal, retencion_n):
    '''
    Cambia la Retencion; solo se puede en una Target Playlist creada por el bot.
        Args:
            conn (psycopg.Connection): conexion abierta
            canal (dict): fila de canales
            retencion_n (int): Lotes que se conservan, o None para Acumulativa
    '''
    if not canal['target_created_by_bot'] and retencion_n is not None:
        raise CanalError('En una playlist tuya se guarda todo.')

    conn.execute('UPDATE canales SET retencion_n = %s WHERE id = %s', (retencion_n, canal['id']))


def set_guardado(conn, canal_id, on):
    '''
    Prende o apaga el filtro de Guardado del Canal.
        Args:
            conn (psycopg.Connection): conexion abierta
            canal_id (int): id del Canal
            on (bool): True saltea lo que el User ya tiene
    '''
    conn.execute('UPDATE canales SET guardado_filter = %s WHERE id = %s', (on, canal_id))


def get_sources(conn, canal_id):
    '''
    Lista los Artist Sources del Canal con cuantos artistas aporta cada uno.
        Args:
            conn (psycopg.Connection): conexion abierta
            canal_id (int): id del Canal
        Returns:
            sources (list): filas de artist_sources con main_count y feat_count
    '''
    sources = conn.cursor(row_factory=dict_row).execute(
        '''
        SELECT s.id, s.type, s.playlist_id, s.include_feats, s.snapshot_id,
            count(sa.artist_id) FILTER (WHERE NOT sa.feat) AS main_count,
            count(sa.artist_id) FILTER (WHERE sa.feat) AS feat_count
        FROM artist_sources s LEFT JOIN source_artists sa ON sa.artist_source_id = s.id
        WHERE s.canal_id = %s GROUP BY s.id ORDER BY s.id
        ''',
        (canal_id,),
    ).fetchall()
    return sources


def get_source_artists(conn, source_id):
    '''
    Lista los artistas guardados de un Artist Source, principales primero.
        Args:
            conn (psycopg.Connection): conexion abierta
            source_id (int): id del Artist Source
        Returns:
            artists (list): filas con artist_id, name y feat
    '''
    artists = conn.cursor(row_factory=dict_row).execute(
        'SELECT artist_id, name, feat FROM source_artists WHERE artist_source_id = %s ORDER BY feat, lower(name)',
        (source_id,),
    ).fetchall()
    return artists


def find_source(conn, canal_id, source_type, playlist_id=None):
    '''
    Busca el Artist Source de un tipo (y playlist) en el Canal.
        Args:
            conn (psycopg.Connection): conexion abierta
            canal_id (int): id del Canal
            source_type (str): seed_playlist, manual o followed
            playlist_id (str): id de la playlist si es seed_playlist
        Returns:
            source_id (int): id del Artist Source, o None si no esta
    '''
    row = conn.execute(
        'SELECT id FROM artist_sources WHERE canal_id = %s AND type = %s AND playlist_id IS NOT DISTINCT FROM %s',
        (canal_id, source_type, playlist_id),
    ).fetchone()
    source_id = row[0] if row else None
    return source_id


def save_source_artists(conn, source_id, artists):
    '''
    Reemplaza los artistas guardados de un Artist Source por los que aporta hoy.
        Args:
            conn (psycopg.Connection): conexion abierta
            source_id (int): id del Artist Source
            artists (dict): artist_id -> {name, feat, isrc}; isrc puede faltar
    '''
    conn.execute('DELETE FROM source_artists WHERE artist_source_id = %s', (source_id,))

    with conn.cursor() as cur:
        cur.executemany(
            'INSERT INTO source_artists (artist_source_id, artist_id, name, feat, isrc) VALUES (%s, %s, %s, %s, %s)',
            [(source_id, artist_id, artist['name'], artist['feat'], artist.get('isrc')) for artist_id, artist in artists.items()],
        )


def add_source(conn, canal_id, source_type, artists, playlist_id=None):
    '''
    Suma un Artist Source al Canal con sus artistas y recalcula la Whitelist.
        Args:
            conn (psycopg.Connection): conexion abierta
            canal_id (int): id del Canal
            source_type (str): seed_playlist o followed
            artists (dict): artist_id -> {name, feat}, ya leidos de Spotify
            playlist_id (str): id de la playlist si es seed_playlist
        Returns:
            source_id (int): id del Artist Source
    '''
    source_id = find_source(conn, canal_id, source_type, playlist_id)

    if source_id is None:
        source_id = conn.execute(
            'INSERT INTO artist_sources (canal_id, type, playlist_id) VALUES (%s, %s, %s) RETURNING id',
            (canal_id, source_type, playlist_id),
        ).fetchone()[0]

    save_source_artists(conn, source_id, artists)
    recalc_whitelist(conn, canal_id)
    return source_id


def remove_source(conn, canal_id, source_type, playlist_id=None):
    '''
    Saca un Artist Source del Canal y recalcula la Whitelist.
        Args:
            conn (psycopg.Connection): conexion abierta
            canal_id (int): id del Canal
            source_type (str): seed_playlist o followed
            playlist_id (str): id de la playlist si es seed_playlist
    '''
    conn.execute(
        'DELETE FROM artist_sources WHERE canal_id = %s AND type = %s AND playlist_id IS NOT DISTINCT FROM %s',
        (canal_id, source_type, playlist_id),
    )
    recalc_whitelist(conn, canal_id)


def set_feats(conn, canal_id, playlist_id, on):
    '''
    Prende o apaga los feats de una Seed Playlist del Canal.
        Args:
            conn (psycopg.Connection): conexion abierta
            canal_id (int): id del Canal
            playlist_id (str): id de la Seed Playlist
            on (bool): True suma tambien a los invitados
    '''
    conn.execute(
        "UPDATE artist_sources SET include_feats = %s WHERE canal_id = %s AND type = 'seed_playlist' AND playlist_id = %s",
        (on, canal_id, playlist_id),
    )
    recalc_whitelist(conn, canal_id)


def add_manual_artist(conn, canal_id, artist_id, name):
    '''
    Suma un artista a la lista manual del Canal, creando la lista si no existe.
        Args:
            conn (psycopg.Connection): conexion abierta
            canal_id (int): id del Canal
            artist_id (str): id de Spotify del artista
            name (str): nombre del artista
    '''
    source_id = find_source(conn, canal_id, 'manual')

    if source_id is None:
        source_id = conn.execute("INSERT INTO artist_sources (canal_id, type) VALUES (%s, 'manual') RETURNING id", (canal_id,)).fetchone()[0]

    conn.execute(
        'INSERT INTO source_artists (artist_source_id, artist_id, name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING',
        (source_id, artist_id, name),
    )
    recalc_whitelist(conn, canal_id)


def remove_manual_artist(conn, canal_id, artist_id):
    '''
    Saca un artista de la lista manual del Canal.
        Args:
            conn (psycopg.Connection): conexion abierta
            canal_id (int): id del Canal
            artist_id (str): id de Spotify del artista
    '''
    conn.execute(
        "DELETE FROM source_artists WHERE artist_id = %s AND artist_source_id IN (SELECT id FROM artist_sources WHERE canal_id = %s AND type = 'manual')",
        (artist_id, canal_id),
    )
    recalc_whitelist(conn, canal_id)


def recalc_whitelist(conn, canal_id):
    '''
    Deja la Whitelist igual a la union de los Artist Sources del Canal (ADR 0006).
        Args:
            conn (psycopg.Connection): conexion abierta
            canal_id (int): id del Canal
    '''
    # un feat cuenta solo si su Seed Playlist tiene los feats prendidos
    wanted = '''
        SELECT sa.artist_id FROM source_artists sa JOIN artist_sources s ON s.id = sa.artist_source_id
        WHERE s.canal_id = %(canal_id)s AND (NOT sa.feat OR s.include_feats)
    '''

    # el que sale se borra: si vuelve entra con added_at nuevo, como un alta (ADR 0006)
    conn.execute(f'DELETE FROM whitelist_artists WHERE canal_id = %(canal_id)s AND artist_id NOT IN ({wanted})', {'canal_id': canal_id})
    conn.execute(f'INSERT INTO whitelist_artists (canal_id, artist_id) SELECT DISTINCT %(canal_id)s, artist_id FROM ({wanted}) w ON CONFLICT DO NOTHING', {'canal_id': canal_id})


def get_whitelist(conn, canal_id):
    '''
    Lista la Whitelist del Canal con el nombre de cada artista.
        Args:
            conn (psycopg.Connection): conexion abierta
            canal_id (int): id del Canal
        Returns:
            whitelist (list): filas con artist_id, name y added_at, por nombre
    '''
    whitelist = conn.cursor(row_factory=dict_row).execute(
        '''
        SELECT * FROM (
            SELECT w.artist_id, w.added_at,
                (SELECT sa.name FROM source_artists sa JOIN artist_sources s ON s.id = sa.artist_source_id
                 WHERE s.canal_id = w.canal_id AND sa.artist_id = w.artist_id LIMIT 1) AS name
            FROM whitelist_artists w WHERE w.canal_id = %s
        ) whitelist ORDER BY lower(name)
        ''',
        (canal_id,),
    ).fetchall()
    return whitelist


def refresh_sources(conn, access_token, canal_id, snapshots):
    '''
    Vuelve a leer de Spotify las Seed Playlists que cambiaron y los follows del Canal, y recalcula la Whitelist.
        Args:
            conn (psycopg.Connection): conexion abierta
            access_token (str): access token del User
            canal_id (int): id del Canal
            snapshots (dict): playlist_id -> snapshot_id actual, sacado de GET /me/playlists
    '''
    for source in get_sources(conn, canal_id):
        if source['type'] == 'followed':
            save_source_artists(conn, source['id'], spotify.get_followed_artists(access_token))
            continue

        if source['type'] != 'seed_playlist':
            continue

        # una playlist que ya no esta en la biblioteca deja sus artistas como estaban; una igual no gasta quota (ADR 0007)
        snapshot_id = snapshots.get(source['playlist_id'])
        if snapshot_id is None or snapshot_id == source['snapshot_id']:
            continue

        save_source_artists(conn, source['id'], spotify.get_playlist_artists(access_token, source['playlist_id']))
        conn.execute('UPDATE artist_sources SET snapshot_id = %s WHERE id = %s', (snapshot_id, source['id']))

    recalc_whitelist(conn, canal_id)


def delete_canal(conn, access_token, canal, delete_playlist):
    '''
    Desvincula un Canal: hard delete en cascada y, si el User lo pide, saca la playlist de su biblioteca.
        Args:
            conn (psycopg.Connection): conexion abierta
            access_token (str): access token del User, solo se usa si delete_playlist
            canal (dict): fila de canales
            delete_playlist (bool): True borra tambien la Target Playlist
    '''
    # primero Spotify: si falla, el Canal sigue entero y el User puede reintentar
    if delete_playlist:
        spotify.remove_playlist(access_token, canal['target_playlist_id'])

    conn.execute('DELETE FROM canales WHERE id = %s', (canal['id'],))
