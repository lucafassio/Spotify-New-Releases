---
status: accepted
---

# Exploracion diaria, Entrega semanal los viernes

El Run corre todos los dias (ADR 0002) pero la Target Playlist se toca una sola vez por
semana. Cada Run diario explora y suma los Tracks nuevos al Lote abierto del Canal como
pendientes; el viernes, despues de explorar, el Lote se cierra y la **Entrega** lo filtra con
la config tal como esta en ese momento, lo inserta, retira lo que la Retencion deja afuera y
recorta al tope. Queremos el ritmo de "playlist nueva el viernes" sin perder lo que da el Run
diario: una caida de Actions cuesta un dia de exploracion y no una semana, y lo que sale fuera
del viernes se detecta el mismo dia.

## Considered Options

- **Insertar en cada Run diario** (lo decidido en #5): la playlist cambia todos los dias y la
  Retencion por semanas no tiene un corte natural.
- **Explorar solo el viernes**: un Run por semana, pero una caida de Actions ese dia pierde la
  semana entera y no hay margen para reintentar la exploracion.

## Consequences

- El Lote de un Canal va de sabado a viernes y se cierra el viernes despues de explorar. Lo
  que se descubre despues va al Lote siguiente, aunque la Entrega se este reintentando.
- La Entrega se reintenta en los Runs siguientes hasta salir completa. Si llega el viernes
  siguiente sin entregarse, los dos Lotes salen juntos y cuentan como una sola Entrega para la
  Retencion.
- La config (Whitelist, filtro de Guardado, Retencion) se lee en el momento de cada intento de
  Entrega, no durante la semana. Lo que ya quedo insertado en un intento anterior se queda.
- El chequeo de Guardado se hace solo en la Entrega, no en cada Run.
- Retiro: despues de una insercion completa, en DELETEs de hasta 100 URIs, marcando cada Track
  al recibir 200. `DELETE /playlists/{id}/items` quita todas las copias de un URI, asi que no
  se retira un URI que figure en un Lote todavia dentro de la Retencion.
- Tope: en una Target Playlist creada por el bot, despues de la Entrega se retiran sus Lotes
  mas viejos hasta quedar en 9500 items. En una existente no se retira nada; si no entra todo,
  se inserta hasta 10.000 y el resto se descarta sin reintentar. La UI avisa al 90% y al 95%.
  El tope de 10.000 viene de la comunidad de Spotify, no de la documentacion.
- Si en la Entrega la Target Playlist ya no existe (`GET /me/library/contains` con su URI), el
  Canal se borra y su Lote se pierde.
- Enmienda el guard de ADR 0002: el Tick corre un Run si el User no exploro hoy o tiene
  alguna Entrega o retiro pendiente. Un Run es exitoso solo si todos sus Canales cerraron lo
  que les tocaba.
