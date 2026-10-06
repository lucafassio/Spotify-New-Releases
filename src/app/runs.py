import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from app import canales, deezer, entregas, spotify, users

# Argentina no tiene horario de verano: un offset fijo evita depender de tzdata en Windows
ART = timezone(timedelta(hours=-3))

# ventana de un Run: los ultimos 7 dias, nunca antes del alta del artista en la Whitelist (#5)
WINDOW_DAYS = 7

# el Lote va de sabado a viernes y se cierra el viernes despues de explorar (ADR 0004)
SATURDAY = 5
FRIDAY = 4

# Deezer aguanta ~50 requests cada 5 s; con 4 hilos y ~0.6 s por request quedamos por debajo
DEEZER_WORKERS = 4

# un artista que no encontramos en Deezer se vuelve a buscar recien despues de esto
RELINK_DAYS = 7

# primera mitad del lock por User: el Tick y el boton de correr ahora no exploran a la vez
RUN_LOCK = 15

# Deezer publica la version clean como un disco aparte: "Titulo (Clean)" o "Titulo [Clean Version]"
CLEAN_MARK = re.compile(r'\s*[(\[]\s*clean(\s+version)?\s*[)\]]\s*$', re.IGNORECASE)


class RunBusy(Exception):
    pass


def get_today():
    '''
    Devuelve la fecha de hoy en Argentina, que es la que manda para Runs y Lotes.
        Returns:
            today (date): fecha ART
    '''
    today = datetime.now(ART).date()
    return today


def get_week_start(day):
    '''
    Devuelve el sabado que abre la semana de un Lote.
        Args:
            day (date): cualquier dia
        Returns:
            week_start (date): sabado igual o anterior a day
    '''
    week_start = day - timedelta(days=(day.weekday() - SATURDAY) % 7)
    return week_start


def run_user(conn, user, today=None):
    '''
    Corre el Run de un User: explora, llena el Lote abierto de cada Canal, entrega lo cerrado y deja el estado en la fila Run.
        Args:
            conn (psycopg.Connection): conexion abierta; el Run commitea por su cuenta
            user (dict): fila de users
            today (date): fecha ART del Run, la pasan los tests
        Returns:
            found (int): Tracks nuevos sumados a los Lotes
    '''
    today = today or get_today()

    # lock de sesion: sobrevive a los commits del Run y se suelta aunque falle
    locked = conn.execute('SELECT pg_try_advisory_lock(%s, %s)', (RUN_LOCK, user['id'])).fetchone()[0]
    conn.commit()
    if not locked:
        raise RunBusy()

    try:
        set_run_status(conn, user['id'], today, 'en_curso')
        conn.commit()

        try:
            found, delivered = explore(conn, user, today)
        except Exception:
            # Desconectado, quota agotada o Spotify caido: el Run queda fallido y el Tick lo reintenta (#19, ADR 0007)
            conn.rollback()
            set_run_status(conn, user['id'], today, 'fallido')
            conn.commit()
            raise

        # exitoso solo si todos los Canales cerraron lo que les tocaba; si no, el Tick reintenta la Entrega (ADR 0004)
        set_run_status(conn, user['id'], today, 'exitoso' if delivered else 'fallido')
        conn.commit()
    finally:
        conn.execute('SELECT pg_advisory_unlock(%s, %s)', (RUN_LOCK, user['id']))
        conn.commit()

    return found


def set_run_status(conn, user_id, today, status):
    '''
    Escribe el estado del Run del dia; un reintento el mismo dia pisa la misma fila (ADR 0006).
        Args:
            conn (psycopg.Connection): conexion abierta
            user_id (int): id interno del User
            today (date): fecha ART del Run
            status (str): en_curso, exitoso o fallido
    '''
    conn.execute(
        'INSERT INTO runs (user_id, date, status) VALUES (%s, %s, %s) ON CONFLICT (user_id, date) DO UPDATE SET status = EXCLUDED.status',
        (user_id, today, status),
    )


def explore(conn, user, today):
    '''
    Busca los Releases de la ventana en Deezer, los pasa a Spotify, suma sus Tracks al Lote abierto de cada Canal y entrega lo cerrado.
        Args:
            conn (psycopg.Connection): conexion abierta
            user (dict): fila de users
            today (date): fecha ART del Run
        Returns:
            result (tuple): Tracks nuevos sumados a los Lotes y si todas las Entregas pendientes salieron
    '''
    access_token = users.get_access_token(conn, user)

    # una sola lectura de la biblioteca sirve para los nombres de las Targets y para saber que Seed Playlist cambio
    playlists = spotify.get_my_playlists(access_token)
    canales.sync_target_names(conn, user['id'], playlists)
    snapshots = {playlist['id']: playlist.get('snapshot_id') for playlist in playlists}
    lotes = {}

    for canal in canales.list_canales(conn, user['id']):
        canales.refresh_sources(conn, access_token, canal['id'], snapshots)
        lotes[canal['id']] = open_lote(conn, canal['id'], today)

    conn.commit()

    starts = get_window_starts(conn, user['id'], today)
    artist_ids = {artist_id for canal_starts in starts.values() for artist_id in canal_starts}
    deezer_ids = link_artists(conn, artist_ids)
    conn.commit()

    # cada artista se explora una sola vez aunque este en varios Canales (ADR 0005)
    first_start = {}

    for canal_starts in starts.values():
        for artist_id, start in canal_starts.items():
            first_start[artist_id] = min(start, first_start.get(artist_id, start))

    releases = find_releases(deezer_ids, first_start, today)
    found = 0

    for release in sorted(releases.values(), key=lambda release: (release['release_date'], release['id'])):
        found += add_release(conn, access_token, release, starts, lotes, today)

    if today.weekday() == FRIDAY:
        close_lotes(conn, list(lotes), get_week_start(today))

    conn.commit()

    # cualquier dia: el viernes entrega el Lote recien cerrado y los otros dias reintenta lo que no salio (ADR 0004)
    delivered = entregas.deliver_all(conn, access_token, user['id'], playlists)
    result = (found, delivered)
    return result


def open_lote(conn, canal_id, today):
    '''
    Devuelve el Lote abierto del Canal para hoy, cerrando el de una semana vieja que quedo abierto.
        Args:
            conn (psycopg.Connection): conexion abierta
            canal_id (int): id del Canal
            today (date): fecha ART del Run
        Returns:
            lote_id (int): id del Lote donde van los Tracks de este Run
    '''
    week_start = get_week_start(today)

    # si el Run del viernes fallo, el sabado cierra ese Lote y la Entrega lo toma como cerrado sin entregar
    conn.execute(
        "UPDATE lotes SET status = 'cerrado', closed_at = now() WHERE canal_id = %s AND status = 'abierto' AND week_start < %s",
        (canal_id, week_start),
    )

    row = conn.execute('SELECT id, status FROM lotes WHERE canal_id = %s AND week_start = %s', (canal_id, week_start)).fetchone()

    # un Run del viernes despues del cierre ya trabaja para el Lote de la semana siguiente
    if row and row[1] == 'cerrado':
        week_start = week_start + timedelta(days=7)
        row = conn.execute('SELECT id, status FROM lotes WHERE canal_id = %s AND week_start = %s', (canal_id, week_start)).fetchone()

    if row:
        return row[0]

    lote_id = conn.execute('INSERT INTO lotes (canal_id, week_start) VALUES (%s, %s) RETURNING id', (canal_id, week_start)).fetchone()[0]
    return lote_id


def close_lotes(conn, canal_ids, week_start):
    '''
    Cierra el viernes los Lotes de la semana que termina.
        Args:
            conn (psycopg.Connection): conexion abierta
            canal_ids (list): ids de los Canales del User
            week_start (date): sabado que abrio la semana
    '''
    conn.execute(
        "UPDATE lotes SET status = 'cerrado', closed_at = now() WHERE canal_id = ANY(%s) AND week_start = %s AND status = 'abierto'",
        (canal_ids, week_start),
    )


def get_window_starts(conn, user_id, today):
    '''
    Calcula desde que fecha mira el Run a cada artista de cada Canal: 7 dias atras o su alta, lo mas reciente.
        Args:
            conn (psycopg.Connection): conexion abierta
            user_id (int): id interno del User
            today (date): fecha ART del Run
        Returns:
            starts (dict): canal_id -> {artist_id: fecha desde la que entra un Release}
    '''
    rows = conn.execute(
        'SELECT w.canal_id, w.artist_id, w.added_at FROM whitelist_artists w JOIN canales c ON c.id = w.canal_id WHERE c.user_id = %s',
        (user_id,),
    ).fetchall()
    window_start = today - timedelta(days=WINDOW_DAYS)
    starts = {}

    for canal_id, artist_id, added_at in rows:
        starts.setdefault(canal_id, {})[artist_id] = max(window_start, added_at.astimezone(ART).date())

    return starts


def link_artists(conn, artist_ids):
    '''
    Vincula artistas de Spotify con Deezer usando el cache global y busca los que faltan (ADR 0007).
        Args:
            conn (psycopg.Connection): conexion abierta
            artist_ids (set): ids de Spotify de los artistas a explorar
        Returns:
            deezer_ids (dict): artist_id de Spotify -> id de Deezer, solo los vinculados
    '''
    rows = conn.execute(
        "SELECT spotify_artist_id, deezer_artist_id, checked_at > now() - %s * interval '1 day' FROM deezer_artists WHERE spotify_artist_id = ANY(%s)",
        (RELINK_DAYS, list(artist_ids)),
    ).fetchall()
    deezer_ids = {row[0]: row[1] for row in rows if row[1] is not None}
    fresh = {row[0] for row in rows if row[1] is not None or row[2]}
    missing = [artist_id for artist_id in artist_ids if artist_id not in fresh]

    if not missing:
        return deezer_ids

    # el ISRC viene de cualquier Seed Playlist donde aparezca el artista, de este User o de otro
    hints = conn.execute(
        'SELECT DISTINCT ON (artist_id) artist_id, name, isrc FROM source_artists WHERE artist_id = ANY(%s) ORDER BY artist_id, isrc NULLS LAST',
        (missing,),
    ).fetchall()

    with ThreadPoolExecutor(DEEZER_WORKERS) as pool:
        matches = list(pool.map(lambda hint: find_deezer_artist(hint[1], hint[2]), hints))

    for (artist_id, _, _), (deezer_id, matched_by) in zip(hints, matches):
        conn.execute(
            '''
            INSERT INTO deezer_artists (spotify_artist_id, deezer_artist_id, matched_by) VALUES (%s, %s, %s)
            ON CONFLICT (spotify_artist_id) DO UPDATE SET deezer_artist_id = EXCLUDED.deezer_artist_id, matched_by = EXCLUDED.matched_by, checked_at = now()
            ''',
            (artist_id, deezer_id, matched_by),
        )

        if deezer_id is not None:
            deezer_ids[artist_id] = deezer_id

    return deezer_ids


def find_deezer_artist(name, isrc):
    '''
    Busca un artista en Deezer: primero por el ISRC de un Track donde figura, si no por nombre.
        Args:
            name (str): nombre del artista en Spotify
            isrc (str): ISRC de un Track suyo, o None
        Returns:
            match (tuple): id de Deezer (o None) y como lo encontramos (isrc, name o None)
    '''
    if isrc:
        deezer_id = deezer.find_artist_by_isrc(isrc, name)
        if deezer_id is not None:
            return deezer_id, 'isrc'

    deezer_id = deezer.find_artist_by_name(name)
    match = (deezer_id, 'name' if deezer_id is not None else None)
    return match


def find_releases(deezer_ids, first_start, today):
    '''
    Lee en Deezer la discografia de cada artista y se queda con lo que salio dentro de su ventana.
        Args:
            deezer_ids (dict): artist_id de Spotify -> id de Deezer
            first_start (dict): artist_id de Spotify -> fecha mas vieja que le interesa a algun Canal
            today (date): fecha ART del Run
        Returns:
            releases (dict): id de album de Deezer -> album con release_date como date
    '''
    artist_ids = list(deezer_ids)

    with ThreadPoolExecutor(DEEZER_WORKERS) as pool:
        discographies = list(pool.map(lambda artist_id: deezer.get_artist_albums(deezer_ids[artist_id]), artist_ids))

    releases = {}

    for artist_id, albums in zip(artist_ids, discographies):
        for album in albums:
            release_date = parse_date(album.get('release_date'))

            # Deezer lista tambien lo anunciado a futuro; una compilation nunca es Release (#5)
            if album.get('record_type') == 'compile' or release_date is None:
                continue

            if first_start[artist_id] <= release_date <= today:
                releases[album['id']] = {**album, 'release_date': release_date}

    releases = drop_clean_twins(releases)
    return releases


def parse_date(text):
    '''
    Lee una fecha YYYY-MM-DD de Deezer.
        Args:
            text (str): fecha como viene, puede faltar o venir 0000-00-00
        Returns:
            day (date): fecha, o None si no es valida
    '''
    try:
        day = date.fromisoformat(text)
    except (TypeError, ValueError):
        day = None

    return day


def drop_clean_twins(releases):
    '''
    Saca la version clean de un disco cuando Deezer tambien tiene la explicit del mismo dia (ADR 0007).
        Args:
            releases (dict): id de album de Deezer -> album
        Returns:
            releases (dict): los mismos sin los gemelos clean
    '''
    explicit_keys = {(album['title'].casefold(), album['release_date']) for album in releases.values() if not CLEAN_MARK.search(album['title'])}
    kept = {}

    for album_id, album in releases.items():
        clean_key = (CLEAN_MARK.sub('', album['title']).casefold(), album['release_date'])
        if CLEAN_MARK.search(album['title']) and clean_key in explicit_keys:
            continue

        kept[album_id] = album

    return kept


def add_release(conn, access_token, release, starts, lotes, today):
    '''
    Pasa un Release de Deezer a Spotify y suma sus Tracks al Lote de cada Canal que todavia no lo tiene.
        Args:
            conn (psycopg.Connection): conexion abierta
            access_token (str): access token del User
            release (dict): album de Deezer con release_date como date
            starts (dict): canal_id -> {artist_id: fecha desde la que entra un Release}
            lotes (dict): canal_id -> id del Lote abierto
            today (date): fecha ART del Run
        Returns:
            found (int): Tracks nuevos sumados
    '''
    # artistas de cada Canal para los que este Release cae dentro de su ventana
    wanted = {}

    for canal_id, canal_starts in starts.items():
        artist_ids = {artist_id for artist_id, start in canal_starts.items() if start <= release['release_date']}
        if artist_ids:
            wanted[canal_id] = artist_ids

    if not wanted:
        return 0

    album_id, album_artist_ids = link_album(conn, access_token, release['id'], today)
    if album_id is None:
        return 0

    # un album ya leido que no tiene a ningun artista de los Canales (ej: un homonimo que Deezer junto) no gasta quota
    if album_artist_ids is not None:
        wanted = {canal_id: artist_ids for canal_id, artist_ids in wanted.items() if artist_ids & set(album_artist_ids)}

    # un Release entra una sola vez por Canal: si ya figura en algun Lote, ya lo vio un Run anterior (#5)
    seen = conn.execute(
        'SELECT DISTINCT l.canal_id FROM lote_items i JOIN lotes l ON l.id = i.lote_id WHERE i.album_id = %s AND l.canal_id = ANY(%s)',
        (album_id, list(wanted)),
    ).fetchall()

    for (canal_id,) in seen:
        wanted.pop(canal_id)

    if not wanted:
        return 0

    album = spotify.get_album(access_token, album_id)
    main_ids = {artist['id'] for artist in album['artists']}
    track_ids = {artist['id'] for track in album['tracks'] for artist in track['artists'] if artist.get('id')}
    conn.execute('UPDATE album_links SET artist_ids = %s WHERE deezer_album_id = %s', (sorted(main_ids | track_ids), release['id']))
    found = 0

    for canal_id, artist_ids in wanted.items():
        # artista principal: entra el disco completo; invitado en un disco ajeno: solo los Tracks donde figura (ADR 0007)
        tracks = album['tracks']
        if not main_ids & artist_ids:
            tracks = [track for track in tracks if {artist['id'] for artist in track['artists']} & artist_ids]

        found += add_lote_items(conn, lotes[canal_id], album, tracks, release['release_date'])

    return found


def link_album(conn, access_token, deezer_album_id, today):
    '''
    Devuelve el album de Spotify de un album de Deezer, buscandolo por UPC si no esta en el cache global.
        Args:
            conn (psycopg.Connection): conexion abierta
            access_token (str): access token del User
            deezer_album_id (int): id de Deezer del album
            today (date): fecha ART del Run
        Returns:
            link (tuple): id de Spotify del album (o None si Spotify todavia no lo tiene) y sus artistas si ya lo leimos (o None)
    '''
    row = conn.execute('SELECT spotify_album_id, checked_at, artist_ids FROM album_links WHERE deezer_album_id = %s', (deezer_album_id,)).fetchone()

    # lo que Spotify no tenia se vuelve a buscar una vez por dia mientras siga en la ventana
    if row and (row[0] is not None or row[1].astimezone(ART).date() >= today):
        return row[0], row[2]

    upc = deezer.get_album_upc(deezer_album_id)
    album = spotify.find_album_by_upc(access_token, upc) if upc else None
    album_id = album['id'] if album else None

    conn.execute(
        '''
        INSERT INTO album_links (deezer_album_id, spotify_album_id) VALUES (%s, %s)
        ON CONFLICT (deezer_album_id) DO UPDATE SET spotify_album_id = EXCLUDED.spotify_album_id, checked_at = now()
        ''',
        (deezer_album_id, album_id),
    )
    return album_id, None


def add_lote_items(conn, lote_id, album, tracks, release_date):
    '''
    Suma Tracks pendientes al Lote con la metadata que despues ordena la Entrega (ADR 0003).
        Args:
            conn (psycopg.Connection): conexion abierta
            lote_id (int): id del Lote abierto
            album (dict): album de Spotify
            tracks (list): tracks del album que entran
            release_date (date): fecha de salida del Release
        Returns:
            found (int): Tracks nuevos; un track repetido dentro del mismo Lote no cuenta
    '''
    # el grupo es el duenio del disco, asi un feat en un disco ajeno queda junto al resto de ese disco
    artist_name = album['artists'][0]['name']
    main_ids = [artist['id'] for artist in album['artists']]
    found = 0

    for track in tracks:
        if not track.get('uri'):
            continue

        # la Entrega filtra con la Whitelist del momento: el Track sigue si alguno de estos sigue en ella
        artist_ids = sorted(set(main_ids) | {artist['id'] for artist in track['artists'] if artist.get('id')})

        cursor = conn.execute(
            '''
            INSERT INTO lote_items (lote_id, track_uri, album_id, artist_name, disc_number, track_number, release_date, artist_ids)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
            ''',
            (lote_id, track['uri'], album['id'], artist_name, track['disc_number'], track['track_number'], release_date, artist_ids),
        )
        found += cursor.rowcount

    return found
