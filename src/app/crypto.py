from cryptography.fernet import Fernet

from app.config import get_env


def encrypt_text(text):
    '''
    Cifra un secreto con la clave Fernet del entorno antes de guardarlo en la DB (ADR 0002).
        Args:
            text (str): secreto en claro
        Returns:
            token (str): secreto cifrado
    '''
    token = Fernet(get_env('FERNET_KEY')).encrypt(text.encode()).decode()
    return token


def decrypt_text(token):
    '''
    Descifra un secreto guardado con encrypt_text.
        Args:
            token (str): secreto cifrado
        Returns:
            text (str): secreto en claro
    '''
    text = Fernet(get_env('FERNET_KEY')).decrypt(token.encode()).decode()
    return text
