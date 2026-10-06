# Deteccion de Releases: quota de Spotify y Deezer como fuente

Ticket: https://github.com/lucafassio/spotify-new-releases/issues/15 (decision en ADR 0007).
Fecha: 2026-10-03 / 2026-10-05. Mediciones propias contra la API real + docs oficiales.
Lo no confirmado por fuente oficial esta marcado UNVERIFIED.

## 1. Mediciones contra Spotify (cuenta de Luca, Shared App)

- `GET /artists/{id}/albums` con `limit=50` da `400 Invalid limit`; `limit=10` anda.
- Grupos `album` y `single` vienen por `release_date` desc. `appears_on` viene sin orden: en
  Bad Bunny (1335 items) los lanzamientos de 2026 aparecen en las posiciones 39, 72 y 83.
- `appears_on` total en 83 artistas principales de las playlists de Luca: suma 19172,
  mediana 103, maximo 1532 (J Balvin). Recorrerlo completo = ~1950 paginas.
- Despues de ~100 llamadas a `/artists/{id}/albums` en pocos minutos: `429`,
  `{"reason":"QUOTA_EXCEEDED"}`, `Retry-After: 86207` (~24 h). En el mismo momento `GET /me`
  respondia 200: la quota va por bucket de endpoints. Dos dias despues la quota estaba
  repuesta.
- `search?q=upc:<upc>&type=album&limit=1` encontro 2 de 2 albums de Deezer, con el mismo
  nombre, fecha y artistas.

## 2. Docs de Spotify

- Quota modes (https://developer.spotify.com/documentation/web-api/concepts/quota-modes):
  "Endpoints are grouped into quota buckets and requests to endpoints in the same bucket
  count toward a shared limit." No lista buckets, numeros ni ventana.
- Changelog julio 2026
  (https://developer.spotify.com/documentation/web-api/references/changes/july-2026): las
  quotas de development mode se cuentan "per developer account rather than per Client ID".
- Search (https://developer.spotify.com/documentation/web-api/reference/search): filtros
  `upc`, `isrc`, `tag:new`; "The `upc`, `tag:new` and `tag:hipster` filters can only be used
  while searching albums." `limit` 0-10.
- Reportes de la comunidad 2026 con QUOTA_EXCEEDED y `Retry-After` de 13-24 h en otros
  endpoints, incluido `/search` (UNVERIFIED si comparte bucket con artist albums). Ningun
  numero publico de llamadas; las ~100 medidas aca son el unico dato duro.

## 3. Deezer (API publica, https://developers.deezer.com/api)

- Sin token para `/search/artist`, `/artist/{id}/albums`, `/album/{id}`, `/track/{id}`,
  `/track/isrc:{isrc}`.
- `/artist/{id}/albums?limit=100`: trae `release_date` (siempre YYYY-MM-DD), `record_type`
  (album, ep, single, compile), `explicit_lyrics`, `title`, `id`, sin UPC. Orden agrupado por
  tipo y casi por fecha, no estricto: hay que leer todas las paginas (150-185 items en
  artistas grandes = 2 paginas).
- Incluye discos ajenos donde el artista esta acreditado a nivel album (Myke Towers aparece
  en "YO QUIERO (REMIX)" de Hades66 y en "Secretos" de Hanzel La H).
- Publica clean y explicit como discos separados ("Algun Dia" y "Algun Dia (Clean)", UPCs
  distintos).
- `/album/{id}` trae `upc`, `contributors` y tracks (sin ISRC); `/track/{id}` trae `isrc`.
- `/track/isrc:{isrc}` devuelve el track con `artist` y `contributors`: sirve para vincular
  un artista de Spotify con su id de Deezer.
- `/search/artist?q=` suele dar 2-3 homonimos exactos; el de mas `nb_fan` fue el correcto
  en los 15 probados.
- 15 artistas: 40 llamadas en 26 s. Rate limit "50 requests per 5 seconds", error code 4
  "Quota limit exceeded" (fuentes de terceros, UNVERIFIED). FAQ oficial: "there is no
  limitation on data in the API, but there is a query quota", sin tope diario documentado.
- Terminos (https://developers.deezer.com/termsofuse): uso "strictly limited for a
  non-commercial purpose"; Deezer puede cortar el acceso "without any prior notice".

## 4. Alternativas descartadas

- MusicBrainz: 1 request/s promedio, browse de release-groups hasta 100 por pagina, sin
  orden por fecha; catalogo editado a mano que llega tarde a lo nuevo.
- iTunes Search/Lookup: "approximately 20 calls per minute", `lookup?id=&entity=album`, sin
  UPC (UNVERIFIED).

## Implicancia

Descubrir en Deezer (~2.5 llamadas por artista, gratis) y gastar Spotify solo por Release
nuevo: `search upc:` + leer Tracks, ~2 llamadas.
