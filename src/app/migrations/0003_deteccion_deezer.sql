-- ADR 0007: la deteccion pasa a Deezer, que se lee completo cada dia, asi que el Checkpoint de #5 ya no sirve
DROP TABLE checkpoints;

-- vinculo global artista de Spotify -> Deezer; deezer_artist_id null es que no lo encontramos
CREATE TABLE deezer_artists (
    spotify_artist_id text PRIMARY KEY,
    deezer_artist_id bigint,
    matched_by text CHECK (matched_by IN ('isrc', 'name')),
    checked_at timestamptz NOT NULL DEFAULT now()
);

-- album de Deezer -> album de Spotify por UPC; spotify_album_id null es que Spotify todavia no lo tiene
CREATE TABLE album_links (
    deezer_album_id bigint PRIMARY KEY,
    spotify_album_id text,
    checked_at timestamptz NOT NULL DEFAULT now()
);

-- un Track de la fuente donde figura el artista, para vincularlo con Deezer por ISRC
ALTER TABLE source_artists ADD COLUMN isrc text;

-- releemos una Seed Playlist solo si cambio su snapshot
ALTER TABLE artist_sources ADD COLUMN snapshot_id text;
