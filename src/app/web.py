import os
import secrets
import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from psycopg.errors import LockNotAvailable, QueryCanceled
from psycopg_pool import PoolTimeout, TooManyRequests
from starlette.middleware.sessions import SessionMiddleware

from app import canales, entregas, runs, spotify, users
from app.config import get_env, get_shared_app
from app.crypto import decrypt_text, encrypt_text
from app.db import close_web_pool, connect, get_web_pool
from app.web_cache import WebCache

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


@asynccontextmanager
async def lifespan(app):
    '''Libera caches y conexiones cuando termina el servidor.'''
    try:
        yield
    finally:
        close_web_pool()
        spotify.close_http_client()
        spotify.clear_cooldowns()
        library_cache.clear()
        token_cache.clear()


# cookie firmada, no cifrada: guardamos solo el id del User y datos de pantalla, nunca tokens
app = FastAPI(lifespan=lifespan)
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
library_cache = WebCache()


@app.middleware('http')
async def interactive_spotify(request: Request, call_next):
    '''Evita reintentos largos de Spotify dentro de una accion de la web.'''
    with spotify.request_context():
        return await call_next(request)


def get_conn(request: Request):
    '''
    Toma una conexion del pool y commitea al terminar si no hubo error.
        Args:
            request (Request): request entrante
        Returns:
            conn (psycopg.Connection): conexion abierta
    '''
    public_page = request.url.path == '/' or (request.url.path == '/login' and not request.cookies.get('own_app'))
    if public_page and not request.session.get('user_id'):
        yield None
        return

    with get_web_pool().connection() as conn:
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


def home_redirect(request):
    '''Vuelve a Inicio tambien si el request apuntaba a un fragmento de la pagina.'''
    if request.headers.get('HX-Request') == 'true':
        return HTMLResponse('', status_code=200, headers={'HX-Redirect': '/'})
    return RedirectResponse('/', status_code=303)


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


def get_library(conn, user):
    '''
    Reutiliza la biblioteca del User durante un minuto entre acciones.
        Args:
            conn (psycopg.Connection): conexion del request
            user (dict): User de la sesion
        Returns:
            playlists (list): playlists del User
    '''
    return library_cache.get((user['id'], 'library'), lambda: spotify.get_my_playlists(get_token(conn, user)))


def get_preview_artists(conn, user, playlist):
    '''
    Reutiliza los artistas de una playlist mientras su snapshot siga vigente.
        Args:
            conn (psycopg.Connection): conexion del request
            user (dict): User de la sesion
            playlist (dict): playlist de la biblioteca
        Returns:
            artists (dict): artistas principales e invitados
    '''
    key = (user['id'], 'artists', playlist['id'], playlist.get('snapshot_id'))
    ttl = 300 if playlist.get('snapshot_id') else 60
    return library_cache.get(key, lambda: spotify.get_playlist_artists(get_token(conn, user), playlist['id']), ttl)


@app.exception_handler(LoginRequired)
def login_required(request, error):
    '''
    Manda a la Pantalla de login a quien no tiene sesion.
        Returns:
            response (RedirectResponse): redirect a /
    '''
    request.session.pop('user_id', None)
    return home_redirect(request)


@app.exception_handler(CanalMissing)
def canal_missing(request, error):
    '''
    Un Canal borrado (o de otro User) manda a Inicio sin cerrar la sesion.
        Returns:
            response (RedirectResponse): redirect a /
    '''
    return home_redirect(request)


@app.exception_handler(users.UserDisconnected)
def user_disconnected(request, error):
    '''
    Un User Desconectado vuelve a Inicio, donde ve el Aviso de desconexion.
        Returns:
            response (RedirectResponse): redirect a /
    '''
    return home_redirect(request)


@app.exception_handler(httpx.HTTPError)
def spotify_failed(request, error):
    '''
    Si Spotify falla a mitad de una accion, vuelve a Inicio con un Aviso en vez de un 500.
        Returns:
            response (RedirectResponse): redirect a /
    '''
    return action_failed(request, 'Spotify no respondio bien. Proba de nuevo en un rato.')


def action_failed(request, message, retry=None):
    '''
    Conserva la pantalla interactiva y muestra un Aviso cuando falla una accion.
        Args:
            request (Request): request entrante
            message (str): aviso para el User
            retry (int): segundos hasta que puede reintentar, si corresponde
        Returns:
            response (Response): aviso parcial o redirect para navegacion sin JS
    '''
    if request.headers.get('HX-Request') == 'true':
        response = templates.TemplateResponse(request, '_action_notice.html', {'message': message})
        response.headers.update({'HX-Retarget': '#action-notice', 'HX-Reswap': 'innerHTML'})
    else:
        request.session['flash'] = message
        response = RedirectResponse('/', status_code=303)
    if retry is not None:
        response.headers['Retry-After'] = str(retry)
    return response


@app.exception_handler(spotify.RateLimited)
@app.exception_handler(spotify.QuotaExceeded)
def spotify_limited(request, error):
    '''Traduce los limites de Spotify a un Aviso sin bloquear la accion.'''
    message = 'Spotify alcanzo su cuota de consultas. Proba de nuevo mas tarde.' if isinstance(error, spotify.QuotaExceeded) else 'Spotify pidio una pausa. Espera un momento antes de volver a intentar.'
    return action_failed(request, message, int(error.args[0]) if error.args else 1)


@app.exception_handler(PoolTimeout)
@app.exception_handler(TooManyRequests)
@app.exception_handler(TimeoutError)
@app.exception_handler(LockNotAvailable)
@app.exception_handler(QueryCanceled)
def service_busy(request, error):
    '''Avisa si la carga o la espera por una conexion excedio su limite.'''
    if request.headers.get('HX-Request') != 'true':
        return templates.TemplateResponse(request, 'busy.html', {}, status_code=503, headers={'Retry-After': '3'})
    return action_failed(request, 'La app esta ocupada. Proba de nuevo en unos segundos.')


def get_cap_warnings(user_canales):
    '''
    Arma los Avisos de tope de las Target Playlists que pasaron el 90% (ADR 0004).
        Args:
            user_canales (list): filas de canales con target_total
        Returns:
            warnings (list): dicts con level (warn o crit) y text
    '''
    warnings = []

    for canal in user_canales:
        cap = entregas.BOT_CAP if canal['target_created_by_bot'] else entregas.PLAYLIST_CAP
        share = canal['target_total'] / cap

        if share < 0.9:
            continue

        percent = 95 if share >= 0.95 else 90
        level = 'crit' if percent == 95 else 'warn'
        cap_text = f'{cap:,}'.replace(',', '.')

        # la del bot se recorta sola; en una del User lo que no entra se pierde
        if canal['target_created_by_bot']:
            text = f'{canal["target_name"]} esta al {percent}% de {cap_text} canciones. Desde ahi retiramos sus Lotes mas viejos para hacer lugar.'
        else:
            text = f'{canal["target_name"]} esta al {percent}% del tope de {cap_text} canciones de Spotify. Cuando se llene, lo nuevo deja de entrar.'

        warnings.append({'level': level, 'text': text})

    return warnings


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
    user_canales = extra.pop('user_canales', None)
    if user_canales is None:
        user_canales = canales.list_canales(conn, user['id'])
    context = {
        'display_name': request.session.get('display_name') or user['spotify_user_id'],
        'disconnected': user['disconnected_at'] is not None,
        'reconnected_since': request.session.pop('reconnected_since', None),
        'flash': request.session.pop('flash', None),
        'canales': user_canales,
        'cap_warnings': get_cap_warnings(user_canales),
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


def run_in_background(user_id):
    '''
    Corre el Run de un User fuera del request, con su propia conexion: con Whitelists grandes tarda minutos.
        Args:
            user_id (int): id interno del User
    '''
    with spotify.request_context(interactive=False), connect() as conn:
        user = users.get_user(conn, user_id)

        try:
            report = runs.run_user(conn, user)
        except runs.RunBusy:
            return
        except Exception:
            # la fila Run ya quedo fallida y el Tick lo reintenta; dejamos la traza en el log de Render
            traceback.print_exc()
            return

        for failure in report['failures']:
            traceback.print_exception(failure['error'])


# FastAPI necesita la anotacion BackgroundTasks para inyectarlo, igual que con Request
@app.post('/correr')
def run_now(request: Request, background_tasks: BackgroundTasks, user=Depends(require_user)):
    '''
    Boton Buscar novedades: dispara el mismo Run que corre el Tick, en segundo plano.
        Args:
            request (Request): request entrante
            background_tasks (BackgroundTasks): cola de tareas que corren despues de responder
            user (dict): User de la sesion
        Returns:
            response (RedirectResponse): vuelta a Inicio con un Aviso de accion
    '''
    background_tasks.add_task(run_in_background, user['id'])
    request.session['flash'] = 'Estamos buscando novedades de tus artistas. Lo que aparezca entra en la Entrega del viernes.'
    return RedirectResponse('/', status_code=303)


ALTA_ERRORS = {
    'vacio': 'Pega el Client ID y el Client secret para seguir.',
    'claves': 'Spotify no reconoce ese Client ID con ese Client secret. Copialos de nuevo desde Settings de tu app.',
    'vencidas': 'Tu app de Spotify dejo de andar: se borro o le cambiaron el secret. Pega las claves nuevas para volver a conectar.',
    'allowlist': 'Tu app no te dejo entrar. Revisa que tu email de Spotify este en User Management y que tengas Premium.',
    'spotify': 'Spotify no respondio bien. Proba de nuevo en un rato.',
}

# cookie del navegador que recuerda la Own App de quien vuelve sin sesion (#18); el client id no es secreto
OWN_APP_COOKIE = 'own_app'
OWN_APP_COOKIE_AGE = 60 * 60 * 24 * 365


def render_alta(request, error=None, client_id=''):
    '''
    Arma la Pantalla de alta: Pasos de Own App con su Formulario de claves y el Aviso de habilitacion.
        Args:
            request (Request): request entrante
            error (str): clave de ALTA_ERRORS, o None
            client_id (str): Client ID para dejar cargado en el Formulario de claves
        Returns:
            response (TemplateResponse): html de la pagina
    '''
    context = {
        'redirect_uri': get_env('SPOTIFY_REDIRECT_URI'),
        'error': ALTA_ERRORS.get(error),
        'client_id': client_id,
    }
    response = templates.TemplateResponse(request, 'alta.html', context)
    return response


def authorize(request, client_id, own_app=None):
    '''
    Manda al User a autorizar una Spotify App y deja en la sesion lo que necesita el callback.
        Args:
            request (Request): request entrante
            client_id (str): client id de la Shared App o de su Own App
            own_app (dict): client_id y client_secret_enc de su Own App; None para la Shared App
        Returns:
            response (RedirectResponse): redirect a accounts.spotify.com
    '''
    state = secrets.token_urlsafe(16)
    request.session['oauth_state'] = state

    # el secret viaja cifrado con Fernet: la cookie de sesion esta firmada pero se puede leer
    if own_app:
        request.session['own_app'] = own_app
    else:
        request.session.pop('own_app', None)

    authorize_url = spotify.build_authorize_url(client_id, get_env('SPOTIFY_REDIRECT_URI'), state)
    response = RedirectResponse(authorize_url, status_code=303)
    return response


def find_returning_own_app(request, conn):
    '''
    Reconoce a un User de Own App que vuelve: por su sesion, o si no tiene, por la cookie del navegador.
        Args:
            request (Request): request entrante
            conn (psycopg.Connection): conexion del request
        Returns:
            own_app (dict): client_id y client_secret_enc guardados, o None si entra por la Shared App
    '''
    user_id = request.session.get('user_id')
    user = users.get_user(conn, user_id) if user_id else None

    if user and user['connection_mode'] == 'own':
        own_app = {'client_id': user['client_id'], 'client_secret_enc': user['client_secret_enc']}
        return own_app

    client_id = request.cookies.get(OWN_APP_COOKIE)
    own_app = users.find_own_app(conn, client_id) if client_id else None
    return own_app


@app.get('/login')
def login(request: Request, conn=Depends(get_conn)):
    '''
    Boton Conectar: autoriza con la Own App si reconoce al User, si no con la Shared App.
        Args:
            request (Request): request entrante
            conn (psycopg.Connection): conexion del request
        Returns:
            response (RedirectResponse): redirect a accounts.spotify.com, o a la Pantalla de alta si su Own App murio
    '''
    own_app = find_returning_own_app(request, conn)

    if own_app is None:
        return authorize(request, get_shared_app()['client_id'])

    # con claves muertas Spotify muestra una pagina de error propia: mejor pedir las nuevas antes (#19)
    try:
        valid = spotify.check_client(own_app['client_id'], decrypt_text(own_app['client_secret_enc']))
    except (httpx.HTTPError, spotify.TokenRejected):
        return RedirectResponse('/?error=spotify', status_code=303)

    if not valid:
        return RedirectResponse('/alta?error=vencidas', status_code=303)

    response = authorize(request, own_app['client_id'], own_app)
    return response


@app.get('/alta')
def alta(request: Request):
    '''
    Pantalla de alta: para quien la Shared App no deja entrar o cuya Own App necesita claves nuevas.
        Args:
            request (Request): request entrante, con error opcional
        Returns:
            response (TemplateResponse): html de la pagina
    '''
    response = render_alta(request, request.query_params.get('error'), request.session.pop('alta_client_id', ''))
    return response


@app.post('/own-app')
def connect_own_app(request: Request, form=Depends(read_form)):
    '''
    Formulario de claves: valida el client id + secret de la Own App y manda a autorizarla.
        Args:
            request (Request): request entrante
            form (dict): client_id y client_secret
        Returns:
            response (Response): redirect a accounts.spotify.com, o la Pantalla de alta con el error
    '''
    client_id = form.get('client_id', '')
    client_secret = form.get('client_secret', '')

    if not client_id or not client_secret:
        return render_alta(request, 'vacio', client_id)

    try:
        valid = spotify.check_client(client_id, client_secret)
    except (httpx.HTTPError, spotify.TokenRejected):
        return render_alta(request, 'spotify', client_id)

    if not valid:
        return render_alta(request, 'claves', client_id)

    response = authorize(request, client_id, {'client_id': client_id, 'client_secret_enc': encrypt_text(client_secret)})
    return response


@app.get('/callback')
def callback(request: Request, conn=Depends(get_conn)):
    '''
    Recibe la vuelta de Spotify: canjea el code, crea o reconecta al User y abre la sesion.
        Args:
            request (Request): request entrante con code y state, o error
            conn (psycopg.Connection): conexion del request
        Returns:
            response (RedirectResponse): redirect al dashboard, o a la Pantalla de alta si Spotify no lo deja entrar
    '''
    params = request.query_params
    expected_state = request.session.pop('oauth_state', None)
    own_app = request.session.pop('own_app', None)

    if params.get('error'):
        return RedirectResponse('/?error=cancelado', status_code=303)

    # sin state guardado tambien cae aca: la cookie se pierde si se abre localhost en vez de 127.0.0.1
    if not expected_state or params.get('state') != expected_state or not params.get('code'):
        return RedirectResponse('/?error=vencido', status_code=303)

    # el code se canjea con el mismo client que lo pidio
    if own_app:
        client_id, client_secret = own_app['client_id'], decrypt_text(own_app['client_secret_enc'])
    else:
        shared_app = get_shared_app()
        client_id, client_secret = shared_app['client_id'], shared_app['client_secret']

    try:
        tokens = spotify.exchange_code(client_id, client_secret, params['code'], get_env('SPOTIFY_REDIRECT_URI'))
        profile = spotify.get_current_user(tokens['access_token'])
    except spotify.NotRegistered:
        # en una Own App el 403 es su propio User Management o su Premium, no la lista de Luca
        if own_app:
            request.session['alta_client_id'] = client_id
            return RedirectResponse('/alta?error=allowlist', status_code=303)
        return RedirectResponse('/alta', status_code=303)
    except spotify.TokenRejected:
        return RedirectResponse('/?error=spotify', status_code=303)

    login = users.save_login(conn, profile['id'], tokens['refresh_token'], own_app)
    conn.commit()

    # el access token cacheado puede ser del client anterior si cambio de modo
    token_cache.pop(login['user_id'], None)
    library_cache.clear(login['user_id'])
    request.session['user_id'] = login['user_id']
    request.session['display_name'] = profile.get('display_name') or profile['id']

    # el Aviso de reconexion se muestra una sola vez, en la pagina que sigue al callback (#19)
    if login['disconnected_at']:
        request.session['reconnected_since'] = login['disconnected_at'].strftime('%d/%m/%Y')

    response = RedirectResponse('/', status_code=303)

    if own_app:
        response.set_cookie(OWN_APP_COOKIE, client_id, max_age=OWN_APP_COOKIE_AGE, httponly=True, samesite='lax', secure=bool(os.environ.get('RENDER')))

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
    user_id = request.session.get('user_id')
    if user_id is not None:
        library_cache.clear(user_id)
        token_cache.pop(user_id, None)
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
        playlists = get_library(conn, user)
        # una Target tiene que ser propia: el bot solo puede escribir donde es owner
        context['own_playlists'] = [
            {'id': p['id'], 'name': p['name'], 'total': spotify.get_track_total(p), 'taken': p['id'] in taken}
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
    library_cache.clear(user['id'])
    response = RedirectResponse(canal_url(canal_id), status_code=303)
    return response


@app.get('/canales/{canal_id}')
def show_canal(request: Request, canal_id, user=Depends(require_user), conn=Depends(get_conn)):
    '''Muestra la pagina completa de un Canal.'''
    return render_canal(request, canal_id, user, conn)


def render_canal(request, canal_id, user, conn, open_ids=None, partial=False):
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
    playlists = get_library(conn, user)
    user_canales = canales.list_canales(conn, user['id'])
    user_canales = canales.sync_target_names(conn, user['id'], playlists, user_canales)
    canal = next((item for item in user_canales if item['id'] == canal['id']), canal)
    conn.commit()

    open_ids = [pid for pid in (open_ids if open_ids is not None else request.query_params.get('open', '')).split(',') if pid]
    sources = canales.get_sources(conn, canal['id'])
    seeds = {s['playlist_id']: s for s in sources if s['type'] == 'seed_playlist'}
    followed = next((s for s in sources if s['type'] == 'followed'), None)
    manual = next((s for s in sources if s['type'] == 'manual'), None)
    targets = {c['target_playlist_id']: c for c in user_canales}

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
                found = get_preview_artists(conn, user, playlist)
                row['artists'] = sorted(({'artist_id': k, **v} for k, v in found.items()), key=lambda a: (a['feat'], a['name'].lower()))

        rows.append(row)

    target = next((p for p in playlists if p['id'] == canal['target_playlist_id']), None)
    context = shell_context(
        request, conn, user,
        user_canales=user_canales,
        view=canal['id'],
        canal=canal,
        rows=rows,
        open_ids=','.join(open_ids),
        followed=followed,
        manual_artists=canales.get_source_artists(conn, manual['id']) if manual else [],
        whitelist=canales.get_whitelist(conn, canal['id']),
        whitelist_count=canal['whitelist_count'],
        target_found=target is not None,
        retencion_options=RETENCION_OPTIONS,
    )
    response = templates.TemplateResponse(request, '_canal_response.html' if partial else 'canal.html', context)
    return response


def finish_canal_change(request, conn, user, canal_id, form, rules_only=False):
    '''
    Devuelve solo la parte modificada del Canal cuando el formulario usa HTMX.
        Args:
            request (Request): request entrante
            conn (psycopg.Connection): conexion del request
            user (dict): User autenticado
            canal_id (int): Canal modificado
            form (dict): formulario con filas abiertas
            rules_only (bool): evita cargar Fuentes al modificar Reglas
        Returns:
            response (Response): fragmento o redirect para formularios sin JS
    '''
    if request.headers.get('HX-Request') != 'true':
        return back_to_canal(form, canal_id)
    if rules_only:
        canal = require_canal(conn, user, canal_id)
        user_canales = canales.list_canales(conn, user['id'])
        count = next((item['whitelist_count'] for item in user_canales if item['id'] == canal['id']), 0)
        context = {'canal': canal, 'whitelist_count': count, 'open_ids': form.get('open', ''), 'retencion_options': RETENCION_OPTIONS, 'flash': request.session.pop('flash', None)}
        return templates.TemplateResponse(request, '_canal_rules_response.html', context)
    return render_canal(request, canal_id, user, conn, form.get('open', ''), partial=True)


@app.get('/canales/{canal_id}/playlists/{playlist_id}', response_class=HTMLResponse)
def preview_playlist(request: Request, canal_id, playlist_id, user=Depends(require_user), conn=Depends(get_conn)):
    '''Devuelve solo los artistas de la playlist que el User acaba de desplegar.'''
    canal = require_canal(conn, user, canal_id)
    playlist = next((item for item in get_library(conn, user) if item['id'] == playlist_id), None)
    if playlist is None or not (playlist['owner']['id'] == user['spotify_user_id'] or playlist.get('collaborative')):
        return action_failed(request, 'No encontramos esa playlist entre las que podes usar.')
    source = next((item for item in canales.get_sources(conn, canal['id']) if item['type'] == 'seed_playlist' and item['playlist_id'] == playlist_id), None)
    if source:
        artists = canales.get_source_artists(conn, source['id'])
    else:
        found = get_preview_artists(conn, user, playlist)
        artists = sorted(({'artist_id': key, **value} for key, value in found.items()), key=lambda artist: (artist['feat'], artist['name'].lower()))
    context = {'canal': canal, 'row': {'id': playlist_id, 'artists': artists, 'source': source}, 'open_ids': request.query_params.get('open', '')}
    return templates.TemplateResponse(request, '_playlist_artists.html', context)


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
def toggle_seed(request: Request, canal_id, user=Depends(require_user), form=Depends(read_form), conn=Depends(get_conn)):
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
        playlist = next((item for item in get_library(conn, user) if item['id'] == playlist_id), None)
        if playlist is None or not (playlist['owner']['id'] == user['spotify_user_id'] or playlist.get('collaborative')):
            return action_failed(request, 'No encontramos esa playlist entre las que podes usar.')
        artists = get_preview_artists(conn, user, playlist)
        canales.add_source(conn, canal['id'], 'seed_playlist', artists, playlist_id)
    else:
        canales.remove_source(conn, canal['id'], 'seed_playlist', playlist_id)

    conn.commit()
    return finish_canal_change(request, conn, user, canal['id'], form)


@app.post('/canales/{canal_id}/feats')
def toggle_feats(request: Request, canal_id, user=Depends(require_user), form=Depends(read_form), conn=Depends(get_conn)):
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
    return finish_canal_change(request, conn, user, canal['id'], form)


@app.post('/canales/{canal_id}/follows')
def toggle_follows(request: Request, canal_id, user=Depends(require_user), form=Depends(read_form), conn=Depends(get_conn)):
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
        artists = library_cache.get((user['id'], 'followed'), lambda: spotify.get_followed_artists(get_token(conn, user)))
        canales.add_source(conn, canal['id'], 'followed', artists)
    else:
        canales.remove_source(conn, canal['id'], 'followed')

    conn.commit()
    return finish_canal_change(request, conn, user, canal['id'], form)


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
def add_manual(request: Request, canal_id, user=Depends(require_user), form=Depends(read_form), conn=Depends(get_conn)):
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
    return finish_canal_change(request, conn, user, canal['id'], form)


@app.post('/canales/{canal_id}/manual/quitar')
def remove_manual(request: Request, canal_id, user=Depends(require_user), form=Depends(read_form), conn=Depends(get_conn)):
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
    return finish_canal_change(request, conn, user, canal['id'], form)


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
    return finish_canal_change(request, conn, user, canal['id'], form, rules_only=True)


@app.post('/canales/{canal_id}/guardado')
def set_guardado(request: Request, canal_id, user=Depends(require_user), form=Depends(read_form), conn=Depends(get_conn)):
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
    return finish_canal_change(request, conn, user, canal['id'], form, rules_only=True)


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
    library_cache.clear(user['id'])

    name = canal['target_name'] or 'el Canal'
    request.session['flash'] = f'Borramos "{name}" y su playlist.' if delete_playlist else f'Borramos "{name}". La playlist sigue en tu Spotify.'
    return RedirectResponse('/', status_code=303)
