# Style guide

Guia viva de la UI. Dos objetivos: que la UI se vea coherente y que Luca y el agente llamen a
cada elemento por el mismo nombre. Se actualiza en el mismo commit que cambia la UI.

Como usar los nombres: en el prototipo, el boton **Nombres** de la barra de abajo (o la tecla
`N`) marca cada elemento. Al pasar el mouse muestra el nombre y la ruta completa, por ejemplo
`Panel > Columna Fuentes > Fila de Seed Playlist`. Un click copia el nombre. Para pedir un
cambio alcanza con "en la Fila de Seed Playlist, ...". Si un nombre choca con el glosario de
`CONTEXT.md`, gana el glosario y se renombra aca.

Estado: base definida (#10). Layout del Panel en revision en #7 (ronda 4: dashboard fijo).

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
| `--verde-suave` | 13% de verde | Fondo de Ficha de artista incluida |
| `--verde-tinta` | `#04210F` | Texto secundario sobre verde |
| `--scroll` / `--scroll-hover` | `#333333` / `#4A4A4A` | Barra de scroll de los paneles |
| `--lima` | `#C6F432` | Solo herramientas del prototipo (capa de Nombres) |
| `--ambar` | `#F5B642` | Aviso: tope al 90%, Premium, 0 artistas seguidos |
| `--rojo` | `#FF6B6B` | Error, tope al 95%, Desvincular |

**Portadas generadas**: cada Canal y cada playlist tiene un tono (`--h`, 0-360) que arma un
degradado con la inicial grande en negro translucido. El tono del Canal tine su Cabecera.

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
| Boton secundario | Pildora con contorno gris ("Sumar", "Seguir") |
| Boton fantasma | Solo texto gris |
| Boton Desvincular | Texto rojo, abre la Confirmacion de desvincular |
| Interruptor | Switch on/off (verde prendido) |
| Selector | Fila de pildoras donde una queda blanca |
| Aviso | Caja de texto con fondo: neutra, ok, warn, crit |
| Chip | Pildora con avatar y x para quitar (artistas a mano) |
| Ficha de artista | Pildora con avatar; verde si esta incluido, tachada si se saco |
| Portada | Cuadrado con degradado e inicial |
| Barras | Grafico de barras horizontal: nombre, barra verde, numero |
| Caja | Bloque `--superficie` dentro de una Columna, con titulo h3 |
| Medidor de tope | Barra sobre 10.000 canciones; solo desde el 90% |

### Login

| Nombre | Que es |
| --- | --- |
| Pantalla de login | Unica puerta de entrada, igual para todos |
| Marca | Punto verde + "Radar de Viernes" |
| Frase de login | Titular grande con "tus artistas" subrayado y "viernes" en verde |
| Boton Conectar | "Conectar con Spotify". El servidor decide que pasa despues |
| Pantalla de alta | Solo si el User no esta en la lista de registrados |
| Frase de alta | "Todavia no estas en la lista..." |
| Pasos de Own App | Lista numerada de 5 pasos |
| Caja de redirect | Redirect URI con boton Copiar |
| Formulario de claves | Client ID + Client secret |
| Aviso Premium | Aviso ambar sobre Premium |

### Estructura

| Nombre | Que es |
| --- | --- |
| Barra lateral | Panel izquierdo fijo |
| Boton Inicio | Primera fila: "Tu biblioteca" |
| Lista de Canales | Canales del User con el +; scrollea sola |
| Fila de Canal | Portada, nombre y cantidad de artistas |
| Boton Nuevo Canal | El + de la Lista de Canales |
| Perfil | Abajo de la Barra lateral: avatar, nombre, Salir |
| Panel | Area derecha: Cabecera + Columnas |
| Cabecera de Canal | Portada, nombre del Canal y Desvincular, con el tono del Canal |
| Confirmacion de desvincular | Globo oscuro-rojo bajo la Cabecera con las dos formas de borrar |
| Columna | Cada una de las tres columnas del Panel; scrollea sola |

### Vista Canal

| Nombre | Que es |
| --- | --- |
| Columna Fuentes | "De quien": todo lo que arma la Whitelist |
| Fuente Follows | Caja con el Interruptor de artistas seguidos |
| Lista de Seed Playlists | Todas las playlists del User, una fila cada una |
| Fila de Seed Playlist | Interruptor, portada, nombre, cantidad de artistas y flecha |
| Interruptor de playlist | Prende o apaga la playlist como fuente del Canal |
| Boton ver artistas | Flecha que despliega la fila |
| Artistas de la playlist | Fichas de artista desplegadas; tocar una la saca del Canal |
| Interruptor Feats | Dentro de la fila desplegada: sumar invitados |
| Artistas a mano | Caja con Chips + Buscador de artistas |
| Columna Reglas | "Donde y como" |
| Editor de Playlist | Caja: Selector de Target + nombre o lista de playlists propias |
| Selector de Target | "Nueva" / "Una mia" |
| Nombre de playlist | Input del nombre (playlist nueva) |
| Selector de playlist existente | Lista con scroll de playlists propias |
| Editor de Retencion | Caja "Cuanto guarda": Selector de Retencion + Semanas |
| Semanas | 12 barritas que dibujan cuanto guarda |
| Interruptor Guardado | Caja "Saltear lo que ya tengo" |
| Resumen de Canal | Caja "En una frase": el Canal contado en una oracion |
| Columna Pistas | Datos de la biblioteca que ayudan a decidir |
| Quienes mas aparecen | Barras de los artistas del Canal por apariciones en tus playlists |
| Sugerencias | "Te faltan en este Canal": artistas de otras playlists tuyas con boton Sumar |
| Cruce con otros Canales | Artistas en comun con cada otro Canal |

### Vista Inicio (Tu biblioteca)

| Nombre | Que es |
| --- | --- |
| Cabecera de Inicio | "Hola, Luca" |
| Numeros de biblioteca | 4 numeros: playlists, artistas, seguidos, Canales |
| Segui a tus mas escuchados | Top 8 artistas con Seguir y "Seguir a los 8" |
| Top de biblioteca | Barras de los 14 artistas con mas temas; gris si no estan en ningun Canal |
| Tarjeta de gemelas | Dos playlists que se pisan, con "Unir en una" y "Dejarlas asi" |

### Del prototipo (no son producto)

Barra del prototipo (abajo, clara), capa de Nombres (lima), Estado (JSON).
