# Spotify New Releases

Servicio multiusuario que centraliza en una playlist de Spotify la musica nueva
de un conjunto de artistas elegido por cada usuario.

## Language

### Personas

**User**:
Persona que conecto su cuenta de Spotify al servicio y tiene una configuracion propia.
_Avoid_: Subscriber, cuenta, perfil

**Spotify App**:
Registro en el dashboard de desarrolladores de Spotify (client id + secret) a traves del cual
un User autoriza al servicio. No es una cuenta de Spotify.
_Avoid_: app (a secas, ambiguo con el servicio), credenciales

**Shared App**:
La Spotify App registrada por Luca. Ofrece hasta 5 cupos, reservados para Users no tecnicos.
_Avoid_: app de Luca, app global

**Own App**:
Spotify App registrada por el propio User. Modo preferido; requiere Premium del User.
_Avoid_: BYO app, app propia (en codigo)

### Artistas

**Whitelist**:
Conjunto final de artistas que un User vigila. Es la union de sus Artist Sources.
_Avoid_: Watchlist, tracked artists, artistas seguidos (ambiguo con la fuente de Spotify)

**Artist Source**:
Origen del que se derivan artistas para la Whitelist: una playlist, una lista manual,
o los artistas que el User sigue en Spotify.
_Avoid_: fuente, input

**Seed Playlist**:
Playlist propia o colaborativa del User usada como Artist Source. Aporta sus artistas
principales (y opcionalmente los feats). Playlists ajenas o editoriales no sirven.
_Avoid_: playlist referencia, playlist origen

### Musica

**Release**:
Album o single de un artista de la Whitelist, o track ajeno donde figura (appears_on), con
`release_date` de dia dentro de la ventana de un Run y nunca visto antes por ese User. Nunca
una compilation. Unidad de deteccion; entra completo o no entra.
_Avoid_: lanzamiento, novedad, drop

**Guardado**:
Track que el User tiene como liked song o dentro de una playlist propia o colaborativa
distinta de la Target Playlist. Un album guardado no hace guardados a sus tracks.
_Avoid_: likeado, en biblioteca, escuchado

**Track**:
Cancion individual que se agrega a la Target Playlist. Un Release rinde uno o mas Tracks.
Unidad de escritura.
_Avoid_: song, cancion, tema

**Target Playlist**:
Playlist del User donde se agregan los Tracks nuevos.
_Avoid_: playlist destino, output playlist

### Ejecucion

**Tick**:
Una invocacion del scheduler. Dispara cero o mas Runs segun que Users esten vencidos.
_Avoid_: cron, job, sweep, corrida del scheduler

**Run**:
Una ejecucion del bot para un User. Su ventana son los ultimos 7 dias, y para cada artista
no antes de su dia de alta en la Whitelist. Arma un Lote y lo inserta; es exitoso solo si
el Lote quedo insertado completo. Un Tick dispara Runs; un Run nunca dispara otro.
_Avoid_: job, ejecucion, corrida

**Lote**:
Lista ordenada de Tracks que un Run decidio agregar, persistida antes de insertar, con el
estado de insercion de cada Track. Un Run que falla a mitad se retoma desde su Lote.
_Avoid_: batch, plan, cola
