---
status: accepted
---

# Modelo de datos: tablas para User, Canal, Whitelist, Run, Lote y Guardado

> **Enmienda 2026-10-01 (ticket #19):** `User` suma `disconnected_at NULL` y
> `refresh_token_enc` pasa a nullable. Un `invalid_grant` (o `invalid_client` de una Own App)
> al refrescar marca al User Desconectado: se setea `disconnected_at` y se borra el token
> muerto. El callback de login limpia ambos.

> **Enmienda 2026-10-02 (ticket #14):** `ManualSourceArtist` pasa a `SourceArtist(artist_source_id,
> artist_id, name, feat)` y guarda los artistas de todo Artist Source, no solo la lista manual:
> lo que aporta cada Seed Playlist (principal o feat) y los follows. Asi la Whitelist se
> recalcula en SQL sin volver a leer Spotify en cada cambio, y la UI muestra nombres sin
> `GET /artists` (sin batch desde feb 2026). `ArtistSource` suma `include_feats` (toggle por
> Seed Playlist) y un indice unico por (Canal, type, playlist). `Canal` suma `target_name`,
> cache del nombre de la Target para la Barra lateral, refrescado al leer `GET /me/playlists`.
> Releer las fuentes de Spotify es `canales.refresh_sources`, pensado para el Run diario.

> **Enmienda 2026-10-05 (ticket #15, ADR 0007):** se va `Checkpoint`: la deteccion pasa a
> Deezer, que se lee completo cada dia. Entran dos caches globales (no por User):
> `DeezerArtist(spotify_artist_id, deezer_artist_id NULL, matched_by[isrc|name], checked_at)`
> y `AlbumLink(deezer_album_id, spotify_album_id NULL, checked_at)`, con NULL = no encontrado
> todavia. `SourceArtist` suma `isrc NULL` (un Track de la fuente donde figura el artista,
> para vincularlo por ISRC) y `ArtistSource` suma `snapshot_id NULL` (releer una Seed
> Playlist solo si cambio). `LoteItem.album_id` sigue siendo el id de Spotify.

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

- `User(id, spotify_user_id, connection_mode, client_id NULL, client_secret_enc NULL, refresh_token_enc NULL, disconnected_at NULL, created_at)`
- `Canal(id, user_id, target_playlist_id, target_name, target_created_by_bot, retencion_n NULL, guardado_filter, created_at)`
- `ArtistSource(id, canal_id, type[seed_playlist|manual|followed], playlist_id NULL, include_feats)` — varias
  filas del mismo type por Canal, ej. multiples Seed Playlists
- `SourceArtist(artist_source_id, artist_id, name, feat)` (era `ManualSourceArtist`, enmienda #14)
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
