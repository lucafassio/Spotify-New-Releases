---
status: accepted
---

# Insercion en la Target Playlist: un POST por Track, en orden inverso, en `position: 0`

> **Enmienda 2026-09-23 (ticket #6):** esto aplica a Target Playlists creadas por el bot. En
> una Target Playlist existente del User se hace append (sin `position`), un POST por Track en
> orden directo del Lote: manda el orden personalizado, donde el User ya espera lo nuevo al
> fondo. En "agregados recientemente" descendente cada album se ve invertido; se acepta. La
> insercion corre en la Entrega semanal (ADR 0004), no en cada Run.

Queremos que lo nuevo quede arriba de la Target Playlist y que un album conserve su orden
original tanto en el orden personalizado de Spotify como en "agregados recientemente", sin
depender de como ordene el User. `added_at` tiene resolucion de segundos y todos los URIs de
un mismo `POST /playlists/{id}/items` comparten el mismo valor, asi que un POST por lote deja
el desempate en manos de Spotify. Decidimos recorrer el Lote **del ultimo Track al primero**
y hacer **un POST por Track** con `position: 0`, esperando 1 s entre POSTs: cada Track pisa
arriba (orden personalizado = orden del Lote) y `added_at` crece de atras hacia adelante
("agregados recientemente" = orden del Lote).

## Considered Options

- **Un POST por lote de hasta 100 URIs** (`position: i*100`): 1-2 requests por Run, pero
  solo garantiza el orden personalizado.
- **Un POST por Release**: garantiza el orden entre albums en "agregados recientemente";
  dentro del album depende de un desempate no documentado.

## Consequences

- Costo por Run: N requests + N segundos en vez de N/100. Con 30-80 Tracks por semana y 5
  Users es ~1 min por User; el rate limit (ventana de 30 s, numero no publicado) no deberia
  molestar. Si la quota muerde, se baja a un POST por Release sin tocar el modelo.
- Orden del Lote: agrupado por `artists[0]` (nombre asc), Release en orden original
  (`disc_number`, `track_number`), Releases del mismo artista por `release_date` desc.
- "Agregados recientemente" invertido rompe la idea de "lo nuevo arriba". Es eleccion del
  User; no hay guard.
- La insercion Track a Track hace trivial retomar un Lote a medio insertar.
