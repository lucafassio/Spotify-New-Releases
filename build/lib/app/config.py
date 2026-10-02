import os

from dotenv import load_dotenv

# en local las variables salen de .env; en Render y Actions ya vienen en el entorno
load_dotenv()


def get_env(name):
    '''
    Devuelve una variable de entorno obligatoria y falla si no esta.
        Args:
            name (str): nombre de la variable
        Returns:
            value (str): valor configurado
    '''
    value = os.environ.get(name)

    if not value:
        raise RuntimeError(f'falta {name} en el entorno o en .env')

    return value


def get_database_url():
    '''
    Devuelve la URL de Postgres configurada en el entorno.
        Returns:
            database_url (str): url de conexion a Neon
    '''
    database_url = get_env('DATABASE_URL')
    return database_url


def get_shared_app():
    '''
    Devuelve las credenciales de la Shared App y el redirect URI del servidor.
        Returns:
            shared_app (dict): client_id, client_secret y redirect_uri
    '''
    shared_app = {
        'client_id': get_env('SPOTIFY_CLIENT_ID'),
        'client_secret': get_env('SPOTIFY_CLIENT_SECRET'),
        'redirect_uri': get_env('SPOTIFY_REDIRECT_URI'),
    }
    return shared_app
