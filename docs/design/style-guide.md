# Style guide

Guia viva de la UI. Dos objetivos: que la UI se vea coherente y que Luca y el agente llamen a
cada elemento por el mismo nombre. Se actualiza en el mismo commit que cambia la UI.

Como usar los nombres: en el prototipo, el boton **Nombres** de la barra de abajo (o la tecla
`N`) marca cada elemento. Al pasar el mouse muestra el nombre y la ruta completa, por ejemplo
`Panel > Columna izquierda > Fila de playlist`. Un click copia el nombre. Para pedir un
cambio alcanza con "en la Fila de playlist, ...". Si un nombre choca con el glosario de
`CONTEXT.md`, gana el glosario y se renombra aca.

Estado: base definida (#10). Layout base elegido en #7 (ronda 4, dashboard fijo); pulido visual pendiente.

Interacciones de Vista Canal: la Flecha de artista (y un Resultado del Buscador) mueve la fila en el acto, sin esperar al servidor, y actualiza
las dos columnas y el contador de la Barra lateral. Al sacar, la fila vuelve a la playlist abierta al instante si salio de ahi; si no,
aparece cuando responde el servidor. Varios clicks seguidos se mandan en orden. Si el servidor rechaza o falla, la fila vuelve a su
lugar y aparece un Aviso de accion ("No pudimos guardar el cambio. Proba de nuevo." si no trae uno propio).
Una sola Fila de playlist abierta a la vez: abrir otra pliega la anterior. La fila muestra su copia guardada al instante y la actualiza por detras.
La lista nunca se reordena mientras se lee: lo que llega durante la lectura va al Bloque de nuevos, al fondo.
Con Seed Playlists prendidas, la Columna izquierda queda apagada y no responde.
Un cambio hecho directamente en Spotify puede tardar uno o dos minutos en verse (ADR 0008).
Durante un envio se deshabilita su boton; los errores de conexion o limites de Spotify aparecen como Aviso de accion sin borrar la vista.
Navegar entre vistas (Barra lateral, Boton Ajustes, Nuevo Canal) cambia solo el Panel y la Barra lateral; la pagina entera llega solo al entrar por URL o recargar.
Apoyar el mouse sobre una Fila de Canal precarga su vista: el click la muestra sin esperar al servidor.
Los tokens y el CSS de todas las vistas viven en `src/app/static/app.css` (un solo archivo, cacheado con version en la URL).

## Principios

- **Configurar, no mirar.** Se muestra lo que el User elige o necesita saber para elegir. No
  van: canciones agregadas o por agregar, historial de Entregas, horarios, Runs, ni
  explicaciones de como trabaja el bot. Un dato interno aparece solo si pide una accion (ej:
  Medidor de tope al 90%).
- **Dashboard de pantalla fija.** La pagina nunca scrollea: ocupa el alto de la ventana. Scrollea
  cada panel por dentro, con la barra de scroll del diseno (fina, `--scroll`, pildora, sin
  flechas). En celular (< 860px) se apila y ahi si scrollea la pagina.
- **Pantalla llena.** Al menos 70% del area con contenido util. Lo que sobra se llena con
  pistas sacadas de la biblioteca del User (quien pesa, que falta, que se repite), nunca con
  relleno decorativo.
- **Listas, no grillas**, para playlists: cada fila se despliega para mostrar sus artistas.
- **Texto sin seleccion.** Nada se selecciona con el mouse (`user-select: none` en `body`), salvo
  los campos donde se escribe.

## Color

Tema unico oscuro, a proposito: la app vive al lado de Spotify y comparte su base negro + verde.
Verde solo para accion principal, seleccion y datos que cuentan. Nada de verde decorativo.

| Token | Hex | Uso |
| --- | --- | --- |
| `--negro` | `#000000` | Fondo de pagina y de columnas, texto sobre verde |
| `--superficie` | `#141414` | Paneles (Barra lateral, Panel) y cajas dentro de columnas |
| `--elevada` | `#1E1E1E` | Inputs, fichas apagadas, filas en hover, pista de barras |
| `--elevada-2` | `#2A2A2A` | Fila seleccionada, pista de Interruptor apagado |
| `--linea` | `#262626` | Separadores |
| `--texto` | `#F5F5F2` | Texto principal |
| `--texto-2` | `#A7A9A4` | Texto secundario |
| `--texto-3` | `#6E716B` | Pistas, "+N mas", barras de lo que no esta en ningun Canal |
| `--verde` | `#1ED760` | Boton primario, Interruptor encendido, seleccion, barras |
| `--verde-hondo` | `#13A049` | Rayado de "guarda todo" en Semanas |
| `--verde-suave` | 13% de verde | Fondo de Aviso ok |
| `--verde-tinta` | `#04210F` | Texto secundario sobre verde |
| `--scroll` / `--scroll-hover` | `#333333` / `#4A4A4A` | Barra de scroll de los paneles |
| `--lima` | `#C6F432` | Solo herramientas del prototipo (capa de Nombres) |
| `--ambar` | `#F5B642` | Aviso: tope al 90%, Premium |
| `--rojo` | `#FF6B6B` | Error, tope al 95%, Desvincular |

**Portadas generadas**: cada Canal y cada playlist tiene un tono (`--h`, 0-360) que arma un
degradado con la inicial grande en negro translucido. El tono del Canal tine su Cabecera.
Si Spotify tiene portada de la playlist (la del Canal es la de su Target), va encima del
degradado; el degradado queda de fondo si no hay o no carga.

**Avatares**: circulo con el tono del nombre y la inicial; encima va la foto: la de Spotify en
el Buscador de artistas, y si no la de Deezer guardada por artista, directo del CDN. Las fotos
son lo ultimo que aparece: un avatar sin foto guardada sale con la inicial y `data-foto`, y
`static/fotos.js` las pide de a tandas recien cuando la pagina esta quieta (refresco de la
playlist respondido), sin entrar en la cola de las Flechas. Un artista que Deezer no tiene se
recuerda un dia. La inicial queda si no hay foto o no carga.
El avatar del Perfil lleva la foto de perfil de Spotify del User.

**Rueda**: los paneles con scroll corren 70% de lo que pide la rueda y frenan rapido, con
menos deslizamiento que el del navegador. Solo los paneles que desbordan: un panel que entra
entero y todo lo que no es panel usan la rueda del navegador tal cual.

## Tipografia

| Rol | Fuente | Uso |
| --- | --- | --- |
| Display | Archivo (ancho variable, peso 400-900) | Titulos, frases, numeros grandes. Angosta (`font-stretch` 70-85%) y pesada (800-900) |
| Texto | Figtree 400-700 | Todo lo demas |
| Mono | DM Mono 400-500 | Client ID, redirect URI |

Escala: 11 (eyebrow) / 12.5 (small) / 14 (texto) / 15 (h3 de caja) / 20 (h2 de columna) / 34
(h1 de Cabecera) / 40-88 (frases de login).

## Forma

- Radios: `--r-s` 6px (inputs, portadas), `--r-m` 10px (columnas, cajas), `--r-l` 14px
  (paneles). Botones, fichas y Selectores: pildora.
- Capas: pagina negra > Panel `--superficie` > Columna negra > Caja `--superficie`. Sin bordes;
  borde solo en Boton secundario y en seleccion (`inset` verde).

## Nombres de elementos

### Primitivos

| Nombre | Que es |
| --- | --- |
| Boton primario | Pildora verde, texto negro. Una por vista |
| Boton secundario | Pildora con contorno gris ("Ajustes", "Sumar") |
| Boton fantasma | Solo texto gris |
| Boton peligro | Texto rojo sin contorno ("Borrar Canal y playlist") |
| Interruptor | Switch on/off (verde prendido) |
| Selector | Fila de pildoras donde una queda blanca |
| Aviso | Caja de texto con fondo: neutra, ok, warn, crit |
| Fila de artista | Avatar con foto, nombre y Flecha de artista, a lo ancho de la columna. Con Flecha, toda la fila es el boton |
| Flecha de artista | A la derecha en la Columna izquierda (suma a la Lista manual), a la izquierda en la Columna derecha (saca de la Lista manual) |
| Portada | Cuadrado con la portada de Spotify, o degradado e inicial si no hay |
| Barras | Grafico de barras horizontal: nombre, barra verde, numero |
| Caja | Bloque `--superficie` dentro de una Columna, con titulo h3 |
| Medidor de tope | Barra sobre 10.000 canciones; solo desde el 90% |

### Login

| Nombre | Que es |
| --- | --- |
| Pantalla de login | Unica puerta de entrada, igual para todos |
| Marca | Punto verde + "Radar de Viernes" |
| Frase de login | Titular grande con "tus artistas" subrayado y "viernes" en verde |
| Boton Conectar | "Conectar con Spotify". El servidor decide que pasa despues: su Own App si la reconoce (sesion o cookie del navegador), si no la Shared App. En la Pantalla de alta envia el Formulario de claves |
| Aviso de login | Aviso crit en la Pantalla de login: conexion cancelada, vencida o Spotify caido |
| Pantalla de alta | Si la Shared App no lo deja entrar o su Own App murio: Pasos de Own App arriba, Aviso de habilitacion abajo ("Sin Premium") |
| Frase de alta | "Todavia no estas en la lista..." |
| Aviso de habilitacion | Pantalla de alta: pedirle a Luca que habilite tu email de Spotify |
| Pasos de Own App | Lista numerada de 5 pasos |
| Caja de redirect | Redirect URI con boton Copiar |
| Formulario de claves | Client ID + Client secret |
| Aviso Premium | Aviso ambar sobre Premium |
| Aviso de claves | Aviso crit en la Pantalla de alta: claves vacias o incorrectas, Own App muerta, email fuera de su User Management |

### Estructura

| Nombre | Que es |
| --- | --- |
| Barra lateral | Panel izquierdo fijo |
| Lista de Canales | Canales del User con el +; scrollea sola |
| Fila de Canal | Portada, nombre y cantidad de artistas |
| Boton Nuevo Canal | El + de la Lista de Canales |
| Perfil | Abajo de la Barra lateral: avatar con la foto del User, nombre, Salir |
| Aviso de desconexion | Aviso crit arriba del Panel si el User esta Desconectado, con "Volver a conectar" |
| Aviso de reconexion | Aviso neutro arriba del Panel, una sola vez despues de reconectar: desde cuando estuvo Desconectado |
| Aviso de accion | Aviso ok arriba del Panel, una sola vez, despues de una accion (ej: Canal borrado) o si Spotify fallo |
| Aviso de tope | Aviso arriba del Panel cuando una Target Playlist pasa el 90% (warn) o el 95% (crit) de su tope: 9.500 en la del bot, 10.000 en una del User |
| Panel | Area derecha: Cabecera + Columnas |
| Cabecera de Canal | Portada, nombre del Canal, Boton Ajustes y Boton Buscar novedades, con el tono del Canal |
| Boton Buscar novedades | Boton secundario en la Cabecera de Canal: corre el Run de todos los Canales en segundo plano y vuelve a la misma vista con un Aviso de accion. No aparece con el User desconectado |
| Boton Ajustes | Abre la Vista Ajustes en lugar de las dos columnas; desde ahi vuelve a la Vista Canal |
| Columna | Cada una de las dos columnas del Panel, en Vista Canal y en Vista Ajustes; scrollea sola |

### Vista Canal

| Nombre | Que es |
| --- | --- |
| Columna izquierda | Las playlists del User y sus artistas. Apagada y sin respuesta si el Canal usa Seed Playlists |
| Fila de playlist | Asa de playlist, portada, nombre, cantidad de canciones y Boton ver artistas. Orden: el que el User armo arrastrando; si no toco nada, al reves de Spotify (la mas vieja arriba) |
| Asa de playlist | Tres barras a la izquierda de la portada: arrastrarla cambia la fila de lugar. El orden se guarda por User, vale en todos sus Canales y no toca Spotify. No aparece si el Canal usa Seed Playlists ni en la Fila no disponible |
| Fila no disponible | Fila gris al fondo de la lista, dice "No disponible" y no se despliega: playlists ajenas no colaborativas y la Target de este Canal |
| Boton ver artistas | Flecha que despliega la fila; una sola abierta a la vez |
| Indicador de lectura | Arriba de la Columna izquierda, fijo, mientras hay una fila abierta: "Buscando...", "Al dia", "Sin actualizar" o "Leida en parte" (Spotify no llego a mandar toda la playlist a tiempo: se ven los artistas leidos hasta ahi y la proxima apertura sigue desde ahi) |
| Artistas de la playlist | Filas de artista en orden alfabetico, invitados incluidos, sin los que ya estan en la Columna derecha. Si no queda ninguno: "Ya estan todos". Si nunca se pudo leer: "No pudimos leer esta playlist. Proba mas tarde." |
| Bloque de nuevos | Al fondo de la fila abierta, apartado: "2 artistas nuevos" y sus Filas de artista en orden alfabetico. Dura hasta plegar la playlist; al volver a abrirla se mezclan con el resto |
| Columna derecha | "Artistas seleccionados": la Whitelist del Canal en Filas de artista, en orden alfabetico. Vacia: "Elegi artistas desde una playlist o buscalos." |
| Boton Clear all | En el encabezado de la Columna derecha, a la derecha del contador, en blanco: vacia la Lista manual del Canal previa confirmacion. No aparece si el Canal usa Seed Playlists ni con la Whitelist vacia |
| Buscador de artistas | Arriba de la Columna derecha; suma a la Lista manual. No aparece si el Canal usa Seed Playlists. Busca desde 2 letras, 200 ms despues de dejar de tipear; al vaciarlo se limpian los resultados |
| Aviso de busqueda | Aviso warn en lugar de los resultados del Buscador si Spotify pidio una pausa o se quedo sin cuota; el resto de la vista sigue igual |
| Resultado agregado | Resultado del Buscador de un artista que ya esta en el Canal: apagado, sin boton y con la etiqueta "added" |
| Aviso de Seed | En la Columna derecha si el Canal usa Seed Playlists, en lugar del Buscador: las filas no tienen flecha y el aviso lleva a Ajustes |

### Vista Ajustes

| Nombre | Que es |
| --- | --- |
| Seed Playlists | Las playlists usables del User con un Interruptor de playlist cada una; al lado del titulo, el Boton info de Seed. Con una prendida el Canal usa Seed; con ninguna, su Lista manual. Se pinta con la biblioteca guardada y se pone al dia sola por detras, sin perder el scroll, solo si aparecio, se fue o cambio una playlist; si Spotify falla queda como estaba |
| Interruptor de playlist | Prende o apaga la playlist como Seed Playlist de este Canal |
| Boton info de Seed | "i" junto al titulo Seed Playlists: abre un globo que explica que al activarse el Canal reemplaza la Lista manual por los artistas principales de esas playlists |
| Interruptor Feats | Junto a cada Seed Playlist prendida: "Agregar featurings a la lista" |
| Columna de reglas | Columna derecha de la Vista Ajustes, "General": Caja Playlist, Editor de Retencion y Caja Desvincular |
| Caja Playlist | La Target Playlist del Canal (vive y muere con ella, ADR 0005) con el Formulario de playlist |
| Formulario de playlist | Portada con el Nombre a su derecha, Descripcion abajo, con "Guardar cambios": los cambia en Spotify y manda solo lo que cambio. No aparece si la playlist se perdio |
| Aviso de playlist perdida | Aviso warn en la Caja Playlist si la Target ya no esta en la biblioteca del User |
| Editor de Retencion | Caja "Tiempo limite": Selector de Retencion + Semanas + "Se mantienen en el Canal las canciones durante N semanas" o "por tiempo indefinido" |
| Semanas | 12 barritas que dibujan cuanto guarda |
| Caja Desvincular | Ultima caja de la Columna de reglas, con borde rojo: Borrar Canal / Borrar Canal y playlist, cada uno con confirmacion |

### Vista Nuevo Canal

| Nombre | Que es |
| --- | --- |
| Cabecera de Nuevo Canal | "Donde va lo nuevo" |
| Editor de Playlist | Caja: Selector de Target + nombre o lista de playlists propias |
| Selector de Target | "Nueva" / "Una mia" |
| Nombre de playlist | Input del nombre (playlist nueva, se crea privada) |
| Selector de playlist existente | Lista de playlists propias; las que ya son Target dicen "en uso" |
| Aviso de Nuevo Canal | Aviso crit: tope de 5 Canales, nombre vacio, playlist ajena |

### Vista sin Canales

Solo la ve un User sin Canales; con alguno, `/` abre el primero. Si `/` llega por una falla (Spotify caido, desconexion) muestra solo la Cabecera, el Aviso y "Elegi un Canal de la izquierda", para no rebotar al Canal que fallo.

| Nombre | Que es |
| --- | --- |
| Cabecera de bienvenida | "Hola, Luca" |
| Pasos de Canal | 3 tarjetas numeradas: elegir la playlist, elegir los artistas, listo cada viernes. En celular se apilan |
| Boton Crear Canal | Boton primario: lleva a la Vista Nuevo Canal |

### Del prototipo (no son producto)

Barra del prototipo (abajo, clara), capa de Nombres (lima), Estado (JSON).
