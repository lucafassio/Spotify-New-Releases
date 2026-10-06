---
status: accepted
---

# Deteccion de Releases en Deezer, Spotify solo para lo esencial

Al arrancar el Run (#15) medimos la API real: despues de ~100 llamadas a
`GET /artists/{id}/albums`, Spotify respondio `429 QUOTA_EXCEEDED` con `Retry-After` de ~24 h,
mientras `/me` seguia andando. Desde julio 2026 la quota de development mode es **por cuenta
de developer** (todas las Spotify Apps de Luca y todos los Users de la Shared App comparten
una), se agrupa por buckets de endpoints y Spotify no publica numeros. Ademas `limit` maximo
es 10, `appears_on` viene sin orden y es enorme (mediana ~100 por artista, Bad Bunny 1335).
La deteccion de #5 (3 llamadas por artista por dia, paginacion completa al cambiar el
Checkpoint) pide ~500 llamadas diarias solo para los ~160 artistas de Luca: no entra.

Decidimos **descubrir en Deezer y usar Spotify solo para lo que nadie mas puede hacer**:
pasar un Release a su album de Spotify, leer sus Tracks y escribir en la Target Playlist.
La API publica de Deezer no pide token ni tiene quota diaria documentada (~50 requests cada
5 s), y `GET /artist/{id}/albums` lista discos, EPs y singles del artista, incluidos los
ajenos donde figura acreditado. Cada album de Deezer trae su UPC, y `search?q=upc:` en
Spotify lo encuentra exacto. El costo en Spotify pasa a ser ~2 llamadas por Release nuevo
(buscar por UPC + leer Tracks) en vez de 3 o mas por artista por dia. Research y mediciones en
la rama `research/deteccion-deezer`.

## Considered Options

- **Solo Spotify, rotando artistas**: cada artista una vez por semana para entrar en la
  quota. Nada se pierde, pero un Release puede llegar 8 dias tarde y la quota compartida
  sigue sin alcanzar para 5 Users.
- **Solo Spotify, sin `appears_on`**: baja a 2 llamadas por artista por dia, sigue en ~330
  para Luca solo.
- **MusicBrainz**: 1 request por segundo, catalogo editado a mano que llega tarde a lo nuevo.
- **iTunes Search API**: ~20 llamadas por minuto, sin UPC para pasar a Spotify.

## Consequences

- Cada artista de una Whitelist se vincula a su artista de Deezer una sola vez, y el vinculo
  es global (no por User). Primero por ISRC: un Track de una Seed Playlist donde figura el
  artista, `GET /track/isrc:{isrc}` en Deezer. Si no hay ISRC (lista manual, follows), por
  nombre exacto, y entre homonimos gana el de mas fans. Un artista sin vinculo no se explora.
- Release se sigue definiendo igual pero se detecta en Deezer: todo lo que Deezer lista para
  el artista salvo `record_type = compile`, con `release_date` en la ventana del Run. Si el
  artista de la Whitelist esta en `album.artists` de Spotify entran todos los Tracks; si no,
  solo los Tracks donde figura (vuelven los feats en discos ajenos, sin costo extra). Un feat
  en un tema suelto de un disco ajeno que Deezer no acredita a nivel album no se ve.
- Deezer publica versiones clean y explicit como discos separados: si dos discos del mismo
  dia tienen el mismo titulo salvo "(Clean)", se queda el explicit.
- El paso Deezer -> Spotify se cachea global por album de Deezer. Si Spotify todavia no lo
  tiene, se reintenta en los Runs siguientes mientras siga en la ventana.
- Las Seed Playlists se releen solo si cambio su `snapshot_id` (lo trae `GET /me/playlists`).
- Un `429 QUOTA_EXCEEDED` de Spotify aborta el Run como fallido; el Tick lo reintenta otro
  dia. Nada de reintentos en loop.
- Reemplaza el Checkpoint de #5 y ADR 0006: con Deezer gratis se lee la discografia completa
  cada dia y no hace falta cortar paginacion. `include_groups` y `release_date_precision`
  dejan de aplicar.
- Dependemos de los terminos de Deezer (uso no comercial, pueden cortar el acceso sin aviso).
  Si pasa, el plan B es MusicBrainz o iTunes con el mismo vinculo por artista.
- Cualquier prueba contra la API real de Spotify se hace con 1-3 llamadas: la quota es la
  misma que usa produccion.
