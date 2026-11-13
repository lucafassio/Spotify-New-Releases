import hashlib
import os
import secrets
import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Request, Response
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from psycopg.errors import LockNotAvailable, QueryCanceled
from psycopg.rows import dict_row
from psycopg_pool import PoolTimeout, TooManyRequests
from starlette.middleware.sessions import SessionMiddleware

from app import canales, copias, deezer, entregas, runs, spotify, users
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
templates.env.filters['artist_key'] = copias.artist_sort_key
def artist_photo(artist):
    '''
    Foto para el avatar de un artista: la de Spotify si vino, si no la de Deezer guardada (#23). Nunca una url de este servidor.
        Args:
            artist (dict): fila con opcionales image y photo_url
        Returns:
            url (str): url de la foto, o None si todavia no la tenemos
    '''
    url = artist.get('image') or artist.get('photo_url') or None
    return url


def photo_lookup(artist):
    '''
    Id que static/fotos.js usa para pedir la foto de un avatar recien cuando la pagina esta quieta.
        Args:
            artist (dict): fila con name, artist_id (id en el Buscador) y opcionales image, photo_url y photo_known
        Returns:
            artist_id (str): id de Spotify del artista, o None si ya tiene foto o hace menos de un dia que no tiene
    '''
    if artist_photo(artist) or artist.get('photo_known') or not artist.get('name'):
        return None

    artist_id = artist.get('artist_id') or artist.get('id')
    return artist_id


templates.env.filters['artist_photo'] = artist_photo
templates.env.filters['photo_lookup'] = photo_lookup


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
        search_cache.clear()
        token_cache.clear()


STATIC_DIR = Path(__file__).parent / 'static'


def get_static_version(name):
    '''
    Version de un archivo de /static a partir de su contenido; se calcula una vez y queda guardada.
        Args:
            name (str): path relativo a static con barras normales, por ejemplo 'app.js'
        Returns:
            version (str): primeros 10 caracteres del sha256, o None si el archivo no existe
    '''
    if name not in STATIC_VERSIONS:
        path = STATIC_DIR / name
        STATIC_VERSIONS[name] = hashlib.sha256(path.read_bytes()).hexdigest()[:10] if path.is_file() else None
    return STATIC_VERSIONS[name]


# todo archivo de /static (subcarpetas incluidas) al arrancar; uno que aparezca despues se calcula al pedirlo
STATIC_VERSIONS = {}
for static_path in STATIC_DIR.rglob('*'):
    if static_path.is_file():
        get_static_version(static_path.relative_to(STATIC_DIR).as_posix())


def static_url(name):
    '''
    URL de cualquier archivo de /static con su version: cambia cuando cambia el contenido y el navegador baja el nuevo.
        Args:
            name (str): path relativo a static, por ejemplo 'app.js' o 'flechas.js'
        Returns:
            url (str): path con ?v=version, o sin version si el archivo no existe
    '''
    version = get_static_version(name)
    url = f'/static/{name}?v={version}' if version else f'/static/{name}'
    return url


templates.env.globals['static_url'] = static_url


class VersionedStaticFiles(StaticFiles):
    '''Sirve /static con cache de un anio solo si la URL trae la version vigente del archivo.'''

    async def get_response(self, path, scope):
        '''
        Agrega Cache-Control inmutable a las URLs versionadas; las demas quedan con la revalidacion de siempre.
            Args:
                path (str): archivo pedido, relativo a static
                scope (dict): scope ASGI del request
            Returns:
                response (Response): archivo o error de StaticFiles
        '''
        response = await super().get_response(path, scope)
        version = parse_qs(scope.get('query_string', b'').decode()).get('v', [''])[0]

        # una version vieja recibe el archivo nuevo: no se cachea para no fijarlo bajo una URL equivocada
        # en Windows StaticFiles arma el path con barras invertidas
        if response.status_code == 200 and version and version == get_static_version(path.replace('\\', '/')):
            response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'

        return response


# cookie firmada, no cifrada: guardamos solo el id del User y datos de pantalla, nunca tokens
app = FastAPI(lifespan=lifespan)
# manifest e iconos para instalar la web como app; CSS, JS, htmx y sus extensiones con version en la URL
app.mount('/static', VersionedStaticFiles(directory=STATIC_DIR), name='static')
SESSION_AGE = 60 * 60 * 24 * 30
SESSION_RENEW_AGE = 60 * 60
# el navegador los pide solo, sin que el User haga nada: no cuentan como entrar ni reescriben la cookie
NO_RENEW_PREFIXES = ('/static/', '/artistas/fotos')


# va antes de SessionMiddleware para quedar adentro y ver request.session
@app.middleware('http')
async def renew_session(request: Request, call_next):
    '''Renueva la cookie como mucho una vez por hora: la sesion vence solo tras 30 dias sin entrar.'''
    # sin leer la sesion los estaticos tampoco salen con Vary: Cookie
    if request.url.path.startswith(NO_RENEW_PREFIXES):
        return await call_next(request)
    # una cookie distinta en cada respuesta rompe el Vary: Cookie y la vista precargada nunca se reusa
    if request.session.get('user_id') and time.time() - request.session.get('seen', 0) > SESSION_RENEW_AGE:
        request.session['seen'] = int(time.time())
    return await call_next(request)


# Render define RENDER en el entorno: alla todo es https y la cookie no viaja por http
app.add_middleware(
    SessionMiddleware,
    secret_key=get_env('SESSION_SECRET'),
    same_site='lax',
    max_age=SESSION_AGE,
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
# el Buscador repite textos al borrar y reescribir: 10 minutos sin volver a Spotify
SEARCH_TTL = 10 * 60
SEARCH_MIN_CHARS = 2
search_cache = WebCache(limit=256)

# GET /me/playlists tarda 30 a 60 s en reflejar una edicion (ADR 0008): abrir varias playlists seguidas la pide una vez (#24)
LIBRARY_TTL = 5 * 60


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
    Reutiliza la biblioteca del User durante cinco minutos entre acciones; cada vez que la trae de Spotify deja al dia las copias y los nombres de las Targets.
        Args:
            conn (psycopg.Connection): conexion del request
            user (dict): User de la sesion
        Returns:
            playlists (list): playlists del User
    '''
    def load():
        playlists = spotify.get_my_playlists(get_token(conn, user))
        copias.save_library(conn, user['id'], playlists)
        canales.sync_target_names(conn, user['id'], playlists)
        conn.commit()
        return playlists

    return library_cache.get((user['id'], 'library'), load, ttl=LIBRARY_TTL)


def sync_library(conn, user):
    '''
    Revisa la biblioteca en Spotify, con los cinco minutos de cache; las copias ya quedaron guardadas cuando se trajo, asi un acierto no toca la base.
        Args:
            conn (psycopg.Connection): conexion del request
            user (dict): User de la sesion
        Returns:
            playlists (list): playlists del User
    '''
    playlists = get_library(conn, user)
    return playlists


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
    message, retry = limit_notice(error)
    return action_failed(request, message, retry)


def limit_notice(error):
    '''
    Texto del Aviso y segundos de espera de un limite de Spotify.
        Args:
            error (Exception): RateLimited o QuotaExceeded
        Returns:
            message (str): aviso para el User
            retry (int): segundos hasta que puede reintentar
    '''
    message = 'Spotify alcanzo su cuota de consultas. Proba de nuevo mas tarde.' if isinstance(error, spotify.QuotaExceeded) else 'Spotify pidio una pausa. Espera un momento antes de volver a intentar.'
    retry = int(error.args[0]) if error.args and error.args[0] is not None else 1
    return message, retry


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


# lado del avatar del Perfil al doble, para pantallas de alta densidad
PROFILE_SIZE = 64


def get_profile_image(request, conn, user):
    '''
    Foto de perfil del User: se guarda en la sesion al loguear, y una sesion de antes la pide una sola vez a GET /me.
        Args:
            request (Request): request entrante
            conn (psycopg.Connection): conexion del request
            user (dict): fila de users
        Returns:
            url (str): url de la foto, o None si no tiene o no se pudo leer
    '''
    if 'avatar' not in request.session and user['disconnected_at'] is None:
        try:
            profile = spotify.get_current_user(get_token(conn, user))
        except (*copias.READ_ERRORS, spotify.TokenRejected, users.UserDisconnected):
            return None
        request.session['avatar'] = spotify.get_image_url(profile.get('images'), PROFILE_SIZE) or ''

    url = request.session.get('avatar') or None
    return url


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
        'avatar': get_profile_image(request, conn, user),
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


# segundos que el navegador guarda una vista precargada; navegacion.js usa el mismo numero
PRELOAD_AGE = 10


def render_view(request, name, context):
    '''
    Arma una vista del dashboard: la pagina entera al entrar por URL, o solo el Panel y la Barra lateral en un click boosteado (va a #vista).
        Args:
            request (Request): request entrante
            name (str): template de la vista, extiende shell.html
            context (dict): contexto armado con shell_context
        Returns:
            response (TemplateResponse): pagina o fragmento
    '''
    # el back sin snapshot de htmx pide la pagina para reemplazar todo el body
    partial = request.headers.get('HX-Boosted') == 'true' and request.headers.get('HX-History-Restore-Request') != 'true'
    response = templates.TemplateResponse(request, name, dict(context, vista_parcial=partial))
    # la misma URL devuelve pagina o fragmento: la cache del navegador tiene que separarlas
    response.headers.add_vary_header('HX-Boosted')
    response.headers.add_vary_header('HX-History-Restore-Request')

    if partial:
        # solo la precarga se guarda, y la usa el click que la sigue
        preloaded = request.headers.get('HX-Preloaded') == 'true'
        response.headers['Cache-Control'] = f'private, max-age={PRELOAD_AGE}' if preloaded else 'no-store'

    return response


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
        # un click boosteado swapea en #vista: la Pantalla de login tiene que cargarse entera
        if request.headers.get('HX-Boosted') == 'true':
            return HTMLResponse('', headers={'HX-Redirect': '/'})
        error = LOGIN_ERRORS.get(request.query_params.get('error'))
        response = templates.TemplateResponse(request, 'login.html', {'error': error})
        return response

    context = shell_context(request, conn, user, view='inicio')
    response = render_view(request, 'dashboard.html', context)
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
    search_cache.clear(login['user_id'])
    request.session['user_id'] = login['user_id']
    request.session['display_name'] = profile.get('display_name') or profile['id']
    request.session['avatar'] = spotify.get_image_url(profile.get('images'), PROFILE_SIZE) or ''

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
        search_cache.clear(user_id)
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


def canal_url(canal_id, open_id=None):
    '''
    Arma la URL de la vista de un Canal conservando la Fila de playlist abierta.
        Args:
            canal_id (int): id del Canal
            open_id (str): id de la playlist abierta
        Returns:
            url (str): path de la vista
    '''
    url = f'/canales/{canal_id}'

    if open_id:
        url = f'{url}?{urlencode({"open": open_id})}'

    return url


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
            {'id': p['id'], 'name': p['name'], 'total': spotify.get_track_total(p), 'image': spotify.get_image_url(p.get('images'), copias.COVER_SIZE), 'taken': p['id'] in taken}
            for p in playlists if p['owner']['id'] == user['spotify_user_id']
        ]

    response = render_view(request, 'nuevo_canal.html', context)
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
    own = form.get('tipo') == 'mia'

    try:
        # chequeo previo al POST a Spotify para no dejar una playlist huerfana si ya tiene 5 Canales
        if len(canales.list_canales(conn, user['id'])) >= canales.MAX_CANALES:
            raise canales.CanalError(f'Ya tenes {canales.MAX_CANALES} Canales, que es el maximo.')

        if own:
            # la misma biblioteca que acaba de listar la Vista Nuevo Canal, con su minuto de cache
            playlists = get_library(conn, user)
            playlist = next((p for p in playlists if p['id'] == form.get('playlist_id') and p['owner']['id'] == user['spotify_user_id']), None)
            if playlist is None:
                raise canales.CanalError('No encontramos esa playlist entre las tuyas.')
            canal_id = canales.create_canal(conn, user['id'], playlist['id'], playlist['name'], False)
        else:
            name = form.get('name', '')[:100]
            if not name:
                raise canales.CanalError('Ponele un nombre a la playlist.')
            playlist = spotify.create_playlist(get_token(conn, user), name)
            canal_id = canales.create_canal(conn, user['id'], playlist['id'], playlist['name'], True)
    except canales.CanalError as error:
        request.session['canal_error'] = str(error)
        kind = 'mia' if own else 'nueva'
        return RedirectResponse(f'/canales/nuevo?tipo={kind}', status_code=303)

    conn.commit()
    # solo una playlist recien creada cambia la biblioteca en Spotify
    if not own:
        library_cache.clear(user['id'])
    response = RedirectResponse(canal_url(canal_id), status_code=303)
    return response


@app.get('/canales/{canal_id}')
def show_canal(request: Request, canal_id, user=Depends(require_user), conn=Depends(get_conn)):
    '''Muestra la pagina completa de un Canal.'''
    canal = require_canal(conn, user, canal_id)
    copies = load_copies(conn, user, canal)
    uses_seed = canales.uses_seed(conn, canal['id'])
    whitelist = canales.get_whitelist(conn, canal['id'])
    rows = [dict(copy, usable=is_usable(copy, user, canal)) for copy in copies]
    open_id = request.query_params.get('open', '')
    opened = next((row for row in rows if row['playlist_id'] == open_id and row['usable']), None) if not uses_seed else None
    context = canal_context(
        request, conn, user, canal, copies,
        section='canal',
        rows=[row for row in rows if row['usable']] + [row for row in rows if not row['usable']],
        uses_seed=uses_seed,
        whitelist=whitelist,
        open_id=opened['playlist_id'] if opened else None,
        open_artists=visible_artists(conn, user, opened, whitelist) if opened else None,
    )
    response = render_view(request, 'canal.html', context)
    return response


@app.get('/canales/{canal_id}/ajustes')
def show_settings(request: Request, canal_id, user=Depends(require_user), conn=Depends(get_conn)):
    '''Muestra la pagina completa de la Vista Ajustes.'''
    return render_settings(request, canal_id, user, conn)


def load_copies(conn, user, canal):
    '''
    Devuelve la biblioteca guardada; revisa Spotify solo si no hay nada guardado o falta la Target (puede ser recien creada).
        Args:
            conn (psycopg.Connection): conexion del request
            user (dict): User de la sesion
            canal (dict): fila de canales
        Returns:
            copies (list): filas de playlist_copies
    '''
    copies = copias.list_copies(conn, user['id'])

    if any(copy['playlist_id'] == canal['target_playlist_id'] for copy in copies):
        return copies

    try:
        sync_library(conn, user)
    except copias.READ_ERRORS:
        # con filas guardadas la vista sigue andando; sin ninguna no hay que pintar y salta el Aviso
        if not copies:
            raise
        return copies

    return copias.list_copies(conn, user['id'])


def is_usable(copy, user, canal):
    '''
    Dice si una playlist se puede abrir o usar de Seed en este Canal.
        Args:
            copy (dict): fila de playlist_copies
            user (dict): User de la sesion
            canal (dict): fila de canales
        Returns:
            usable (bool): False si es ajena no colaborativa, o la Target de este Canal (ADR 0008)
    '''
    # Spotify da 403 al leer items de playlists ajenas no colaborativas (feb 2026)
    readable = copy['owner_id'] == user['spotify_user_id'] or copy['collaborative']
    usable = bool(readable) and copy['playlist_id'] != canal['target_playlist_id']
    return usable


def visible_artists(conn, user, copy, whitelist):
    '''
    Arma los Artistas de la playlist: lo guardado menos lo que ya esta en la Columna derecha.
        Args:
            conn (psycopg.Connection): conexion del request
            user (dict): User de la sesion
            copy (dict): fila de playlist_copies
            whitelist (list): Whitelist del Canal
        Returns:
            artists (list): filas en orden alfabetico, o None si la playlist nunca se leyo
    '''
    # una primera lectura cortada por el tope ya muestra lo leido hasta ahi (#24)
    if copy['artists_snapshot_id'] is None and copy['partial_total'] is None:
        return None

    chosen = {artist['artist_id'] for artist in whitelist}
    artists = copias.sorted_artists(copias.get_artists(conn, user['id'], copy['playlist_id']))
    return copias.add_deezer_ids(conn, [artist for artist in artists if artist['artist_id'] not in chosen])


def canal_context(request, conn, user, canal, copies, **extra):
    '''
    Arma lo que comparten la Vista Canal y la Vista Ajustes.
        Args:
            request (Request): request entrante
            conn (psycopg.Connection): conexion del request
            user (dict): User de la sesion
            canal (dict): fila de canales
            copies (list): biblioteca guardada
            **extra: claves propias de la vista
        Returns:
            context (dict): contexto para el template
    '''
    user_canales = canales.list_canales(conn, user['id'])
    canal = next((item for item in user_canales if item['id'] == canal['id']), canal)
    context = shell_context(
        request, conn, user,
        user_canales=user_canales,
        view=canal['id'],
        canal=canal,
        whitelist_count=canal['whitelist_count'],
        target_found=any(copy['playlist_id'] == canal['target_playlist_id'] for copy in copies),
        retencion_options=RETENCION_OPTIONS,
        **extra,
    )
    return context


def render_settings(request, canal_id, user, conn, partial=False):
    '''
    Vista Ajustes: Seed Playlists, Caja Playlist, Retencion, Guardado y Resumen de Canal, en lugar de las dos columnas.
        Args:
            request (Request): request entrante
            canal_id (str): id del Canal, tal como viene en el path
            user (dict): User de la sesion
            conn (psycopg.Connection): conexion del request
            partial (bool): True devuelve solo el Panel y la Barra lateral
        Returns:
            response (TemplateResponse): html de la vista
    '''
    canal = require_canal(conn, user, canal_id)
    # pinta con lo guardado y una playlist nueva la trae refresh_settings por detras; tras un Interruptor alcanzan las copias
    copies = copias.list_copies(conn, user['id']) if partial else load_copies(conn, user, canal)
    context = canal_context(request, conn, user, canal, copies, section='ajustes', rows=seed_rows(conn, user, canal, copies))
    response = templates.TemplateResponse(request, '_canal_response.html', context) if partial else render_view(request, 'canal.html', context)
    return response


def seed_rows(conn, user, canal, copies):
    '''
    Arma las filas de la Columna de Seed Playlists: las playlists usables, con su Seed si esta prendida.
        Args:
            conn (psycopg.Connection): conexion del request
            user (dict): User de la sesion
            canal (dict): fila de canales
            copies (list): biblioteca guardada
        Returns:
            rows (list): copias usables con la clave source
    '''
    seeds = {source['playlist_id']: source for source in canales.get_sources(conn, canal['id']) if source['type'] == 'seed_playlist'}
    rows = [dict(copy, source=seeds.get(copy['playlist_id'])) for copy in copies if is_usable(copy, user, canal)]
    return rows


def seed_list_view(copies, user, canal):
    '''
    Resume lo que la Columna de Seed Playlists muestra de cada playlist, para saber si hay que repintarla.
        Args:
            copies (list): biblioteca guardada
            user (dict): User de la sesion
            canal (dict): fila de canales
        Returns:
            view (list): id, nombre, canciones y portada de cada playlist usable, en orden
    '''
    view = [(copy['playlist_id'], copy['name'], copy['total'], copy['image_url']) for copy in copies if is_usable(copy, user, canal)]
    return view


@app.get('/canales/{canal_id}/ajustes/refrescar', response_class=HTMLResponse)
def refresh_settings(request: Request, canal_id, user=Depends(require_user), conn=Depends(get_conn)):
    '''
    Revisa la biblioteca por detras de la Vista Ajustes y devuelve la Columna de Seed Playlists solo si cambio.
        Args:
            request (Request): request entrante
            canal_id (str): id del Canal, tal como viene en el path
            user (dict): User de la sesion
            conn (psycopg.Connection): conexion del request
        Returns:
            response (Response): columna para el morph, o 204 sin cuerpo si no cambio nada o Spotify fallo
    '''
    canal = require_canal(conn, user, canal_id)
    before = seed_list_view(copias.list_copies(conn, user['id']), user, canal)

    try:
        sync_library(conn, user)
    except (*copias.READ_ERRORS, spotify.TokenRejected, TimeoutError):
        # la vista ya esta pintada con lo guardado: si Spotify falla no se muestra nada
        return Response(status_code=204)

    copies = copias.list_copies(conn, user['id'])
    if seed_list_view(copies, user, canal) == before:
        return Response(status_code=204)

    context = {'canal': canal, 'rows': seed_rows(conn, user, canal, copies)}
    return templates.TemplateResponse(request, '_canal_seeds.html', context)


def finish_settings_change(request, conn, user, canal_id):
    '''
    Vuelve a la Vista Ajustes despues de prender o apagar una Seed Playlist o sus feats.
        Args:
            request (Request): request entrante
            conn (psycopg.Connection): conexion del request
            user (dict): User de la sesion
            canal_id (int): Canal modificado
        Returns:
            response (Response): Panel para HTMX o redirect para formularios sin JS
    '''
    if request.headers.get('HX-Request') != 'true':
        return RedirectResponse(f'/canales/{canal_id}/ajustes', status_code=303)
    return render_settings(request, canal_id, user, conn, partial=True)


def finish_rules_change(request, conn, user, canal_id):
    '''
    Devuelve solo las cajas de Retencion, Guardado y Resumen cuando el formulario usa HTMX.
        Args:
            request (Request): request entrante
            conn (psycopg.Connection): conexion del request
            user (dict): User de la sesion
            canal_id (int): Canal modificado
        Returns:
            response (Response): fragmento o redirect para formularios sin JS
    '''
    if request.headers.get('HX-Request') != 'true':
        return RedirectResponse(f'/canales/{canal_id}/ajustes', status_code=303)
    canal = require_canal(conn, user, canal_id)
    user_canales = canales.list_canales(conn, user['id'])
    count = next((item['whitelist_count'] for item in user_canales if item['id'] == canal['id']), 0)
    context = {'canal': canal, 'whitelist_count': count, 'retencion_options': RETENCION_OPTIONS, 'flash': request.session.pop('flash', None)}
    return templates.TemplateResponse(request, '_canal_rules_response.html', context)


def find_usable_copy(conn, user, canal, playlist_id):
    '''
    Busca una playlist que el User puede abrir o usar de Seed en este Canal.
        Args:
            conn (psycopg.Connection): conexion del request
            user (dict): User de la sesion
            canal (dict): fila de canales
            playlist_id (str): id de Spotify de la playlist
        Returns:
            copy (dict): fila de playlist_copies, o None si no se puede usar
    '''
    copy = copias.get_copy(conn, user['id'], playlist_id)
    return copy if copy and is_usable(copy, user, canal) else None


def refresh_copy(conn, user, playlist_id):
    '''
    Confirma contra Spotify la Copia de una playlist, sin reintentar si falla (ADR 0008).
        Args:
            conn (psycopg.Connection): conexion del request
            user (dict): User de la sesion
            playlist_id (str): id de Spotify de la playlist
        Returns:
            result (dict): resultado de copias.refresh; status failed si Spotify fallo o la playlist ya no esta
    '''
    try:
        playlist = next((item for item in sync_library(conn, user) if item['id'] == playlist_id), None)
        if playlist is None:
            return copias.failed_refresh(None)
        return copias.refresh(conn, get_token(conn, user), user['id'], playlist)
    except (*copias.READ_ERRORS, spotify.TokenRejected) as error:
        return copias.failed_refresh(error)


@app.get('/canales/{canal_id}/playlists/{playlist_id}', response_class=HTMLResponse)
def open_playlist(request: Request, canal_id, playlist_id, user=Depends(require_user), conn=Depends(get_conn)):
    '''Abre una Fila de playlist con su copia guardada al instante; el fragmento dispara el refresco por detras.'''
    canal = require_canal(conn, user, canal_id)
    if canales.uses_seed(conn, canal['id']):
        return action_failed(request, SEED_MESSAGE)
    copy = find_usable_copy(conn, user, canal, playlist_id)
    if copy is None:
        return action_failed(request, 'No encontramos esa playlist entre las que podes usar.')
    artists = visible_artists(conn, user, copy, canales.get_whitelist(conn, canal['id']))
    context = {'canal': canal, 'open_id': playlist_id, 'open_artists': artists, 'oob': True}
    return templates.TemplateResponse(request, '_playlist_open.html', context)


@app.get('/canales/{canal_id}/playlists/{playlist_id}/refrescar', response_class=HTMLResponse)
def refresh_playlist(request: Request, canal_id, playlist_id, user=Depends(require_user), conn=Depends(get_conn)):
    '''Confirma la copia y responde solo con el Indicador de lectura, el Bloque de nuevos y la baja de filas: la lista no se reordena.'''
    canal = require_canal(conn, user, canal_id)
    copy = find_usable_copy(conn, user, canal, playlist_id)
    if copy is None or canales.uses_seed(conn, canal['id']):
        return HTMLResponse('')

    never_read = copy['artists_snapshot_id'] is None and copy['partial_total'] is None
    result = refresh_copy(conn, user, playlist_id)
    context = {'canal': canal, 'open_id': playlist_id, 'status': result['status'], 'never_read': never_read, 'first_read': result['first_read'], 'remaining': [], 'nuevos': [], 'gone': []}

    # al dia o sin leer nada solo cambia el Indicador de lectura: no hace falta la Whitelist ni los artistas
    if result['status'] in ('same', 'failed'):
        return templates.TemplateResponse(request, '_playlist_refresh.html', context)

    conn.commit()
    chosen = {artist['artist_id'] for artist in canales.get_whitelist(conn, canal['id'])}
    gone = [artist_id for artist_id in result['removed'] if artist_id not in chosen]
    context['gone'] = gone

    # la lista entera solo se pinta en la primera lectura, o para saber si quedo vacia tras una baja
    if result['first_read'] or gone:
        context['remaining'] = [artist for artist in copias.sorted_artists(copias.get_artists(conn, user['id'], playlist_id)) if artist['artist_id'] not in chosen]
    if not result['first_read']:
        context['nuevos'] = [artist for artist in result['added'] if artist['artist_id'] not in chosen]

    shown = context['remaining'] if result['first_read'] else context['nuevos']
    if shown:
        copias.add_deezer_ids(conn, shown)

    return templates.TemplateResponse(request, '_playlist_refresh.html', context)


SPOTIFY_IMAGES = 'https://i.scdn.co/'

SEED_MESSAGE = 'Este Canal sigue sus Seed Playlists. Para elegir artistas a mano, apagalas en Ajustes.'


def artist_moved(request, conn, user, canal, form, artist_id, playlist_id=None, from_search=False, listed=None):
    '''
    Responde a una Flecha de artista o al Buscador sin llamar a Spotify: solo la fila movida y los contadores, nunca la Whitelist entera.
        Args:
            request (Request): request entrante
            conn (psycopg.Connection): conexion del request
            user (dict): User de la sesion
            canal (dict): fila de canales
            form (dict): formulario enviado, con open opcional
            artist_id (str): id del artista movido
            playlist_id (str): fila de donde salio; si no, la playlist abierta
            from_search (bool): True si vino del Buscador de artistas
            listed (dict): el artista en la copia de playlist_id, si el que llama ya lo leyo
        Returns:
            response (Response): swaps fuera de banda, o redirect para formularios sin JS
    '''
    playlist_id = playlist_id or form.get('open')

    if request.headers.get('HX-Request') != 'true':
        return RedirectResponse(canal_url(canal['id'], playlist_id), status_code=303)

    # sin chequear si la playlist se puede abrir: la copia es del User y una que no se abre no tiene filas que tocar
    added = canales.get_whitelist_artist(conn, canal['id'], artist_id)
    if playlist_id and listed is None:
        listed = copias.get_playlist_artist(conn, user['id'], playlist_id, artist_id)

    # la foto que ya mostraba el Buscador; solo de Spotify, el form lo arma el navegador
    search_image = form.get('image', '') if from_search else ''
    search_image = search_image if search_image.startswith(SPOTIFY_IMAGES) else None
    if added and search_image:
        added = dict(added, image=search_image)

    context = {
        'canal': canal,
        'artist_id': artist_id,
        'added': added,
        'whitelist_size': canales.count_whitelist(conn, canal['id']),
        'open_id': playlist_id if listed else None,
        'back': listed if listed and added is None else None,
        'left_empty': bool(listed and added) and copias.count_unchosen(conn, user['id'], playlist_id, canal['id']) == 0,
        'from_search': from_search,
        'search_image': search_image,
    }
    return templates.TemplateResponse(request, '_artista_movido.html', context)


templates.env.globals['canal_url'] = canal_url


@app.post('/canales/{canal_id}/artistas/sumar')
def add_artist(request: Request, canal_id, user=Depends(require_user), form=Depends(read_form), conn=Depends(get_conn)):
    '''
    Flecha de artista en la Columna izquierda: pasa un artista de la copia a la Lista manual, con su nombre e ISRC.
        Args:
            canal_id (str): id del Canal, tal como viene en el path
            user (dict): User de la sesion
            form (dict): playlist_id y artist_id
            conn (psycopg.Connection): conexion del request
        Returns:
            response (Response): swaps fuera de banda
    '''
    canal = require_canal(conn, user, canal_id)
    if canales.uses_seed(conn, canal['id']):
        return action_failed(request, SEED_MESSAGE)

    artist = copias.get_playlist_artist(conn, user['id'], form['playlist_id'], form['artist_id'])
    if artist is None:
        return action_failed(request, 'Ese artista ya no esta en esa playlist.')

    canales.add_manual_artist(conn, canal['id'], form['artist_id'], artist['name'], artist['isrc'])
    conn.commit()
    return artist_moved(request, conn, user, canal, form, form['artist_id'], playlist_id=form['playlist_id'], listed=artist)


@app.post('/canales/{canal_id}/artistas/quitar')
def remove_artist(request: Request, canal_id, user=Depends(require_user), form=Depends(read_form), conn=Depends(get_conn)):
    '''
    Flecha de artista en la Columna derecha: saca un artista de la Lista manual y lo devuelve a la playlist abierta si esta en ella.
        Args:
            canal_id (str): id del Canal, tal como viene en el path
            user (dict): User de la sesion
            form (dict): artist_id y open
            conn (psycopg.Connection): conexion del request
        Returns:
            response (Response): swaps fuera de banda
    '''
    canal = require_canal(conn, user, canal_id)
    if canales.uses_seed(conn, canal['id']):
        return action_failed(request, SEED_MESSAGE)

    canales.remove_manual_artist(conn, canal['id'], form['artist_id'])
    conn.commit()
    return artist_moved(request, conn, user, canal, form, form['artist_id'])


@app.post('/canales/{canal_id}/seeds')
def toggle_seed(request: Request, canal_id, user=Depends(require_user), form=Depends(read_form), conn=Depends(get_conn)):
    '''
    Prende o apaga una playlist como Seed Playlist del Canal, leyendo por su Copia de playlist.
        Args:
            canal_id (str): id del Canal, tal como viene en el path
            user (dict): User de la sesion
            form (dict): playlist_id y on (1 o 0)
            conn (psycopg.Connection): conexion del request
        Returns:
            response (Response): vuelta a la Vista Ajustes
    '''
    canal = require_canal(conn, user, canal_id)
    playlist_id = form['playlist_id']

    if form.get('on') == '1':
        # una playlist guardada que no se puede usar se rechaza sin ir a Spotify
        copy = copias.get_copy(conn, user['id'], playlist_id)
        if copy is not None and not is_usable(copy, user, canal):
            return action_failed(request, 'No encontramos esa playlist entre las que podes usar.')

        # unica revision de la biblioteca del request: trae la playlist si todavia no estaba guardada y confirma sus artistas
        result = refresh_copy(conn, user, playlist_id)
        copy = find_usable_copy(conn, user, canal, playlist_id)
        if copy is None and result.get('error') is not None:
            return action_failed(request, 'No pudimos leer esta playlist. Proba mas tarde.')
        if copy is None:
            return action_failed(request, 'No encontramos esa playlist entre las que podes usar.')

        # si Spotify falla pero ya hay copia, alcanza: el Run la pone al dia porque la Seed queda sin snapshot
        if result['status'] == 'failed' and copy['artists_snapshot_id'] is None:
            return action_failed(request, 'No pudimos leer esta playlist. Proba mas tarde.')

        canales.add_source(conn, canal['id'], 'seed_playlist', copias.get_artists(conn, user['id'], playlist_id), playlist_id)
    else:
        canales.remove_source(conn, canal['id'], 'seed_playlist', playlist_id)

    conn.commit()
    return finish_settings_change(request, conn, user, canal['id'])


@app.post('/canales/{canal_id}/feats')
def toggle_feats(request: Request, canal_id, user=Depends(require_user), form=Depends(read_form), conn=Depends(get_conn)):
    '''
    Prende o apaga los feats de una Seed Playlist.
        Args:
            canal_id (str): id del Canal, tal como viene en el path
            user (dict): User de la sesion
            form (dict): playlist_id y on (1 o 0)
            conn (psycopg.Connection): conexion del request
        Returns:
            response (Response): vuelta a la Vista Ajustes
    '''
    canal = require_canal(conn, user, canal_id)
    canales.set_feats(conn, canal['id'], form['playlist_id'], form.get('on') == '1')
    conn.commit()
    return finish_settings_change(request, conn, user, canal['id'])


@app.get('/canales/{canal_id}/buscar', response_class=HTMLResponse)
def search_artists(request: Request, canal_id, user=Depends(require_user), conn=Depends(get_conn)):
    '''
    Resultados del Buscador de artistas, como fragmento para HTMX.
        Args:
            request (Request): request entrante, con q
            canal_id (str): id del Canal, tal como viene en el path
            user (dict): User de la sesion
            conn (psycopg.Connection): conexion del request
        Returns:
            response (TemplateResponse): fragmento con los resultados
    '''
    canal = require_canal(conn, user, canal_id)
    query = ' '.join(request.query_params.get('q', '').split())
    results = []
    notice = None
    retry = None

    # con una letra Spotify devuelve casi cualquier cosa: no gastamos cuota ni base
    if len(query) >= SEARCH_MIN_CHARS and not canales.uses_seed(conn, canal['id']):
        text = query.casefold()
        try:
            # el User va primero: el cache se limpia por User y el resultado depende del mercado de su token
            found = search_cache.get((user['id'], 'search', text), lambda: spotify.search_artists(get_token(conn, user), text), ttl=SEARCH_TTL)
        except (spotify.RateLimited, spotify.QuotaExceeded) as error:
            notice, retry = limit_notice(error)
        else:
            # los que ya entran se muestran apagados para que el User vea que estan, sin poder sumarlos de nuevo
            chosen = canales.get_whitelist_ids(conn, canal['id'])
            results = [dict(artist, added=artist['id'] in chosen) for artist in found]

    context = {'canal': canal, 'query': query if len(query) >= SEARCH_MIN_CHARS else '', 'results': results, 'notice': notice}
    response = templates.TemplateResponse(request, '_busqueda.html', context)
    if retry is not None:
        response.headers['Retry-After'] = str(retry)
    return response


# tope de artistas por tanda de static/fotos.js; Deezer corta a ~50 llamadas cada 5 s y cada foto gasta 1 o 2
PHOTO_BATCH = 20


# FastAPI necesita la anotacion Request para inyectarlo, es la unica excepcion a no usar type hints
async def read_json(request: Request):
    '''
    Lee un cuerpo JSON; uno roto o vacio queda como dict vacio.
        Args:
            request (Request): request entrante
        Returns:
            body (dict): cuerpo del request
    '''
    try:
        body = await request.json()
    except ValueError:
        return {}

    body = body if isinstance(body, dict) else {}
    return body


def read_photo_hints(conn, user_id, artist_ids):
    '''
    Lee lo que hay guardado de cada artista pedido: foto, vinculo con Deezer, y nombre e ISRC de las playlists y Canales del User.
        Args:
            conn (psycopg.Connection): conexion abierta
            user_id (int): id interno del User
            artist_ids (list): ids de Spotify
        Returns:
            hints (dict): artist_id -> dict con deezer_artist_id, photo_url, photo_known, name e isrc
    '''
    rows = conn.cursor(row_factory=dict_row).execute(
        f'''
        SELECT a.id AS artist_id, d.deezer_artist_id, {copias.PHOTO_COLUMNS}, n.name, n.isrc
        FROM unnest(%s::text[]) a(id)
        LEFT JOIN deezer_artists d ON d.spotify_artist_id = a.id
        LEFT JOIN artist_photos p ON p.spotify_artist_id = a.id
        LEFT JOIN LATERAL (
            SELECT name, isrc FROM playlist_copy_artists WHERE user_id = %s AND artist_id = a.id
            UNION ALL
            SELECT sa.name, sa.isrc FROM source_artists sa
            JOIN artist_sources s ON s.id = sa.artist_source_id JOIN canales c ON c.id = s.canal_id
            WHERE c.user_id = %s AND sa.artist_id = a.id
            ORDER BY isrc NULLS LAST LIMIT 1
        ) n ON true
        ''',
        (artist_ids, user_id, user_id),
    ).fetchall()
    hints = {row['artist_id']: row for row in rows}
    return hints


def save_photos(conn, pictures):
    '''
    Guarda las fotos encontradas por artista; None es que Deezer no tiene y se vuelve a buscar en un dia.
        Args:
            conn (psycopg.Connection): conexion abierta
            pictures (dict): artist_id -> url o None
    '''
    with conn.cursor() as cursor:
        cursor.executemany(
            '''
            INSERT INTO artist_photos (spotify_artist_id, url) VALUES (%s, %s)
            ON CONFLICT (spotify_artist_id) DO UPDATE SET url = EXCLUDED.url, checked_at = now()
            ''',
            list(pictures.items()),
        )
    conn.commit()


def get_conn_opener():
    '''
    Para requests que esperan afuera de la base: en vez de tener una conexion todo el request, devuelve como tomar una corta del pool.
        Returns:
            opener (callable): sin argumentos, devuelve un context manager con la conexion
    '''
    return lambda: get_web_pool().connection()


@app.post('/artistas/fotos')
def find_artist_photos(request: Request, body=Depends(read_json), opener=Depends(get_conn_opener)):
    '''
    Fotos de una tanda de avatares que static/fotos.js pide con la pagina quieta: lo guardado sale de la base y el resto se busca en Deezer.
    La conexion se suelta mientras se espera a Deezer, asi una tanda no ocupa el pool.
        Args:
            request (Request): request entrante
            body (dict): artists, lista de {id, name}; el nombre solo se usa si el User no tiene guardado al artista (Buscador)
            opener (callable): toma una conexion corta del pool
        Returns:
            response (JSONResponse): photos, artist_id -> url o None; los que faltan se reintentan mas tarde
    '''
    user_id = request.session.get('user_id')
    if not user_id:
        return PlainTextResponse('', status_code=401)

    asked = body.get('artists') if isinstance(body.get('artists'), list) else []
    names = {}
    for artist in asked:
        if isinstance(artist, dict) and isinstance(artist.get('id'), str) and artist['id'] and len(names) < PHOTO_BATCH:
            names.setdefault(artist['id'], str(artist.get('name') or '').strip())

    if not names:
        return JSONResponse({'photos': {}})

    with opener() as conn:
        hints = read_photo_hints(conn, user_id, list(names))

    photos = {}
    search, guessed = [], set()
    for artist_id, hint in hints.items():
        if hint['photo_url'] or hint['photo_known']:
            photos[artist_id] = hint['photo_url']
        elif hint['name'] or hint['deezer_artist_id']:
            search.append(dict(hint, name=hint['name'] or names[artist_id]))
        elif names[artist_id]:
            # solo el Buscador manda artistas que el User no tiene guardados
            search.append(dict(hint, name=names[artist_id]))
            guessed.add(artist_id)
        else:
            photos[artist_id] = None

    found = deezer.find_pictures(search) if search else {}
    photos.update(found)

    # un nombre que mando el navegador no se guarda: una foto equivocada quedaria para todos los Users
    keep = {artist_id: url for artist_id, url in found.items() if artist_id not in guessed}
    if keep:
        with opener() as conn:
            save_photos(conn, keep)

    response = JSONResponse({'photos': photos})
    return response


@app.post('/canales/{canal_id}/manual')
def add_manual(request: Request, canal_id, user=Depends(require_user), form=Depends(read_form), conn=Depends(get_conn)):
    '''
    Buscador de artistas: suma un artista a la Lista manual por su nombre, sin ISRC (ADR 0007).
        Args:
            canal_id (str): id del Canal, tal como viene en el path
            user (dict): User de la sesion
            form (dict): artist_id, name y open
            conn (psycopg.Connection): conexion del request
        Returns:
            response (Response): swaps fuera de banda
    '''
    canal = require_canal(conn, user, canal_id)
    if canales.uses_seed(conn, canal['id']):
        return action_failed(request, SEED_MESSAGE)

    canales.add_manual_artist(conn, canal['id'], form['artist_id'], form.get('name', ''))
    conn.commit()
    return artist_moved(request, conn, user, canal, form, form['artist_id'], from_search=True)


@app.post('/canales/{canal_id}/retencion')
def set_retencion(request: Request, canal_id, user=Depends(require_user), form=Depends(read_form), conn=Depends(get_conn)):
    '''
    Cambia la Retencion del Canal: N Lotes o Acumulativa.
        Args:
            request (Request): request entrante
            canal_id (str): id del Canal, tal como viene en el path
            user (dict): User de la sesion
            form (dict): retencion (numero o acum)
            conn (psycopg.Connection): conexion del request
        Returns:
            response (Response): cajas de la Vista Ajustes, o redirect a ella sin JS
    '''
    canal = require_canal(conn, user, canal_id)
    value = form.get('retencion')
    retencion_n = int(value) if value in {str(n) for n in RETENCION_OPTIONS} else None

    try:
        canales.set_retencion(conn, canal, retencion_n)
    except canales.CanalError as error:
        request.session['flash'] = str(error)

    conn.commit()
    return finish_rules_change(request, conn, user, canal['id'])


@app.post('/canales/{canal_id}/guardado')
def set_guardado(request: Request, canal_id, user=Depends(require_user), form=Depends(read_form), conn=Depends(get_conn)):
    '''
    Prende o apaga el filtro de Guardado del Canal.
        Args:
            canal_id (str): id del Canal, tal como viene en el path
            user (dict): User de la sesion
            form (dict): on (1 o 0)
            conn (psycopg.Connection): conexion del request
        Returns:
            response (Response): cajas de la Vista Ajustes, o redirect a ella sin JS
    '''
    canal = require_canal(conn, user, canal_id)
    canales.set_guardado(conn, canal['id'], form.get('on') == '1')
    conn.commit()
    return finish_rules_change(request, conn, user, canal['id'])


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
