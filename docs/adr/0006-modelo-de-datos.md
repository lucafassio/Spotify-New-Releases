---
status: accepted
---

# Modelo de datos: tablas para User, Canal, Whitelist, Run, Lote y Guardado

Las decisiones de #5 y #6 (ADR 0002-0005) ya fijaron la semantica; esto cierra el shape de
tablas para implementarla. Tokens y credenciales de Own App se guardan cifrados (Fernet) en
User, nunca las de Shared App (viven en env/GitHub Secrets, ADR 0002). WhitelistArtist
persiste `added_at` por (Canal, artista) para la ventana de 7 dias (#5): si un artista sale de
todos los Artist Sources de un Canal y vuelve, `added_at` se resetea en vez de conservar
historial, mismo trato que un alta nueva. Checkpoint por (User, artista, grupo) queda huerfano
si el artista sale de todos los Canales del User: barrerlo exige un join extra en cada Run
para ahorrar filas que no cuestan nada, y si el artista vuelve el checkpoint viejo sigue
sirviendo. LoteItem cachea la metadata de orden (artist_name, disc_number, track_number,
release_date) al explorar, asi la Entrega no repite requests. Entrega no tiene tabla propia:
un Lote nunca tiene mas de una Entrega en curso (el no entregado se suma al siguiente, ADR
0004), asi que `closed_at` / `delivered_at` / `attempt_count` en Lote alcanzan. Run es una fila
por (User, fecha), no por intento: el guard del Tick lee ese estado directo, un reintento el
mismo dia hace upsert sobre la misma fila.

## Tablas

- `User(id, spotify_user_id, connection_mode, client_id NULL, client_secret_enc NULL, refresh_token_enc, created_at)`
- `Canal(id, user_id, target_playlist_id, target_created_by_bot, retencion_n NULL, guardado_filter, created_at)`
- `ArtistSource(id, canal_id, type[seed_playlist|manual|followed], playlist_id NULL)` — varias
  filas del mismo type por Canal, ej. multiples Seed Playlists
- `ManualSourceArtist(artist_source_id, artist_id)`
- `WhitelistArtist(canal_id, artist_id, added_at)`
- `Checkpoint(user_id, artist_id, group, total, first_page_ids)`
- `Lote(id, canal_id, week_start, status[abierto|cerrado], closed_at NULL, delivered_at NULL, attempt_count)`
- `LoteItem(lote_id, track_uri, album_id, artist_name, disc_number, track_number, release_date, status[pendiente|insertado|retirado|descartado])`
- `SavedSource(user_id, source[liked|playlist_id], snapshot_id)`
- `SavedTrack(user_id, source, track_id)`
- `Run(user_id, date, status[en_curso|exitoso|fallido])`, UNIQUE(user_id, date)

## Consequences

- Desvincular un Canal es hard delete en cascada (Lotes, LoteItems, Checkpoints propios no
  aplica porque Checkpoint es por User no por Canal). Sin backups (ADR 0002), agregar retencion
  de historial solo para Canales borrados seria inconsistente con el resto del sistema.
- `target_created_by_bot` en Canal es el flag que decide orden de insercion (ADR 0003), tope de
  9500 y si Retencion es editable; sin el, esas reglas no se pueden aplicar.
