import traceback

from app import canales, spotify

# en una Target Playlist creada por el bot se retiran sus Lotes mas viejos hasta quedar en esto (ADR 0004)
BOT_CAP = 9500

# tope de una playlist segun la comunidad de Spotify; en una existente lo que no entra se descarta sin reintentar
PLAYLIST_CAP = 10000

# orden del Lote (ADR 0003): por duenio del disco, sus Releases del mas nuevo al mas viejo y cada disco en su orden
LOTE_ORDER = 'lower(artist_name), artist_name, release_date DESC, album_id, disc_number, track_number'


def deliver_all(conn, access_token, user_id, playlists):
    '''
    Hace la Entrega de cada Canal del User con un Lote cerrado sin entregar; un Canal que falla no frena a los otros (ADR 0005).
        Args:
            conn (psycopg.Connection): conexion abierta; cada paso commitea para que un reintento retome
            access_token (str): access token del User
            user_id (int): id interno del User
            playlists (list): playlists de GET /me/playlists, leidas al arrancar el Run
        Returns:
            delivered (bool): True si todos los Canales cerraron lo que les tocaba
    '''
    by_id = {playlist['id']: playlist for playlist in playlists}
    delivered = True

    for canal in canales.list_canales(conn, user_id):
        try:
            deliver(conn, access_token, canal, by_id.get(canal['target_playlist_id']))
        except spotify.QuotaExceeded:
            # sin quota no hay nada que reintentar hoy: el Run entero queda fallido (ADR 0007)
            raise
        except Exception:
            conn.rollback()
            traceback.print_exc()
            delivered = False

    return delivered


def deliver(conn, access_token, canal, playlist):
    '''
    Lleva el Lote cerrado del Canal a su Target Playlist: filtra con la config de ahora, inserta, retira y recorta (ADR 0004).
        Args:
            conn (psycopg.Connection): conexion abierta
            access_token (str): access token del User
            canal (dict): fila de canales
            playlist (dict): la Target Playlist como vino en GET /me/playlists, o None si no aparecio
    '''
    lote_id = take_pending_lote(conn, canal['id'])
    if lote_id is None:
        return

    conn.execute('UPDATE lotes SET attempt_count = attempt_count + 1 WHERE id = %s', (lote_id,))
    filter_whitelist(conn, canal['id'], lote_id)
    conn.commit()

    items = get_pending(conn, lote_id)
    expired = get_expired_lotes(conn, canal, lote_id)

    # un Lote vacio no toca la playlist ni gasta quota
    if not items and not expired:
        finish(conn, canal, lote_id, None)
        return

    if playlist is None and not target_exists(access_token, canal):
        # la Target ya no existe: no hay Entrega, el Canal se borra y su Lote se pierde (ADR 0005)
        conn.execute('DELETE FROM canales WHERE id = %s', (canal['id'],))
        conn.commit()
        return

    total = spotify.get_track_total(playlist) if playlist else canal['target_total']

    if items and canal['guardado_filter']:
        filter_guardado(conn, access_token, lote_id, items)
        conn.commit()
        items = get_pending(conn, lote_id)

    if not canal['target_created_by_bot']:
        items = drop_overflow(conn, lote_id, items, total)

    total += insert(conn, access_token, canal, lote_id, items)
    total -= retire(conn, access_token, canal, get_expired_lotes(conn, canal, lote_id))

    if canal['target_created_by_bot']:
        total = trim(conn, access_token, canal, lote_id, total)

    finish(conn, canal, lote_id, total)


def take_pending_lote(conn, canal_id):
    '''
    Devuelve el Lote cerrado sin entregar del Canal, sumandole los anteriores que tampoco salieron.
        Args:
            conn (psycopg.Connection): conexion abierta
            canal_id (int): id del Canal
        Returns:
            lote_id (int): id del Lote a entregar, o None si no hay
    '''
    rows = conn.execute(
        "SELECT id FROM lotes WHERE canal_id = %s AND status = 'cerrado' AND delivered_at IS NULL ORDER BY week_start",
        (canal_id,),
    ).fetchall()

    if not rows:
        return None

    lote_id = rows[-1][0]
    older = [row[0] for row in rows[:-1]]

    # un Lote cerrado sin entregar se suma al siguiente y salen juntos como una sola Entrega (ADR 0004)
    if older:
        conn.execute(
            '''
            INSERT INTO lote_items (lote_id, track_uri, album_id, artist_name, disc_number, track_number, release_date, status, artist_ids)
            SELECT %s, track_uri, album_id, artist_name, disc_number, track_number, release_date, status, artist_ids
            FROM lote_items WHERE lote_id = ANY(%s)
            ON CONFLICT (lote_id, track_uri) DO UPDATE SET status = EXCLUDED.status WHERE lote_items.status = 'pendiente'
            ''',
            (lote_id, older),
        )
        conn.execute(
            'UPDATE lotes SET attempt_count = attempt_count + (SELECT sum(attempt_count) FROM lotes WHERE id = ANY(%s)) WHERE id = %s',
            (older, lote_id),
        )
        conn.execute('DELETE FROM lotes WHERE id = ANY(%s)', (older,))

    return lote_id


def filter_whitelist(conn, canal_id, lote_id):
    '''
    Descarta lo pendiente de artistas que el User saco de la Whitelist durante la semana (ADR 0004).
        Args:
            conn (psycopg.Connection): conexion abierta
            canal_id (int): id del Canal
            lote_id (int): id del Lote a entregar
    '''
    conn.execute(
        '''
        UPDATE lote_items SET status = 'descartado'
        WHERE lote_id = %s AND status = 'pendiente' AND artist_ids IS NOT NULL
            AND NOT artist_ids && ARRAY(SELECT artist_id FROM whitelist_artists WHERE canal_id = %s)
        ''',
        (lote_id, canal_id),
    )


def get_pending(conn, lote_id):
    '''
    Lista lo pendiente del Lote en el orden en que debe quedar en la playlist.
        Args:
            conn (psycopg.Connection): conexion abierta
            lote_id (int): id del Lote
        Returns:
            items (list): tuplas (track_uri, album_id) en orden del Lote
    '''
    items = conn.execute(
        f"SELECT track_uri, album_id FROM lote_items WHERE lote_id = %s AND status = 'pendiente' ORDER BY {LOTE_ORDER}",
        (lote_id,),
    ).fetchall()
    return items


def target_exists(access_token, canal):
    '''
    Confirma con Spotify si la Target sigue en la biblioteca; solo se pregunta si no aparecio en GET /me/playlists.
        Args:
            access_token (str): access token del User
            canal (dict): fila de canales
        Returns:
            exists (bool): True si la playlist sigue
    '''
    uri = f'spotify:playlist:{canal["target_playlist_id"]}'
    exists = spotify.check_library(access_token, [uri])[uri]
    return exists


def filter_guardado(conn, access_token, lote_id, items):
    '''
    Descarta un Release si el User ya tiene todos sus Tracks en liked, consultado en vivo (Guardado, #16).
        Args:
            conn (psycopg.Connection): conexion abierta
            access_token (str): access token del User
            lote_id (int): id del Lote
            items (list): pendientes (track_uri, album_id)
    '''
    # un Release que ya entro en parte en un intento anterior se termina de entregar
    started = {row[0] for row in conn.execute("SELECT DISTINCT album_id FROM lote_items WHERE lote_id = %s AND status = 'insertado'", (lote_id,))}
    albums = {}

    for track_uri, album_id in items:
        if album_id not in started:
            albums.setdefault(album_id, []).append(track_uri)

    saved = spotify.check_library(access_token, [uri for uris in albums.values() for uri in uris])

    for uris in albums.values():
        if all(saved[uri] for uri in uris):
            conn.execute("UPDATE lote_items SET status = 'descartado' WHERE lote_id = %s AND track_uri = ANY(%s)", (lote_id, uris))


def drop_overflow(conn, lote_id, items, total):
    '''
    En una playlist existente entra solo lo que cabe hasta el tope; el resto se descarta sin reintentar (ADR 0004).
        Args:
            conn (psycopg.Connection): conexion abierta
            lote_id (int): id del Lote
            items (list): pendientes en orden del Lote
            total (int): canciones que ya tiene la playlist
        Returns:
            items (list): los que entran
    '''
    room = max(PLAYLIST_CAP - total, 0)
    dropped = [track_uri for track_uri, _ in items[room:]]

    if dropped:
        conn.execute("UPDATE lote_items SET status = 'descartado' WHERE lote_id = %s AND track_uri = ANY(%s)", (lote_id, dropped))
        conn.commit()

    return items[:room]


def insert(conn, access_token, canal, lote_id, items):
    '''
    Mete los Tracks en tandas de 100, un POST por tanda; los de un mismo POST quedan en orden en todas las vistas (ADR 0003).
        Args:
            conn (psycopg.Connection): conexion abierta
            access_token (str): access token del User
            canal (dict): fila de canales
            lote_id (int): id del Lote
            items (list): pendientes en orden del Lote
        Returns:
            inserted (int): Tracks agregados
    '''
    uris = [track_uri for track_uri, _ in items]
    chunks = [uris[start:start + spotify.ITEMS_CHUNK] for start in range(0, len(uris), spotify.ITEMS_CHUNK)]

    # playlist del bot: todo arriba y la ultima tanda primero, asi la primera queda arriba y es la mas reciente
    # existente: al final en orden directo, donde el User ya espera lo nuevo (enmienda ADR 0003)
    position = 0 if canal['target_created_by_bot'] else None
    if canal['target_created_by_bot']:
        chunks.reverse()

    for chunk in chunks:
        spotify.add_items(access_token, canal['target_playlist_id'], chunk, position)

        # marcar cada tanda apenas entra: un reintento sigue con las que faltan
        conn.execute("UPDATE lote_items SET status = 'insertado' WHERE lote_id = %s AND track_uri = ANY(%s)", (lote_id, chunk))
        conn.commit()

    return len(uris)


def get_counted_lotes(conn, canal_id, lote_id):
    '''
    Lotes que cuentan como Entrega para la Retencion: entregados o el actual, con algo que llego a la playlist.
        Args:
            conn (psycopg.Connection): conexion abierta
            canal_id (int): id del Canal
            lote_id (int): id del Lote que se esta entregando
        Returns:
            lote_ids (list): del mas nuevo al mas viejo
    '''
    # un Lote donde no entro nada no cuenta: con Retencion 1 una semana vacia no deja la playlist vacia
    rows = conn.execute(
        '''
        SELECT l.id FROM lotes l
        WHERE l.canal_id = %s AND (l.delivered_at IS NOT NULL OR l.id = %s)
            AND EXISTS (SELECT 1 FROM lote_items i WHERE i.lote_id = l.id AND i.status IN ('insertado', 'retirado'))
        ORDER BY l.week_start DESC
        ''',
        (canal_id, lote_id),
    ).fetchall()
    lote_ids = [row[0] for row in rows]
    return lote_ids


def get_expired_lotes(conn, canal, lote_id):
    '''
    Lotes que la Retencion deja afuera y todavia tienen Tracks en la playlist.
        Args:
            conn (psycopg.Connection): conexion abierta
            canal (dict): fila de canales
            lote_id (int): id del Lote que se esta entregando
        Returns:
            lote_ids (list): Lotes a retirar
    '''
    # Acumulativa nunca retira, y una playlist existente es siempre Acumulativa
    if canal['retencion_n'] is None or not canal['target_created_by_bot']:
        return []

    expired = get_counted_lotes(conn, canal['id'], lote_id)[canal['retencion_n']:]
    rows = conn.execute("SELECT DISTINCT lote_id FROM lote_items WHERE lote_id = ANY(%s) AND status = 'insertado'", (expired,)).fetchall()
    lote_ids = [row[0] for row in rows]
    return lote_ids


def retire(conn, access_token, canal, lote_ids):
    '''
    Saca de la playlist los Tracks de unos Lotes en DELETEs de hasta 100, sin tocar URIs de Lotes que siguen (ADR 0004).
        Args:
            conn (psycopg.Connection): conexion abierta
            access_token (str): access token del User
            canal (dict): fila de canales
            lote_ids (list): Lotes a retirar
        Returns:
            removed (int): URIs borrados de la playlist
    '''
    if not lote_ids:
        return 0

    uris = [row[0] for row in conn.execute(
        "SELECT DISTINCT track_uri FROM lote_items WHERE lote_id = ANY(%s) AND status = 'insertado' ORDER BY track_uri",
        (lote_ids,),
    )]
    kept = {row[0] for row in conn.execute(
        "SELECT i.track_uri FROM lote_items i JOIN lotes l ON l.id = i.lote_id WHERE l.canal_id = %s AND NOT i.lote_id = ANY(%s) AND i.status = 'insertado'",
        (canal['id'], lote_ids),
    )}

    # DELETE borra todas las copias de un URI: el que sigue en un Lote vigente se queda y solo se marca
    removable = [uri for uri in uris if uri not in kept]

    for start in range(0, len(removable), spotify.ITEMS_CHUNK):
        chunk = removable[start:start + spotify.ITEMS_CHUNK]
        spotify.remove_items(access_token, canal['target_playlist_id'], chunk)
        conn.execute("UPDATE lote_items SET status = 'retirado' WHERE lote_id = ANY(%s) AND track_uri = ANY(%s) AND status = 'insertado'", (lote_ids, chunk))
        conn.commit()

    conn.execute("UPDATE lote_items SET status = 'retirado' WHERE lote_id = ANY(%s) AND status = 'insertado'", (lote_ids,))
    conn.commit()
    return len(removable)


def trim(conn, access_token, canal, lote_id, total):
    '''
    En una playlist del bot retira sus Lotes mas viejos hasta quedar en el tope (ADR 0004).
        Args:
            conn (psycopg.Connection): conexion abierta
            access_token (str): access token del User
            canal (dict): fila de canales
            lote_id (int): id del Lote que se esta entregando, nunca se recorta
            total (int): canciones de la playlist despues de insertar
        Returns:
            total (int): canciones despues de recortar
    '''
    oldest_first = [counted for counted in reversed(get_counted_lotes(conn, canal['id'], lote_id)) if counted != lote_id]

    for counted in oldest_first:
        if total <= BOT_CAP:
            break

        total -= retire(conn, access_token, canal, [counted])

    return total


def finish(conn, canal, lote_id, total):
    '''
    Marca el Lote como entregado y guarda el tamanio de la playlist para los avisos de tope.
        Args:
            conn (psycopg.Connection): conexion abierta
            canal (dict): fila de canales
            lote_id (int): id del Lote
            total (int): canciones de la playlist, o None si no cambio
    '''
    conn.execute('UPDATE lotes SET delivered_at = now() WHERE id = %s', (lote_id,))

    if total is not None:
        conn.execute('UPDATE canales SET target_total = %s WHERE id = %s', (total, canal['id']))

    conn.commit()
