# spotify-new-releases

Bot que cada viernes agrega a una playlist los lanzamientos nuevos de un set
de artistas. El set sale de los artistas unicos de una playlist de referencia.

## Desarrollo

Paquete unico `app` en `src/` (ADR 0002). DB de desarrollo: branch `dev` del proyecto `spotify-new-releases` en Neon.

```cmd
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
```

`.env` en la raiz necesita:

- `DATABASE_URL`: connection string de la branch `dev`, sin pooler.
- `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`: Shared App (#9).
- `FERNET_KEY`: cifra refresh tokens y secrets de Own App. Generarla una vez con `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`; si se pierde, todos los Users tienen que volver a entrar.
- `SESSION_SECRET`: firma la cookie de sesion. Cualquier string largo al azar, ej: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

En local la web se abre en `http://127.0.0.1:8000`, no en `localhost`: el redirect de Spotify vuelve a `127.0.0.1` y con otro host la cookie de sesion no viaja.

```cmd
python -m app.migrate
uvicorn app.web:app --reload
pytest
ruff check .
```

- Migraciones: archivos `src/app/migrations/NNNN_nombre.sql`, se aplican en orden y quedan registradas en `schema_migrations`. Nunca editar una ya aplicada; agregar una nueva.
- Tests de DB: corren sobre un schema efimero `test_*` en la DB de `DATABASE_URL`, que se borra al terminar. Sin `DATABASE_URL` se saltean.
- Spotify en tests: fixture `spotify` (respx), todo request HTTP sin ruta mockeada falla. Nunca llamadas reales.
- `ruff format` no se usa: pisa el estilo de docstrings con `'''` y corta lineas.
