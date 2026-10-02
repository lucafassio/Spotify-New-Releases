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

## Produccion

Web en Render Free (`render.yaml`), DB en la branch `main` de Neon (la `dev` es solo local). Cada push a `main` despliega solo.

Env en Render (y en GitHub Secrets cuando exista el Tick): las mismas variables que `.env`, con estos cambios:

- `DATABASE_URL`: connection string de la branch `main`, sin pooler.
- `SPOTIFY_REDIRECT_URI`: `https://<servicio>.onrender.com/callback`, registrado tambien en el dashboard de la Shared App.
- `FERNET_KEY` y `SESSION_SECRET`: propias de produccion, distintas de las locales. Perder `FERNET_KEY` obliga a todos los Users a volver a entrar.

### Subir una version sin romper un Run

- Render arranca la version nueva al lado de la vieja y corta el trafico recien cuando `/health` responde; si `python -m app.migrate` falla, la vieja sigue sirviendo.
- Las migraciones corren al arrancar la web (pre-deploy es pago) y toman un advisory lock, asi que la web y el Tick pueden llamar a `app.migrate` a la vez sin pisarse.
- Toda migracion tiene que ser compatible con el codigo del commit anterior, porque un Run en curso sigue corriendo codigo viejo contra el schema nuevo: agregar columnas nullable o con default y tablas nuevas; renombrar o borrar es en dos commits (primero dejar de usar, despues borrar).
- Un Run cortado a la mitad por un deploy o por Actions se retoma en el proximo Tick desde su checkpoint por artista; la Entrega se reintenta (ADR 0004).
