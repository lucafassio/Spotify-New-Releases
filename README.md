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

`.env` en la raiz necesita `DATABASE_URL` (connection string de la branch `dev`, sin pooler) ademas de las credenciales de Spotify.

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
