---
status: accepted
---

# Varios Canales por User

El mapa dejaba "varias Target Playlists por User" fuera de scope. Lo revertimos: un User arma
hasta 5 **Canales**, cada uno con su Whitelist, su Target Playlist, su Retencion y su filtro
de Guardado, para separar por ejemplo "reggaeton nuevo" de "rock nuevo". Canal y Target
Playlist van 1 a 1: el Canal nace con su playlist y muere con ella.

## Considered Options

- **Una Whitelist por User y varias Target Playlists**: los mismos Tracks en varias
  playlists, sin uso real.
- **Canales de artistas compartidos entre Targets (N a M)**: mas flexible, pero innecesario
  por ahora. Queda anotado en el mapa.

## Consequences

- Whitelist, Artist Sources, fecha de alta por artista, Lotes, Entregas y el registro de
  Releases ya incluidos cuelgan del Canal, no del User. Un artista en dos Canales rinde sus
  Releases en ambos.
- Cada artista se explora una sola vez por User y por dia y el resultado se reparte entre los
  Canales que lo tienen. El costo en requests crece con los artistas distintos, no con la
  cantidad de Canales. El tope de 5 existe porque la quota de la Shared App la comparten todos.
- Los Canales fallan por separado: la Entrega de uno no bloquea a otro.
- Una playlist es Target de un solo Canal a la vez. Ninguna Target Playlist cuenta como
  Guardado. Una Target puede ser Seed Playlist; si el User lo hace, la Whitelist crece sola y
  es su decision.
- Desvincular un Canal lo borra y ofrece eliminar tambien la playlist (`DELETE /me/library`,
  suma el scope `user-library-modify`). Si el User borra la playlist en Spotify, el Canal se
  borra en la proxima Entrega.
