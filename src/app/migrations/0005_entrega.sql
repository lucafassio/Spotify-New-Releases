-- #16: artistas de cada Track del Lote (principales del disco y del track) para filtrar con la Whitelist del momento de la Entrega
-- null en filas viejas: no se filtran
ALTER TABLE lote_items ADD COLUMN artist_ids text[];

-- canciones de la Target Playlist para los avisos de tope sin llamar a Spotify; se refresca al leer playlists y al entregar
ALTER TABLE canales ADD COLUMN target_total integer NOT NULL DEFAULT 0;
