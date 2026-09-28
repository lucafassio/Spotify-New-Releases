# Style guide

Guia viva de la UI. Dos objetivos: que la UI se vea coherente y que Luca y el agente llamen a
cada elemento por el mismo nombre. Se actualiza en el mismo commit que cambia la UI.

Como usar los nombres: en el prototipo, el boton **Nombres** de la barra de abajo (o la tecla
`N`) marca cada elemento. Al pasar el mouse muestra el nombre y la ruta completa, por ejemplo
`Panel > Editor de Artistas > Tarjeta de Seed Playlist`. Un click copia el nombre. Para pedir un
cambio alcanza con "en la Tarjeta de Seed Playlist, ...". Si un nombre choca con el glosario de
`CONTEXT.md`, gana el glosario y se renombra aca.

Estado: base definida (#10). Variante del Panel pendiente de eleccion en #7 (B1, B2 o B3).

## Principio

El Panel es para configurar, no para mirar. Se muestra solo lo que el User elige o necesita
saber para elegir. No van: canciones agregadas o por agregar, historial de Entregas, horarios,
Runs, ni explicaciones de como trabaja el bot por dentro. Un dato interno aparece solo si le
pide una accion al User (ej: Medidor de tope al 90%).

## Color

Tema unico oscuro, a proposito: la app vive al lado de Spotify y comparte su base negro + verde.
Verde solo para lo que es accion principal o estado "vivo" (lo que entra el viernes). Nada de
verde decorativo.

| Token | Hex | Uso |
| --- | --- | --- |
| `--negro` | `#000000` | Fondo de pagina, texto sobre verde |
| `--fondo` | `#0B0B0B` | Reservado |
| `--superficie` | `#141414` | Paneles: Barra lateral, Panel, Cajon de edicion |
| `--elevada` | `#1E1E1E` | Tarjetas, inputs, filas en hover |
| `--elevada-2` | `#2A2A2A` | Hover de tarjetas, fila seleccionada, pista de Interruptor apagado |
| `--linea` | `#2E2E2E` | Separadores |
| `--texto` | `#F5F5F2` | Texto principal |
| `--texto-2` | `#A7A9A4` | Texto secundario |
| `--texto-3` | `#6E716B` | Numeracion, pistas, placeholders |
| `--verde` | `#1ED760` | Boton primario, Interruptor encendido, seleccion, bloque destacado de cada variante |
| `--verde-hondo` | `#13A049` | Rayado de Retencion Acumulativa |
| `--verde-tinta` | `#04210F` | Texto secundario sobre verde |
| `--lima` | `#C6F432` | Solo herramientas del prototipo (capa de Nombres) |
| `--ambar` | `#F5B642` | Aviso: tope al 90%, Premium |
| `--rojo` | `#FF6B6B` | Error, tope al 95%, Desvincular |

Semanticos (`--ambar`, `--rojo`, y `--verde` en Etiqueta ok) van siempre con su version
`-suave` (14% de opacidad) de fondo.

**Portadas generadas**: cada Canal y cada playlist tiene un tono (`--h`, 0-360) que arma un
degradado de dos colores con la inicial grande en negro translucido. El tono del Canal tambien
tine su Cabecera en B1.

## Tipografia

| Rol | Fuente | Uso |
| --- | --- | --- |
| Display | Archivo (ancho variable 62-125, peso 400-900) | Titulos, Frase de login, numeros grandes. Angosta (`font-stretch` 68-85%) y pesada (800-900) |
| Texto | Figtree 400-700 | Todo lo demas |
| Mono | DM Mono 400-500 | Client ID, redirect URI |

Escala: 11 (eyebrow, mayusculas +0.12em) / 13 (small) / 15 (texto) / 22-28 (h2) / 40-88 (h1 y
frases, con `clamp`).

## Forma

- Radios: `--r-s` 6px (inputs, portadas chicas), `--r-m` 10px (paneles, avisos), `--r-l` 16px
  (tarjetas del Tablero). Botones, Etiquetas, Chips y Selectores: pildora (99px).
- Sin bordes en tarjetas: se separan por cambio de superficie (`--superficie` > `--elevada` >
  `--elevada-2`). Borde solo en Boton secundario y en seleccion (`inset` verde).

## Nombres de elementos

### Primitivos

| Nombre | Que es |
| --- | --- |
| Boton primario | Pildora verde, texto negro. Una por pantalla |
| Boton secundario | Pildora con contorno gris |
| Boton fantasma | Solo texto gris, sin contorno |
| Boton Desvincular | Texto rojo, abre la Confirmacion de desvincular |
| Interruptor | Switch on/off (verde cuando esta prendido) |
| Selector | Fila de pildoras donde una queda blanca (Selector de Target, Selector de Retencion) |
| Etiqueta | Pildora chica de estado: ok (verde), aviso (ambar), error (rojo), neutra (gris) |
| Aviso | Caja de texto con fondo: neutra, ok, warn, crit |
| Chip | Pildora con avatar y x para quitar |
| Portada | Cuadrado con degradado e inicial |
| Medidor de tope | Barra de canciones sobre 10.000; solo aparece desde el 90% |

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

### Panel (comun a B1, B2, B3)

| Nombre | Que es |
| --- | --- |
| Barra lateral | Columna izquierda (arriba en celular) |
| Perfil | Avatar, nombre y Salir |
| Lista de Canales | Tarjeta con los Canales y el + |
| Fila de Canal | Portada chica, nombre y cantidad de artistas |
| Boton Nuevo Canal | El + de la Lista de Canales |
| Panel | Area derecha con el Canal elegido |
| Cabecera de Canal | Portada + nombre del Canal + resumen (artistas, que guarda) |
| Confirmacion de desvincular | Caja roja con las dos formas de borrar |

### Editores

| Nombre | Que es |
| --- | --- |
| Editor de Artistas | Todo lo que arma la Whitelist |
| Fuente Follows | Interruptor de artistas seguidos |
| Seed Playlists | Grilla de playlists del User |
| Tarjeta de Seed Playlist | Una playlist de la grilla; tilde verde si esta elegida, apagada si es ajena |
| Interruptor Feats | Por cada Seed Playlist elegida, sumar feats |
| Artistas a mano | Chips + Buscador |
| Ficha de artista | Chip de un artista a mano |
| Buscador de artistas | Input para sumar artistas a mano |
| Total de Whitelist | Aviso con la cantidad final de artistas |
| Editor de Playlist | Target Playlist del Canal |
| Selector de Target | "Una nueva del bot" / "Una que ya tengo" |
| Nombre de playlist | Input del nombre (solo playlist del bot) |
| Selector de playlist existente | Lista de playlists propias elegibles |
| Editor de Retencion | Cuantas semanas guarda |
| Selector de Retencion | 1 / 2 / 4 / 8 / 12 semanas / Todo |
| Interruptor Guardado | Saltear lo ya guardado |

### Propios de cada variante

| Variante | Nombre | Que es |
| --- | --- | --- |
| B1 Biblioteca | Pestanas | Artistas / Playlist / Que guarda |
| B2 Tablero | Tarjeta de Artistas | Bloque verde grande con total, Seed Playlists y artistas a mano |
| B2 Tablero | Tarjeta de Playlist, de Retencion | Bloques oscuros; abren el Cajon |
| B2 Tablero | Tarjeta de Guardados | Bloque ancho con el Interruptor Guardado, se edita ahi mismo |
| B2 Tablero | Semanas | 12 barritas que muestran la Retencion |
| B2 Tablero | Cajon de edicion | Panel que entra desde la derecha con un Editor |
| B3 Ajustes | Franja de Canal | Banda verde con portada, nombre y resumen del Canal |
| B3 Ajustes | Lista de ajustes | Filas que se despliegan |
| B3 Ajustes | Fila de ajuste | Nombre, valor actual y flecha |

### Del prototipo (no son producto)

Barra del prototipo (abajo, clara), capa de Nombres (lima), Estado (JSON).
