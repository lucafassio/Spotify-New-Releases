# Limites de la Spotify Web API en 2026

Ticket: https://github.com/lucafassio/spotify-new-releases/issues/2
Fecha de consulta: 2026-09-19. Solo fuentes primarias (developer.spotify.com).
Todo lo que no pudo verificarse contra una fuente oficial esta marcado UNVERIFIED.

Contexto clave: entre nov 2024 y jul 2026 Spotify cambio tres veces las reglas
de development mode. El estado vigente para una app nueva es el que fija el
cambio de feb 2026 (mas los ajustes de jul 2026). Varias suposiciones del mapa
(#1) quedaron viejas; ver la seccion final.

Fuentes principales:

- Quota modes: https://developer.spotify.com/documentation/web-api/concepts/quota-modes
- Rate limits: https://developer.spotify.com/documentation/web-api/concepts/rate-limits
- Scopes: https://developer.spotify.com/documentation/web-api/concepts/scopes
- Blog nov 2024: https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api
- Blog abr 2025 (extended access): https://developer.spotify.com/blog/2025-04-15-updating-the-criteria-for-web-api-extended-access
- Blog feb 2026 (dev mode): https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security
- Changelog feb 2026: https://developer.spotify.com/documentation/web-api/references/changes/february-2026
- Guia de migracion feb 2026: https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide
- Blog jul 2026 (quota): https://developer.spotify.com/blog/2026-07-23-web-api-quota-updates
- Changelog jul 2026: https://developer.spotify.com/documentation/web-api/references/changes/july-2026

---

## 1. Tope de users y allowlist por email

Hechos (quota-modes + blog feb 2026 + guia de migracion):

- Toda app nueva arranca en development mode.
  Fuente: https://developer.spotify.com/documentation/web-api/concepts/quota-modes
- **Tope: 5 users autenticados por app**, no 25. Texto literal: "Up to 5
  authenticated Spotify users can use an app that is in development mode".
  Fuente: https://developer.spotify.com/documentation/web-api/concepts/quota-modes
- Vigencia: Client IDs creados desde el 11 feb 2026 nacen con el tope de 5;
  las apps existentes migraron el 9 mar 2026. Grandfathering: "If you already
  have multiple Client IDs or more than 5 users, you will retain them. These
  limits only restrict what you can create or add going forward."
  Fuente: https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide
- **El owner de la app necesita Spotify Premium activo** para que la app
  funcione en development mode: "The app owner must have a Spotify Premium
  account for apps in development mode to function."
  Fuente: https://developer.spotify.com/documentation/web-api/concepts/quota-modes
- Mecanismo de allowlist: Developer Dashboard > app > Settings > User
  Management > Add new user, cargando nombre y email de Spotify del user. El
  user debe estar en la lista antes de autorizar; si no, el login falla.
  Fuente: https://developer.spotify.com/documentation/web-api/concepts/quota-modes
- Client IDs por cuenta de developer: 1 desde feb 2026, subido a 25 el 23 jul
  2026. Todas las apps en development mode de una misma cuenta comparten una
  unica quota.
  Fuente: https://developer.spotify.com/blog/2026-07-23-web-api-quota-updates

## 2. Extension quota (extended quota mode) para individuos

- Desde el 15 may 2025 Spotify "only accepts applications from organizations
  (not individuals)".
  Fuente: https://developer.spotify.com/documentation/web-api/concepts/quota-modes
- Requisitos listados en la misma pagina: entidad legal registrada; servicio
  activo y lanzado; minimo 250k MAU; presencia en mercados clave de Spotify;
  viabilidad comercial; cumplimiento de Terms. Se aplica con email
  corporativo via formulario; la revision puede tardar hasta seis semanas.
  Fuente: https://developer.spotify.com/documentation/web-api/concepts/quota-modes
- Motivacion oficial y aclaracion de que apps con extended access previo no
  se ven afectadas:
  https://developer.spotify.com/blog/2025-04-15-updating-the-criteria-for-web-api-extended-access
- Conclusion: para este proyecto extended quota mode es inalcanzable. No hay
  via para un individuo sin empresa ni 250k MAU.

## 3. Endpoints deprecados o restringidos para apps nuevas

Hay dos oleadas. Ambas aplican a cualquier app creada hoy.

### 3a. Nov 27 2024 (apps en dev mode sin extension pendiente + apps nuevas)

Quitados: Related Artists, Recommendations, Audio Features, Audio Analysis,
Get Featured Playlists, Get Category's Playlists, preview URLs de 30 s en
respuestas multi-get, y playlists algoritmicas y editoriales de Spotify.
Fuente: https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api

Ninguno de esos lo usa el proyecto, salvo el bloqueo de playlists editoriales
(afecta Seed Playlists; ver 3c).

### 3b. Feb 2026 (Client IDs creados desde 11 feb 2026; para apps existentes
la parte de endpoints quedo pospuesta el 9 mar 2026, sin fecha nueva)

Fuente del changelog: https://developer.spotify.com/documentation/web-api/references/changes/february-2026
Fuente de la postergacion: https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security

Estado de cada endpoint que pregunta el ticket:

| Endpoint | Estado para app nueva |
|---|---|
| `GET /artists/{id}/albums` | Disponible (lista "Endpoints still available > Metadata"). Pierde `album_group` en la respuesta. Reference hoy dice `limit` default 5, max 10 (ver nota abajo). |
| `GET /albums/{id}` | Disponible. Pierde `available_markets`, `label`, `popularity`. |
| `GET /albums` (varios) | **Removido**. Usar `GET /albums/{id}` uno por uno. |
| `GET /albums/{id}/tracks` | Disponible. `limit` max 50. Tracks pierden `available_markets`, `linked_from`, `popularity`. |
| `GET /artists/{id}` | Disponible. Pierde `followers`, `popularity`. |
| `GET /artists` (varios) | **Removido**. |
| `GET /me/following?type=artist` | Disponible (lista "Library"). Scope `user-follow-read`. `limit` max 50, paginacion por cursor `after`. |
| `PUT /me/following` / `DELETE /me/following` | Removidos; reemplazados por `PUT /me/library` / `DELETE /me/library` con URIs. No los necesitamos. |
| `GET /me/playlists` | Disponible. `limit` max 50. |
| `GET /users/{id}/playlists` | **Removido**. |
| `POST /users/{user_id}/playlists` | **Removido**. Usar `POST /me/playlists`. |
| `POST /me/playlists` | Disponible. |
| `GET /playlists/{id}` | Disponible, pero `items` solo viene si el user es owner o colaborador. Campo `tracks` renombrado a `items`. |
| `GET /playlists/{id}/tracks` | Deprecado; usar `GET /playlists/{id}/items`. |
| `GET /playlists/{id}/items` | Nuevo. **403 si el user no es owner ni colaborador de la playlist.** Respuesta: `items[].item` (antes `items[].track`). |
| `POST /playlists/{id}/tracks` | Deprecado; usar `POST /playlists/{id}/items`. |
| `POST /playlists/{id}/items` | Nuevo. Max 100 URIs por request, body `{"uris": [...], "position": n}`, responde 201 + `snapshot_id`. Requiere owner o colaborador. |
| `DELETE /playlists/{id}/tracks` | Deprecado; usar `DELETE /playlists/{id}/items`. |
| `DELETE /playlists/{id}/items` | Nuevo. Body `{"items": [{"uri": ...}], "snapshot_id": ...}`, max 100. |
| `GET /me` | Disponible, pero pierde `country`, `email`, `explicit_content`, `followers`, `product` (marcados Deprecated en la reference). |
| `GET /markets` | **Removido**. |
| `GET /browse/new-releases` | **Removido**. |
| `GET /search` | Disponible con `limit` max 10, default 5. |

Fuentes de la tabla:
- Changelog feb 2026 (lista de removidos, renombrados y "still available"):
  https://developer.spotify.com/documentation/web-api/references/changes/february-2026
- Reference Get Artist's Albums: https://developer.spotify.com/documentation/web-api/reference/get-an-artists-albums
- Reference Get Album Tracks: https://developer.spotify.com/documentation/web-api/reference/get-an-albums-tracks
- Reference Get Followed Artists: https://developer.spotify.com/documentation/web-api/reference/get-followed
- Reference Create Playlist: https://developer.spotify.com/documentation/web-api/reference/create-playlist
- Reference Get Playlist Items: https://developer.spotify.com/documentation/web-api/reference/get-playlists-items
- Reference Add Items: https://developer.spotify.com/documentation/web-api/reference/add-items-to-playlist
- Reference Remove Items: https://developer.spotify.com/documentation/web-api/reference/remove-items-playlist
- Reference Get Current User's Profile: https://developer.spotify.com/documentation/web-api/reference/get-current-users-profile
- Reference Get Current User's Playlists: https://developer.spotify.com/documentation/web-api/reference/get-a-list-of-current-users-playlists

Nota sobre `limit` de `GET /artists/{id}/albums`: la reference hoy dice
literal "Default: 5. Minimum: 1. Maximum: 10." (dos lecturas independientes
de la pagina lo confirman). El changelog de feb 2026 solo documenta la baja
de `limit` para Search. UNVERIFIED si es un cambio real de la API o un error
de la reference; asumir 10 y paginar con `offset` hasta que `next` sea null.

### 3c. Restricciones sobre playlists ajenas

Dos reglas independientes se suman:

- Nov 2024: playlists algoritmicas y editoriales de Spotify no accesibles.
  Fuente: https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api
- Feb 2026: `GET /playlists/{id}/items` y el campo `items` de
  `GET /playlists/{id}` solo funcionan si el user es owner o colaborador;
  cualquier otra playlist devuelve 403.
  Fuente: https://developer.spotify.com/documentation/web-api/reference/get-playlists-items

Resultado: una Seed Playlist solo puede ser una playlist propia o colaborativa
del User. No sirven playlists publicas de terceros ni editoriales.

## 4. Semantica de `include_groups` y `appears_on`

Hechos literales de la reference
(https://developer.spotify.com/documentation/web-api/reference/get-an-artists-albums):

- `include_groups`: "A comma-separated list of keywords that will be used to
  filter the response. If not supplied, all album types will be returned.
  Valid values are: album, single, appears_on, compilation".
- `album_group` (campo de respuesta): "This field describes the relationship
  between the artist and the album." Valores: `album`, `single`,
  `compilation`, `appears_on`. Esta marcado **Deprecated** en la reference y
  figura como campo removido de Album en el changelog feb 2026.
- `album_type` (campo de respuesta): "The type of the album". Valores:
  `album`, `single`, `compilation`. Este sigue.
- `market`: "If a valid user access token is specified in the request header,
  the country associated with the user account will take priority over this
  parameter. Note: If neither market or user country are provided, the
  content is considered unavailable for the client."

Que devuelve `appears_on`: la doc no define la categoria mas alla de
"relationship between the artist and the album". Interpretacion operativa
(UNVERIFIED como texto oficial, pero consistente con `album_type` +
`artists`): son albums donde el artista figura en algun track pero no es uno
de los artistas del album (`album.artists`). Un album con `album_group =
appears_on` puede tener `album_type` album, single o compilation.

Como saber en que tracks figura el artista: `GET /artists/{id}/albums` no lo
dice. Hay que llamar `GET /albums/{id}/tracks`; cada SimplifiedTrackObject
trae `artists[]` con `id` y `name`, y se filtra por `artist.id`.
Fuente: https://developer.spotify.com/documentation/web-api/reference/get-an-albums-tracks

Como saber a que grupo pertenece cada album si `album_group` ya no viene:
hacer una llamada por grupo (`include_groups=album`, `=single`,
`=appears_on`) y etiquetar del lado nuestro. Un mismo album puede aparecer
en mas de un grupo si la misma persona figura como artista principal y
tambien como feat, asi que deduplicar por `album.id`.

## 5. `release_date_precision`

Hechos (reference Get Album y Get Artist's Albums):

- `release_date`: "The date the album was first released". Ejemplo oficial:
  `"1981-12"`.
- `release_date_precision`: valores `year`, `month`, `day`.
  Fuentes: https://developer.spotify.com/documentation/web-api/reference/get-an-album
  y https://developer.spotify.com/documentation/web-api/reference/get-an-artists-albums

La doc no prescribe como tratar `month` / `year`. Los formatos son `YYYY`,
`YYYY-MM`, `YYYY-MM-DD` (inferido del ejemplo y de los valores de precision;
UNVERIFIED como texto oficial). No hay campo de hora ni timezone.

## 6. Rate limits y 429

Fuente: https://developer.spotify.com/documentation/web-api/concepts/rate-limits

- "Spotify's API rate limit is calculated based on the number of calls that
  your app makes to Spotify in a rolling 30 second window."
- El numero exacto no esta publicado. Extended quota mode tiene "a rate limit
  that is much higher than apps in development mode".
- Al pasarse: HTTP 429. "The header of the 429 response will normally include
  a `Retry-After` header with a value in seconds." Recomendacion oficial:
  esperar esos segundos antes de reintentar.
- Recomendaciones oficiales: usar endpoints batch (ya no existen para
  albums/artists/tracks en dev mode, ver 3b), usar `snapshot_id` de playlists,
  lazy loading, revisar patrones de uso en el dashboard.
- Ademas del rate limit hay una **quota** separada para dev mode: "development
  mode apps also have quota restrictions which have a different enforcement
  mechanism than rate limits". Los endpoints se agrupan en "quota buckets"
  con limite compartido; "The specific groupings and limits are subject to
  change" y no se publican numeros. La quota se cuenta por cuenta de
  developer (todas las apps dev mode de la cuenta la comparten). Al agotarla,
  429 con body:

  ```json
  { "error": { "status": 429, "message": "Too many requests", "reason": "QUOTA_EXCEEDED" } }
  ```

  Fuentes: https://developer.spotify.com/documentation/web-api/concepts/quota-modes
  y https://developer.spotify.com/documentation/web-api/references/changes/july-2026

Tratamiento: distinguir 429 sin `reason` (rate limit, reintentar tras
`Retry-After`) de 429 con `reason = QUOTA_EXCEEDED` (quota agotada, no sirve
reintentar en segundos; abortar el Run y reprogramar).

## 7. Scopes necesarios

Fuente: https://developer.spotify.com/documentation/web-api/concepts/scopes
mas las paginas de reference de cada endpoint.

| Necesidad | Endpoint | Scope |
|---|---|---|
| Leer follows | `GET /me/following?type=artist` | `user-follow-read` |
| Leer playlists privadas del user | `GET /me/playlists`, `GET /playlists/{id}/items` | `playlist-read-private` (+ `playlist-read-collaborative` para que aparezcan las colaborativas) |
| Crear playlist | `POST /me/playlists` | `playlist-modify-public` si `public=true` (default), `playlist-modify-private` si `public=false`; colaborativa requiere ambos |
| Agregar items | `POST /playlists/{id}/items` | `playlist-modify-public` o `playlist-modify-private` segun visibilidad de la Target Playlist |
| Quitar items | `DELETE /playlists/{id}/items` | idem |
| Leer perfil | `GET /me` | `user-read-private` para `country` / `product`; `user-read-email` para `email` |

Sobre `country`: el scope sigue existiendo pero el campo esta marcado
Deprecated y removido de la respuesta de `GET /me` para apps en dev mode
(changelog feb 2026). Pedir `user-read-private` no lo devuelve.
Fuente: https://developer.spotify.com/documentation/web-api/reference/get-current-users-profile

Set minimo sugerido: `user-follow-read playlist-read-private
playlist-read-collaborative playlist-modify-private playlist-modify-public`.
`id` y `display_name` de `GET /me` vienen sin scope extra; `user-read-private`
hoy no aporta nada util en dev mode porque `country` y `product` no se
devuelven.

---

## Implicaciones para el proyecto

1. **Techo real: 5 users, no 25.** El mapa #1 dice 25; corregir. Con "Luca +
   un amigo" alcanza, pero no hay margen para crecer sin otra cuenta de
   developer (cada cuenta puede tener 25 Client IDs, cada uno con 5 users,
   pero comparten quota y cada owner necesita Premium).
2. **Luca necesita Spotify Premium activo** mientras la app viva. Si se cae el
   Premium, la app deja de funcionar para todos los users.
3. **`GET /me` -> `country` no existe mas en dev mode.** El mapa asume que el
   market sale de ahi. Alternativa que la doc garantiza: no pasar `market` y
   usar el access token del user en `GET /artists/{id}/albums` y
   `GET /albums/{id}/tracks`; Spotify aplica el pais de la cuenta
   automaticamente ("the country associated with the user account will take
   priority"). Todas las llamadas de un Run deben ir con el token del User,
   nunca con client credentials, porque sin market ni user country "the
   content is considered unavailable".
4. **Seed Playlists solo propias o colaborativas.** `GET /playlists/{id}/items`
   da 403 para cualquier otra. Ajustar el onboarding: el User elige entre sus
   playlists (`GET /me/playlists`), no pega un link arbitrario.
5. **Sin batch.** `GET /albums?ids=` y `GET /artists?ids=` no existen. Cada
   Release nuevo cuesta como minimo 1 request a `GET /albums/{id}/tracks`
   (o `GET /albums/{id}`, que trae los primeros 20 tracks). Con Whitelists
   grandes el costo por Run es N artistas x 3 grupos x paginas + M albums
   nuevos. Cachear `album.id` ya vistos por User.
6. **`album_group` ya no viene.** Para distinguir album / single / appears_on
   hay que llamar `include_groups` de a uno y etiquetar localmente; deduplicar
   por `album.id`.
7. **`limit` de artist albums posiblemente 10.** Armar la paginacion sin
   asumir 50; seguir `next` hasta null. Para un Run semanal alcanza con la
   primera pagina si el orden es por fecha desc (UNVERIFIED: la doc no
   documenta el orden; verificar en runtime y cortar cuando `release_date`
   sea anterior a la ventana).
8. **`release_date_precision`:** un Run mira una ventana de dias, asi que
   solo `day` se puede comparar con precision. Regla propuesta: `month` ->
   tratar como dia 1 del mes; `year` -> tratar como 1 de enero; y en ambos
   casos incluir solo si esa fecha cae dentro de la ventana. En la practica
   los lanzamientos nuevos vienen con `day`; `month`/`year` aparecen en
   catalogo viejo re-subido, que no queremos igual.
9. **Endpoints a usar (nombres nuevos):** `POST /me/playlists`,
   `GET/POST/DELETE /playlists/{id}/items`, `items[].item` en las respuestas.
   No usar `/tracks` ni `POST /users/{id}/playlists` aunque sigan respondiendo
   hoy como deprecados.
10. **429 doble:** rate limit (ventana 30 s, honrar `Retry-After`) y
    `QUOTA_EXCEEDED` por cuenta de developer (abortar el Run, reintentar en
    la siguiente ventana). Escalonar los Runs de los 5 users para no chocar
    entre si; la quota es una sola para todos.
11. **Extended quota mode: descartado definitivamente.** No es cuestion de
    pedirlo; requiere empresa registrada y 250k MAU.
