import os
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import spotify, users
from app.config import get_env, get_shared_app
from app.db import connect

templates = Jinja2Templates(directory=Path(__file__).parent / 'templates')

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


def get_conn():
    '''
    Abre una conexion por request y la commitea al terminar si no hubo error.
        Returns:
            conn (psycopg.Connection): conexion abierta
    '''
    with connect() as conn:
        yield conn


# FastAPI necesita la anotacion Request para inyectarlo, es la unica excepcion a no usar type hints
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

    context = {
        'display_name': request.session.get('display_name') or user['spotify_user_id'],
        'disconnected': user['disconnected_at'] is not None,
        'reconnected_since': request.session.pop('reconnected_since', None),
    }
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
