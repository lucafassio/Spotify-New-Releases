---
status: accepted
---

# Lista manual por defecto, Seed Playlists excluyentes, sin Seguidos y Copia de playlist

Prender una playlist metia todos sus artistas de una, y para ver que aportaba habia que leerla
de Spotify cada vez: el cache vivia en memoria, duraba 5 minutos y moria cuando Render se dormia.
Eso gastaba quota y obligaba a esperar. Ademas la Whitelist mezclaba tres origenes, asi que un
artista podia estar a la derecha sin que hubiera forma de sacarlo.

Decidimos que **cada Canal arma su Whitelist de una sola forma**. Por defecto, la Lista manual:
el User elige artistas uno por uno desde sus playlists o el Buscador. Si prende al menos una Seed
Playlist en Ajustes, el Canal pasa a Seed: la lista de playlists queda apagada y la Whitelist solo
se lee. **Seguidos desaparece**, incluido el scope `user-follow-read`. Los feats solo existen como
opcion por Seed Playlist; en la Lista manual un invitado se elige como cualquiera.

Para leer menos, cada playlist abierta deja una **Copia de playlist** en la DB: se muestra al
instante y por detras se confirma con el `snapshot_id` de `GET /me/playlists` (una llamada cada
50 playlists, ya la necesitamos para listarlas). Si coincide, no se lee nada. Si cambio y crecio,
se lee solo la cola desde el ultimo Track conocido; si ese Track sigue en su posicion, fueron solo
agregados y alcanza con esa llamada. En cualquier otro caso se relee entera.

## Medicion (2026-11-06, API real, playlist de prueba de Luca)

- `snapshot_id` y `total` de `GET /me/playlists` no cambian al instante: seguian iguales a los
  30 s y cambiaron antes de los 60 s.
- `GET /playlists/{id}` da el `total` nuevo al instante, pero su `snapshot_id` tambien tarda.
- `GET /playlists/{id}/items` con `offset` ve el Track nuevo al instante.
- Sacar lo agregado da un `snapshot_id` nuevo, no vuelve al anterior: es version, no hash.

## Considered Options

- **Espera fija (actualizar una vez por dia)**: la mas simple, pero un artista agregado a una
  playlist no aparece hasta el dia siguiente, y no ahorra nada frente a comparar la huella.
- **Leer al abrir con `GET /playlists/{id}`**: ve el `total` sin demora, pero cuesta una llamada
  por cada apertura y justo lo que queriamos evitar era que repetir clicks gaste quota.
- **Guardar toda la biblioteca en el primer login**: 60 playlists de 300 temas son ~360 llamadas
  de golpe, con riesgo de agotar la quota compartida por 24 h.
- **Mantener Seguidos sumado a la Lista manual**: habria artistas a la derecha sin flecha posible,
  o una lista de exclusiones. No se usaba lo suficiente para justificarlo.
- **Lista manual y Seed sumadas**: mismo problema con cualquier artista que venga de una Seed.

## Consequences

- Hasta ~1 minuto despues de editar en Spotify (mas el cache de biblioteca de la web, 5 min desde #24) la
  fila puede decir "Al dia" sin estarlo. Se acepta a cambio de que repetir aperturas no gaste
  llamadas de items.
- Un error posible y aceptado: sacar y meter un tema en el medio y ademas agregar otro al final
  entre dos chequeos no se detecta hasta la proxima relectura entera.
- La Lista manual y las Seed Playlists de un Canal se guardan aparte: probar Seed no borra nada.
  Un artista que sale de la Whitelist y vuelve es alta nueva (ADR 0006).
- La flecha lleva el ISRC de la Copia de playlist a la Lista manual, asi el vinculo con Deezer
  sigue saliendo por ISRC (ADR 0007). Lo que se busca a mano va por nombre.
- En Lista manual el Run no lee playlists: el costo en Spotify de ese Canal baja a lo de la
  Entrega y la deteccion por Release.
- Migracion: todo Canal pasa a Lista manual con su Whitelist de hoy y su `added_at`. Se pierde
  que Seed Playlists tenia prendidas; en prod era un solo Canal, de Luca.
- Una playlist inutilizable (ajena no colaborativa, o Target del mismo Canal) va al fondo de la
  lista como "No disponible". Enmienda ADR 0005, que dejaba usar la propia Target como Seed.
