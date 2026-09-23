# Spotify New Releases

Servicio multiusuario que centraliza en playlists de Spotify la musica nueva de conjuntos de
artistas elegidos por cada usuario.

## Language

### Personas

**User**:
Persona que conecto su cuenta de Spotify al servicio. Tiene uno o mas Canales.
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

### Canales

**Canal**:
Unidad que configura un User: una Whitelist, una Target Playlist, una Retencion y el filtro de
Guardado. Cada Canal es independiente: tiene sus propios Lotes y Entregas, y un artista en dos
Canales rinde sus Releases en ambos. Un User tiene hasta 5. El Canal vive y muere con su
Target Playlist: desvincularla o que el User la borre en Spotify borra el Canal. Al
desvincular, la playlist queda en la biblioteca del User, salvo que elija eliminarla tambien.
_Avoid_: feed, radar, suscripcion

**Whitelist**:
Conjunto final de artistas que vigila un Canal. Es la union de sus Artist Sources.
_Avoid_: Watchlist, tracked artists, artistas seguidos (ambiguo con la fuente de Spotify)

**Artist Source**:
Origen del que se derivan artistas para una Whitelist: una playlist, una lista manual,
o los artistas que el User sigue en Spotify.
_Avoid_: fuente, input

**Seed Playlist**:
Playlist propia o colaborativa del User usada como Artist Source. Aporta sus artistas
principales (y opcionalmente los feats). Playlists ajenas o editoriales no sirven.
_Avoid_: playlist referencia, playlist origen

### Musica

**Release**:
Album o single de un artista de la Whitelist, o track ajeno donde figura (appears_on), con
`release_date` de dia dentro de la ventana de un Run y que no figura en ningun Lote anterior
del Canal. Nunca una compilation. Unidad de deteccion; entra completo o no entra.
_Avoid_: lanzamiento, novedad, drop, visto

**Guardado**:
Track que el User tiene como liked song o dentro de una playlist propia o colaborativa que
no sea ninguna de sus Target Playlists. Un album guardado no hace guardados a sus tracks.
_Avoid_: likeado, en biblioteca, escuchado

**Track**:
Cancion individual que se agrega a una Target Playlist. Un Release rinde uno o mas Tracks.
Unidad de escritura.
_Avoid_: song, cancion, tema

**Target Playlist**:
Playlist propia del User donde un Canal agrega sus Tracks. La crea el bot o el User asigna una
existente; una playlist es Target de un solo Canal a la vez. Se identifica por su id de
Spotify, asi que el User puede renombrarla o editarla libremente. Una existente tiene siempre
Retencion Acumulativa. Si el User la borra, su Canal se borra.
_Avoid_: playlist destino, output playlist

**Retencion**:
Cuantos Lotes entregados conserva la Target Playlist de un Canal: un numero N (quedan los
ultimos N, con N=1 la playlist se renueva entera cada semana) o Acumulativa (nunca se
retira). Retira solo Tracks del bot. Solo configurable si la Target Playlist la creo el bot.
_Avoid_: modo, rolling, limpieza

### Ejecucion

**Tick**:
Una invocacion del scheduler. Dispara cero o mas Runs segun que Users tengan algo pendiente.
_Avoid_: cron, job, sweep, corrida del scheduler

**Run**:
Una ejecucion diaria del bot para un User. Explora los ultimos 7 dias (para cada artista no
antes de su dia de alta en la Whitelist del Canal) y suma al Lote abierto de cada Canal los
Tracks nuevos como pendientes. Si algun Canal tiene un Lote cerrado sin entregar, tambien hace
su Entrega. Es exitoso solo si todos los Canales cerraron lo que les tocaba. Un Tick dispara
Runs; un Run nunca dispara otro.
_Avoid_: job, ejecucion, corrida

**Lote**:
Tracks de una semana de un Canal: lo llenan los Runs de sabado a viernes y se cierra el
viernes despues de explorar. Cada Track pasa por pendiente, insertado y retirado. Lo que se
descubre despues del cierre va al Lote siguiente. Un Lote cerrado que no se entrego se suma al
siguiente y salen juntos en una sola Entrega.
_Avoid_: batch, plan, cola

**Entrega**:
Paso semanal que lleva un Lote cerrado a la Target Playlist de su Canal: filtra el Lote con la
config tal como esta en ese momento, inserta, retira lo que la Retencion deja afuera y, en una
Target Playlist creada por el bot, recorta al tope retirando sus Lotes mas viejos.
Corre el viernes y, si falla, se reintenta en los Runs siguientes hasta salir completa. Si la
Target Playlist ya no existe, no hay Entrega: el Canal se borra y su Lote se pierde.
_Avoid_: upload, subida, sync, publicacion
