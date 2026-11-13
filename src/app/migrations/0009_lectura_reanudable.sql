-- #24: avance de una lectura cortada por el tope de la web; el proximo refresco sigue desde ahi si la playlist no cambio
-- partial_total son los items leidos desde el principio y partial_artists sus artistas (artist_id -> {name, feat, isrc})
ALTER TABLE playlist_copies
    ADD COLUMN partial_snapshot_id text,
    ADD COLUMN partial_total integer,
    ADD COLUMN partial_artists jsonb;
