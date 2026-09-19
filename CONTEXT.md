# Spotify New Releases

Servicio multiusuario que centraliza en una playlist de Spotify la musica nueva
de un conjunto de artistas elegido por cada usuario.

## Language

### Personas

**User**:
Persona que autorizo la app con su cuenta de Spotify y tiene una configuracion propia.
_Avoid_: Subscriber, cuenta, perfil

### Artistas

**Whitelist**:
Conjunto final de artistas que un User vigila. Es la union de sus Artist Sources.
_Avoid_: Watchlist, tracked artists, artistas seguidos (ambiguo con la fuente de Spotify)

**Artist Source**:
Origen del que se derivan artistas para la Whitelist: una playlist, una lista manual,
o los artistas que el User sigue en Spotify.
_Avoid_: fuente, input

**Seed Playlist**:
Playlist usada como Artist Source. Aporta sus artistas principales (y opcionalmente los feats).
_Avoid_: playlist referencia, playlist origen

### Musica

**Release**:
Album, single o aparicion (appears_on) de un artista de la Whitelist publicado dentro de la
ventana de un Run. Unidad de deteccion.
_Avoid_: lanzamiento, novedad, drop

**Track**:
Cancion individual que se agrega a la Target Playlist. Un Release rinde uno o mas Tracks.
Unidad de escritura.
_Avoid_: song, cancion, tema

**Target Playlist**:
Playlist del User donde se agregan los Tracks nuevos.
_Avoid_: playlist destino, output playlist

### Ejecucion

**Run**:
Una ejecucion del bot para un User. Define la ventana temporal: desde el Run anterior
hasta ahora.
_Avoid_: job, ejecucion, corrida
