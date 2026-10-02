import os
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import canales, spotify, users
from app.config import get_env, get_shared_app
from app.db import connect

templates = Jinja2Templates(directory=Path(__file__).parent / 'templates')


def hue(text):
    '''
    Tono fijo 0-360 derivado de un texto, para Portadas y avatares (style guide).
        Args:
            text (str): id o nombre
        Returns:
            hue (int): tono del degradado
    '''
    value = 7
    for char in str(text):
        value = (value * 131 + ord(char) * 17) % 360
    return value


def initial(text):
    '''
    Inicial que va grande en una Portada: la primera letra o numero, o el primer caracter.
        Args:
            text (str): nombre
        Returns:
            initial (str): un caracter
    '''
    initial = next((char.upper() for char in str(text) if char.isascii() and char.isalnum()), str(text)[:1] or '#')
    return initial


templates.env.filters['hue'] = hue
templates.env.filters['initial'] = initial

# cookie firmada, no cifrada: guardamos solo el id del User y datos de pantalla, nunca tokens
app = FastAPI()
# Render define RENDER en el entorno: alla todo es https y la cookie no viaja por http
app.add_middleware(
    SessionMiddleware,
    secret_key=get_env('SESSION_SECRET'),
    same_site='lax',
    max_age=60 * 60 * 24 * 30,
    https_only=bool(os.environ.get('RENDER')),
)

LOGIN_ERRORS = {
    'cancelado': 'Cancelaste la conexion con Spotify. Cuando quieras, volve a tocar Conectar.',
    'vencido': 'El intento de conexion vencio. Toca Conectar de nuevo.',
    'spotify': 'Spotify no respondio bien. Proba de nuevo en un rato.',
}


# los access tokens duran una hora; cachearlos evita un refresh por cada click del dashboard
TOKEN_TTL = 50 * 60
token_cache = {}


def get_conn():
    '''
    Abre una conexion por request y la commitea al terminar si no hubo error.
        Returns:
            conn (psycopg.Connection): conexion abierta
    '''
    with connect() as conn:
        yield conn


# FastAPI necesita la anotacion Request para inyectarlo, es la unica excepcion a no usar type hints
async def read_form(request: Request):
    '''
    Lee un formulario urlencoded sin depender de python-multipart.
        Args:
            request (Request): request entrante
        Returns:
            form (dict): campo -> primer valor
    '''
    body = (await request.body()).decode()
    form = {key: values[0].strip() for key, values in parse_qs(body, keep_blank_values=True).items()}
    return form


class LoginRequired(Exception):
    pass


class CanalMissing(Exception):
    pass


def require_user(request: Request, conn=Depends(get_conn)):
    '''
    Devuelve el User de la sesion o corta el request mandandolo al login.
        Args:
            request (Request): request entrante
            conn (psycopg.Connection): conexion del request
        Returns:
            user (dict): fila de users
    '''
    user_id = request.session.get('user_id')
    user = users.get_user(conn, user_id) if user_id else None

    if user is None:
        raise LoginRequired()

    return user


def get_token(conn, user):
    '''
    Devuelve un access token del User, del cache si sigue vigente.
        Args:
            conn (psycopg.Connection): conexion del request
            user (dict): fila de users
        Returns:
            access_token (str): token para la Web API
    '''
    cached = token_cache.get(user['id'])
    if cached and cached[1] > time.monotonic():
        return cached[0]

    access_token = users.get_access_token(conn, user)
    token_cache[user['id']] = (access_token, time.monotonic() + TOKEN_TTL)
    return access_token


@app.exception_handler(LoginRequired)
def login_required(request, error):
    '''
    Manda a la Pantalla de login a quien no tiene sesion.
        Returns:
            response (RedirectResponse): redirect a /
    '''
    request.session.pop('user_id', None)
    return RedirectResponse('/', status_code=303)


@app.exception_handler(CanalMissing)
def canal_missing(request, error):
    '''
    Un Canal borrado (o de otro User) manda a Inicio sin cerrar la sesion.
        Returns:
            response (RedirectResponse): redirect a /
    '''
    return RedirectResponse('/', status_code=303)


@app.exception_handler(users.UserDisconnected)
def user_disconnected(request, error):
    '''
    Un User Desconectado vuelve a Inicio, donde ve el Aviso de desconexion.
        Returns:
            response (RedirectResponse): redirect a /
    '''
    return RedirectResponse('/', status_code=303)


@app.exception_handler(httpx.HTTPError)
def spotify_failed(request, error):
    '''
    Si Spotify falla a mitad de una accion, vuelve a Inicio con un Aviso en vez de un 500.
        Returns:
            response (RedirectResponse): redirect a /
    '''
    request.session['flash'] = 'Spotify no respondio bien. Proba de nuevo en un rato.'
    return RedirectResponse('/', status_code=303)


def shell_context(request, conn, user, **extra):
    '''
    Arma lo que necesita todo el dashboard: Barra lateral, Perfil y Avisos.
        Args:
            request (Request): request entrante
            conn (psycopg.Connection): conexion del request
            user (dict): fila de users
            **extra: claves propias de la vista
        Returns:
            context (dict): contexto para el template
    '''
    context = {
        'display_name': request.session.get('display_name') or user['spotify_user_id'],
        'disconnected': user['disconnected_at'] is not None,
        'reconnected_since': request.session.pop('reconnected_since', None),
        'flash': request.session.pop('flash', None),
        'canales': canales.list_canales(conn, user['id']),
        'max_canales': canales.MAX_CANALES,
        'view': None,
    }
    context.update(extra)
    return context


@app.get('/')
def home(request: Request, conn=Depends(get_conn)):
    '''
    Muestra el dashboard si hay sesion; si no, la Pantalla de login.
        Args:
            request (Request): request entrante
            conn (psycopg.Connection): conexion del request
        Returns:
            response (TemplateResponse): html de la pagina
    '''
    user_id = request.session.get('user_id')
    user = users.get_user(conn, user_id) if user_id else None

    # un User borrado a mano por Luca deja sesiones colgadas
    if user is None:
        request.session.pop('user_id', None)
        error = LOGIN_ERRORS.get(request.query_params.get('error'))
        response = templates.TemplateResponse(request, 'login.html', {'error': error})
        return response

    context = shell_context(request, conn, user, view='inicio')
    response = templates.TemplateResponse(request, 'dashboard.html', context)
    return response


@app.get('/login')
def login(request: Request):
    '''
    Manda al User a autorizar la Shared App en Spotify.
        Args:
            request (Request): request entrante
        Returns:
            response (RedirectResponse): redirect a accounts.spotify.com
    '''
    shared_app = get_shared_app()
    state = secrets.token_urlsafe(16)
    request.session['oauth_state'] = state
    authorize_url = spotify.build_authorize_url(shared_app['client_id'], shared_app['redirect_uri'], state)
    response = RedirectResponse(authorize_url, status_code=303)
    return response


@app.get('/callback')
def callback(request: Request, conn=Depends(get_conn)):
    '''
    Recibe la vuelta de Spotify: canjea el code, crea o reconecta al User y abre la sesion.
        Args:
            request (Request): request entrante con code y state, o error
            conn (psycopg.Connection): conexion del request
        Returns:
            response (Response): redirect al dashboard, o la Pantalla de alta si Spotify no lo deja entrar
    '''
    params = request.query_params
    expected_state = request.session.pop('oauth_state', None)

    if params.get('error'):
        return RedirectResponse('/?error=cancelado', status_code=303)

    # sin state guardado tambien cae aca: la cookie se pierde si se abre localhost en vez de 127.0.0.1
    if not expected_state or params.get('state') != expected_state or not params.get('code'):
        return RedirectResponse('/?error=vencido', status_code=303)

    shared_app = get_shared_app()

    try:
        tokens = spotify.exchange_code(shared_app['client_id'], shared_app['client_secret'], params['code'], shared_app['redirect_uri'])
        profile = spotify.get_current_user(tokens['access_token'])
    except spotify.NotRegistered:
        response = templates.TemplateResponse(request, 'alta.html')
        return response
    except spotify.TokenRejected:
        return RedirectResponse('/?error=spotify', status_code=303)

    login = users.save_shared_login(conn, profile['id'], tokens['refresh_token'])
    conn.commit()

    request.session['user_id'] = login['user_id']
    request.session['display_name'] = profile.get('display_name') or profile['id']

    # el Aviso de reconexion se muestra una sola vez, en la pagina que sigue al callback (#19)
    if login['disconnected_at']:
        request.session['reconnected_since'] = login['disconnected_at'].strftime('%d/%m/%Y')

    response = RedirectResponse('/', status_code=303)
    return response


@app.post('/logout')
def logout(request: Request):
    '''
    Cierra la sesion web; el User sigue conectado y sus Runs siguen corriendo.
        Args:
            request (Request): request entrante
        Returns:
            response (RedirectResponse): redirect a la Pantalla de login
    '''
    request.session.clear()
    response = RedirectResponse('/', status_code=303)
    return response


@app.get('/health', response_class=PlainTextResponse)
def health():
    '''
    Responde ok sin tocar la DB, para el health check de Render.
        Returns:
            status (str): siempre ok
    '''
    return 'ok'


RETENCION_OPTIONS = [1, 2, 4, 8, 12]


def canal_url(canal_id, open_ids=None):
    '''
    Arma la URL de la vista de un Canal conservando las Filas de Seed Playlist desplegadas.
        Args:
            canal_id (int): id del Canal
            open_ids (str): ids de playlists desplegadas separados por coma
        Returns:
            url (str): path de la vista
    '''
    url = f'/canales/{canal_id}'

    if open_ids:
        url = f'{url}?{urlencode({"open": open_ids})}'

    return url


def back_to_canal(form, canal_id):
    '''
    Redirect a la vista del Canal despues de una accion, como dejo la pantalla el User.
        Args:
            form (dict): formulario enviado, con open opcional
            canal_id (int): id del Canal
        Returns:
            response (RedirectResponse): redirect 303
    '''
    response = RedirectResponse(canal_url(canal_id, form.get('open')), status_code=303)
    return response


def require_canal(conn, user, canal_id):
    '''
    Devuelve el Canal del User o corta el request si no es suyo o ya no existe.
        Args:
            conn (psycopg.Connection): conexion del request
            user (dict): fila de users
            canal_id (str): id del Canal, tal como viene en el path
        Returns:
            canal (dict): fila de canales
    '''
    # el id viene del path como texto: uno que no es numero es un Canal que no existe
    canal = canales.get_canal(conn, user['id'], int(canal_id)) if str(canal_id).isdigit() else None

    if canal is None:
        raise CanalMissing()

    return canal


def track_total(playlist):
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


@app.get('/canales/nuevo')
def new_canal(request: Request, user=Depends(require_user), conn=Depends(get_conn)):
    '''
    Vista Nuevo Canal: elegir la Target Playlist, nueva o una propia.
        Args:
            request (Request): request entrante, con tipo=nueva|mia
            user (dict): User de la sesion
            conn (psycopg.Connection): conexion del request
        Returns:
            response (TemplateResponse): html de la vista
    '''
    kind = 'mia' if request.query_params.get('tipo') == 'mia' else 'nueva'
    context = shell_context(request, conn, user, view='nuevo', kind=kind, error=request.session.pop('canal_error', None))

    if kind == 'mia':
        taken = {canal['target_playlist_id'] for canal in context['canales']}
        playlists = spotify.get_my_playlists(get_token(conn, user))
        # una Target tiene que ser propia: el bot solo puede escribir donde es owner
        context['own_playlists'] = [
            {'id': p['id'], 'name': p['name'], 'total': track_total(p), 'taken': p['id'] in taken}
            for p in playlists if p['owner']['id'] == user['spotify_user_id']
        ]

    response = templates.TemplateResponse(request, 'nuevo_canal.html', context)
    return response


@app.post('/canales')
def create_canal(request: Request, user=Depends(require_user), form=Depends(read_form), conn=Depends(get_conn)):
    '''
    Crea el Canal con su Target Playlist: la crea en Spotify o toma una propia.
        Args:
            request (Request): request entrante
            user (dict): User de la sesion
            form (dict): tipo, y name o playlist_id
            conn (psycopg.Connection): conexion del request
        Returns:
            response (RedirectResponse): redirect a la vista del Canal nuevo
    '''
    access_token = get_token(conn, user)

    try:
        # chequeo previo al POST a Spotify para no dejar una playlist huerfana si ya tiene 5 Canales
        if len(canales.list_canales(conn, user['id'])) >= canales.MAX_CANALES:
            raise canales.CanalError(f'Ya tenes {canales.MAX_CANALES} Canales, que es el maximo.')

        if form.get('tipo') == 'mia':
            playlists = spotify.get_my_playlists(access_token)
            playlist = next((p for p in playlists if p['id'] == form.get('playlist_id') and p['owner']['id'] == user['spotify_user_id']), None)
            if playlist is None:
                raise canales.CanalError('No encontramos esa playlist entre las tuyas.')
            canal_id = canales.create_canal(conn, user['id'], playlist['id'], playlist['name'], False)
        else:
            name = form.get('name', '')[:100]
            if not name:
                raise canales.CanalError('Ponele un nombre a la playlist.')
            playlist = spotify.create_playlist(access_token, name)
            canal_id = canales.create_canal(conn, user['id'], playlist['id'], playlist['name'], True)
    except canales.CanalError as error:
        request.session['canal_error'] = str(error)
        kind = 'mia' if form.get('tipo') == 'mia' else 'nueva'
        return RedirectResponse(f'/canales/nuevo?tipo={kind}', status_code=303)

    conn.commit()
    response = RedirectResponse(canal_url(canal_id), status_code=303)
    return response


@app.get('/canales/{canal_id}')
def show_canal(request: Request, canal_id, user=Depends(require_user), conn=Depends(get_conn)):
    '''
    Vista Canal: Columna Fuentes, Columna Reglas y Columna Whitelist.
        Args:
            request (Request): request entrante, con open opcional
            canal_id (str): id del Canal, tal como viene en el path
            user (dict): User de la sesion
            conn (psycopg.Connection): conexion del request
        Returns:
            response (TemplateResponse): html de la vista
    '''
    canal = require_canal(conn, user, canal_id)
    access_token = get_token(conn, user)
    playlists = spotify.get_my_playlists(access_token)
    canales.sync_target_names(conn, user['id'], playlists)
    conn.commit()

    open_ids = [pid for pid in request.query_params.get('open', '').split(',') if pid]
    sources = canales.get_sources(conn, canal['id'])
    seeds = {s['playlist_id']: s for s in sources if s['type'] == 'seed_playlist'}
    followed = next((s for s in sources if s['type'] == 'followed'), None)
    manual = next((s for s in sources if s['type'] == 'manual'), None)
    targets = {c['target_playlist_id']: c for c in canales.list_canales(conn, user['id'])}

    rows = []
    for playlist in playlists:
        # Spotify da 403 al leer items de playlists ajenas no colaborativas (feb 2026)
        usable = playlist['owner']['id'] == user['spotify_user_id'] or bool(playlist.get('collaborative'))
        source = seeds.get(playlist['id'])
        row = {
            'id': playlist['id'],
            'name': playlist['name'],
            'owner': playlist['owner'].get('display_name') or playlist['owner']['id'],
            'usable': usable,
            'source': source,
            'target_of': targets.get(playlist['id']),
            'open': playlist['id'] in open_ids,
            'artists': None,
        }

        # desplegada: si es fuente se lee de la DB; si no, de Spotify para que el User vea que aportaria
        if row['open'] and usable:
            if source:
                row['artists'] = canales.get_source_artists(conn, source['id'])
            else:
                found = spotify.get_playlist_artists(access_token, playlist['id'])
                row['artists'] = sorted(({'artist_id': k, **v} for k, v in found.items()), key=lambda a: (a['feat'], a['name'].lower()))

        rows.append(row)

    target = next((p for p in playlists if p['id'] == canal['target_playlist_id']), None)
    context = shell_context(
        request, conn, user,
        view=canal['id'],
        canal=canal,
        rows=rows,
        open_ids=','.join(open_ids),
        followed=followed,
        manual_artists=canales.get_source_artists(conn, manual['id']) if manual else [],
        whitelist=canales.get_whitelist(conn, canal['id']),
        target_found=target is not None,
        retencion_options=RETENCION_OPTIONS,
    )
    response = templates.TemplateResponse(request, 'canal.html', context)
    return response


def toggle_open(open_ids, playlist_id):
    '''
    Despliega o pliega una Fila de Seed Playlist en la lista de abiertas.
        Args:
            open_ids (str): ids abiertos separados por coma
            playlist_id (str): fila a alternar
        Returns:
            open_ids (str): lista nueva
    '''
    ids = [pid for pid in open_ids.split(',') if pid]
    ids = [pid for pid in ids if pid != playlist_id] if playlist_id in ids else ids + [playlist_id]
    return ','.join(ids)


templates.env.globals['toggle_open'] = toggle_open
templates.env.globals['canal_url'] = canal_url


@app.post('/canales/{canal_id}/seeds')
def toggle_seed(canal_id, user=Depends(require_user), form=Depends(read_form), conn=Depends(get_conn)):
    '''
    Prende o apaga una playlist como Seed Playlist del Canal.
        Args:
            canal_id (str): id del Canal, tal como viene en el path
            user (dict): User de la sesion
            form (dict): playlist_id, on (1 o 0) y open
            conn (psycopg.Connection): conexion del request
        Returns:
            response (RedirectResponse): vuelta a la vista del Canal
    '''
    canal = require_canal(conn, user, canal_id)
    playlist_id = form['playlist_id']

    if form.get('on') == '1':
        artists = spotify.get_playlist_artists(get_token(conn, user), playlist_id)
        canales.add_source(conn, canal['id'], 'seed_playlist', artists, playlist_id)
    else:
        canales.remove_source(conn, canal['id'], 'seed_playlist', playlist_id)

    conn.commit()
    return back_to_canal(form, canal['id'])


@app.post('/canales/{canal_id}/feats')
def toggle_feats(canal_id, user=Depends(require_user), form=Depends(read_form), conn=Depends(get_conn)):
    '''
    Prende o apaga los feats de una Seed Playlist.
        Args:
            canal_id (str): id del Canal, tal como viene en el path
            user (dict): User de la sesion
            form (dict): playlist_id, on (1 o 0) y open
            conn (psycopg.Connection): conexion del request
        Returns:
            response (RedirectResponse): vuelta a la vista del Canal
    '''
    canal = require_canal(conn, user, canal_id)
    canales.set_feats(conn, canal['id'], form['playlist_id'], form.get('on') == '1')
    conn.commit()
    return back_to_canal(form, canal['id'])


@app.post('/canales/{canal_id}/follows')
def toggle_follows(canal_id, user=Depends(require_user), form=Depends(read_form), conn=Depends(get_conn)):
    '''
    Prende o apaga los artistas seguidos como Artist Source.
        Args:
            canal_id (str): id del Canal, tal como viene en el path
            user (dict): User de la sesion
            form (dict): on (1 o 0) y open
            conn (psycopg.Connection): conexion del request
        Returns:
            response (RedirectResponse): vuelta a la vista del Canal
    '''
    canal = require_canal(conn, user, canal_id)

    if form.get('on') == '1':
        canales.add_source(conn, canal['id'], 'followed', spotify.get_followed_artists(get_token(conn, user)))
    else:
        canales.remove_source(conn, canal['id'], 'followed')

    conn.commit()
    return back_to_canal(form, canal['id'])


@app.get('/canales/{canal_id}/buscar', response_class=HTMLResponse)
def search_artists(request: Request, canal_id, user=Depends(require_user), conn=Depends(get_conn)):
    '''
    Resultados del Buscador de artistas, como fragmento para HTMX.
        Args:
            request (Request): request entrante, con q y open
            canal_id (str): id del Canal, tal como viene en el path
            user (dict): User de la sesion
            conn (psycopg.Connection): conexion del request
        Returns:
            response (TemplateResponse): fragmento con los resultados
    '''
    canal = require_canal(conn, user, canal_id)
    query = request.query_params.get('q', '').strip()
    results = []

    if query:
        manual = canales.find_source(conn, canal['id'], 'manual')
        chosen = {a['artist_id'] for a in canales.get_source_artists(conn, manual)} if manual else set()
        results = [a for a in spotify.search_artists(get_token(conn, user), query) if a['id'] not in chosen]

    context = {'canal': canal, 'query': query, 'results': results, 'open_ids': request.query_params.get('open', '')}
    response = templates.TemplateResponse(request, '_busqueda.html', context)
    return response


@app.post('/canales/{canal_id}/manual')
def add_manual(canal_id, user=Depends(require_user), form=Depends(read_form), conn=Depends(get_conn)):
    '''
    Suma un artista a mano al Canal.
        Args:
            canal_id (str): id del Canal, tal como viene en el path
            user (dict): User de la sesion
            form (dict): artist_id, name y open
            conn (psycopg.Connection): conexion del request
        Returns:
            response (RedirectResponse): vuelta a la vista del Canal
    '''
    canal = require_canal(conn, user, canal_id)
    canales.add_manual_artist(conn, canal['id'], form['artist_id'], form.get('name', ''))
    conn.commit()
    return back_to_canal(form, canal['id'])


@app.post('/canales/{canal_id}/manual/quitar')
def remove_manual(canal_id, user=Depends(require_user), form=Depends(read_form), conn=Depends(get_conn)):
    '''
    Saca un artista de la lista manual del Canal.
        Args:
            canal_id (str): id del Canal, tal como viene en el path
            user (dict): User de la sesion
            form (dict): artist_id y open
            conn (psycopg.Connection): conexion del request
        Returns:
            response (RedirectResponse): vuelta a la vista del Canal
    '''
    canal = require_canal(conn, user, canal_id)
    canales.remove_manual_artist(conn, canal['id'], form['artist_id'])
    conn.commit()
    return back_to_canal(form, canal['id'])


@app.post('/canales/{canal_id}/retencion')
def set_retencion(request: Request, canal_id, user=Depends(require_user), form=Depends(read_form), conn=Depends(get_conn)):
    '''
    Cambia la Retencion del Canal: N Lotes o Acumulativa.
        Args:
            request (Request): request entrante
            canal_id (str): id del Canal, tal como viene en el path
            user (dict): User de la sesion
            form (dict): retencion (numero o acum) y open
            conn (psycopg.Connection): conexion del request
        Returns:
            response (RedirectResponse): vuelta a la vista del Canal
    '''
    canal = require_canal(conn, user, canal_id)
    value = form.get('retencion')
    retencion_n = int(value) if value in {str(n) for n in RETENCION_OPTIONS} else None

    try:
        canales.set_retencion(conn, canal, retencion_n)
    except canales.CanalError as error:
        request.session['flash'] = str(error)

    conn.commit()
    return back_to_canal(form, canal['id'])


@app.post('/canales/{canal_id}/guardado')
def set_guardado(canal_id, user=Depends(require_user), form=Depends(read_form), conn=Depends(get_conn)):
    '''
    Prende o apaga el filtro de Guardado del Canal.
        Args:
            canal_id (str): id del Canal, tal como viene en el path
            user (dict): User de la sesion
            form (dict): on (1 o 0) y open
            conn (psycopg.Connection): conexion del request
        Returns:
            response (RedirectResponse): vuelta a la vista del Canal
    '''
    canal = require_canal(conn, user, canal_id)
    canales.set_guardado(conn, canal['id'], form.get('on') == '1')
    conn.commit()
    return back_to_canal(form, canal['id'])


@app.post('/canales/{canal_id}/desvincular')
def unlink_canal(request: Request, canal_id, user=Depends(require_user), form=Depends(read_form), conn=Depends(get_conn)):
    '''
    Desvincula el Canal y, si el User lo eligio, borra tambien la Target Playlist.
        Args:
            request (Request): request entrante
            canal_id (str): id del Canal, tal como viene en el path
            user (dict): User de la sesion
            form (dict): borrar_playlist (1 o 0)
            conn (psycopg.Connection): conexion del request
        Returns:
            response (RedirectResponse): redirect a Inicio
    '''
    canal = require_canal(conn, user, canal_id)
    delete_playlist = form.get('borrar_playlist') == '1'
    access_token = get_token(conn, user) if delete_playlist else None
    canales.delete_canal(conn, access_token, canal, delete_playlist)
    conn.commit()

    name = canal['target_name'] or 'el Canal'
    request.session['flash'] = f'Borramos "{name}" y su playlist.' if delete_playlist else f'Borramos "{name}". La playlist sigue en tu Spotify.'
    return RedirectResponse('/', status_code=303)
