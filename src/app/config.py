import os

from dotenv import load_dotenv

# en local las variables salen de .env; en Render y Actions ya vienen en el entorno
load_dotenv()


def get_database_url():
    '''
    Devuelve la URL de Postgres configurada en el entorno.
        Returns:
            database_url (str): url de conexion a Neon
    '''
    database_url = os.environ.get('DATABASE_URL')

    if not database_url:
        raise RuntimeError('falta DATABASE_URL en el entorno o en .env')

    return database_url
