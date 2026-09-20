---
status: accepted
---

# Hosting: GitHub Actions + Render Free + Neon, Tick semanal a hora fija

> **Enmienda 2026-09-20 (ticket #5):** el Tick pasa a ser **diario** a la misma hora (02:37
> ART, crons `37 5,6,7 * * *`). El guard "ultimo Run exitoso > 6 dias" se reemplaza por
> "Run solo si el User no tiene Run exitoso hoy (fecha ART)". Motivo: un Run diario con
> ventana de 7 dias y registro de Releases vistos agarra el mismo dia lo que sale fuera del
> jueves y hace que una caida de Actions cueste un dia, no una semana. Todo lo demas de este
> ADR sigue vigente.

Costo cero es restriccion dura y ninguna plataforma free da web + scheduler + disco
persistente sin tarjeta (ver research en `research/free-hosting`). Decidimos repartir en
tres proveedores gratuitos y sin tarjeta: la **web** (FastAPI, OAuth + UI) corre en Render
Free con auto-deploy desde `main`; el **scheduler** es un workflow `schedule` de GitHub
Actions que ejecuta `python -m app.tick` directo contra la DB; la **DB** es Postgres en Neon
Free con `psycopg`. Un solo paquete Python: la web importa la misma logica de Run para
"correr ahora". El repo es publico para que los minutos de Actions sean ilimitados.

El Tick corre a **hora fija global, viernes 02:37 Argentina (05:37 UTC)**, no en la hora
local de cada User. Eso mata timezones, config de horario por User y el riesgo de dos Ticks
pisandose; a cambio pisa la preferencia original de "dia + hora configurables". Spotify
publica a las 00:00 local de cada mercado, asi que 02:37 ART ya ve los Releases del viernes
argentino y evita las horas pico de Actions (inicio de cada hora, horario laboral USA/EU).

## Considered Options

- **Oracle Always Free VM, todo en uno con SQLite en disco**: sin sleep ni jitter, pero exige
  tarjeta, reclama VMs idle a los 7 dias y deja TLS/hardening a cargo nuestro.
- **Cloudflare Workers Python + D1**: un solo proveedor, pero Pyodide beta, 10 ms CPU free y
  reescribir la app al modelo Worker.
- **Turso** como DB: SQLite igual local y prod, pero driver 0.1.0 y proveedor desconocido.
- **Supabase** como DB: pausa el proyecto a los 7 dias idle con restore manual; conexion
  directa IPv6 obliga a usar pooler desde Actions. Neon se apaga y se prende sola.
- **Vercel Hobby** como web: cold start mas corto que Render, pero FastAPI via adaptador
  serverless y solo uso no comercial.
- **Tick hourly con hora local por User**: 24 Ticks/dia, logica de timezones y pisado de
  Ticks, para una ganancia que nadie pidio.

## Consequences

- Actions no garantiza la hora de arranque y puede dropear jobs. Guards: crons `37 5 * * *`
  (diario) mas `37 6,7 * * 5` (reintentos viernes), regla "Run solo si el ultimo Run
  exitoso tiene mas de 6 dias", y `concurrency: { group: tick, cancel-in-progress: false }`.
  Peor caso normal: playlist lista 04:37 ART viernes; con Actions caido, sabado 02:37.
- Actions desactiva `schedule` en repos publicos tras 60 dias sin commits. Un workflow
  mensual hace `git commit --allow-empty` para mantenerlo vivo.
- Render Free no tiene disco persistente y duerme a los 15 min: el primer OAuth callback tras
  un rato tarda ~50 s. Todo estado vive en Neon.
- Neon apaga el compute a los 5 min idle; cold start de 1-2 s en la primera query. Aceptable
  para batch y para una web con 5 Users. Branch `dev` de Neon para desarrollo local, sin
  Postgres ni docker en la maquina.
- Secrets (clave Fernet, URL de Neon, client id/secret de la Shared App) viven por
  duplicado en Render env y GitHub Secrets, mas `.env` local. Tokens y client secrets de Own
  App se cifran con Fernet antes de tocar la DB.
- Sin backups: si Neon desaparece, los Users re-conectan y re-configuran. El registro de
  Tracks agregados es reconstruible desde la Target Playlist (`added_at` por item).
- La cuenta de Neon es propia y nueva; si el signup pide tarjeta se resuelve en el ticket de
  setup (#9).
