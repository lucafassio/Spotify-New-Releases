from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=Path(__file__).parent / 'templates')

app = FastAPI()


# FastAPI necesita la anotacion Request para inyectarlo, es la unica excepcion a no usar type hints
@app.get('/')
def home(request: Request):
    '''
    Renderiza la pagina de inicio.
        Args:
            request (Request): request entrante
        Returns:
            response (TemplateResponse): html de la pagina
    '''
    response = templates.TemplateResponse(request, 'index.html')
    return response


@app.get('/health', response_class=PlainTextResponse)
def health():
    '''
    Responde ok sin tocar la DB, para el health check de Render.
        Returns:
            status (str): siempre ok
    '''
    return 'ok'
