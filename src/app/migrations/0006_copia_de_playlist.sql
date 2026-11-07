-- ADR 0008: Copia de playlist por User; position guarda el orden de Spotify para pintar sin llamarlo
CREATE TABLE playlist_copies (
    user_id bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    playlist_id text NOT NULL,
    position integer NOT NULL,
    name text NOT NULL,
    owner_id text NOT NULL,
    collaborative boolean NOT NULL DEFAULT false,
    snapshot_id text,
    total integer NOT NULL DEFAULT 0,
    -- null hasta abrirla; total lo pisa la biblioteca, artists_total es lo que habia al leer los artistas
    artists_snapshot_id text,
    artists_total integer,
    last_track_uri text,
    PRIMARY KEY (user_id, playlist_id)
);

CREATE TABLE playlist_copy_artists (
    user_id bigint NOT NULL,
    playlist_id text NOT NULL,
    artist_id text NOT NULL,
    name text NOT NULL,
    feat boolean NOT NULL DEFAULT false,
    isrc text,
    PRIMARY KEY (user_id, playlist_id, artist_id),
    FOREIGN KEY (user_id, playlist_id) REFERENCES playlist_copies (user_id, playlist_id) ON DELETE CASCADE
);

-- todo Canal pasa a Lista manual con su Whitelist de hoy; whitelist_artists no se toca para conservar added_at
CREATE TEMP TABLE migrated_manual ON COMMIT DROP AS
SELECT w.canal_id, w.artist_id, coalesce(max(nullif(sa.name, '')), '') AS name, max(sa.isrc) AS isrc
FROM whitelist_artists w
LEFT JOIN (source_artists sa JOIN artist_sources s ON s.id = sa.artist_source_id)
    ON s.canal_id = w.canal_id AND sa.artist_id = w.artist_id
GROUP BY w.canal_id, w.artist_id;

DELETE FROM artist_sources WHERE type IN ('seed_playlist', 'followed');
DELETE FROM source_artists;

INSERT INTO artist_sources (canal_id, type)
SELECT id, 'manual' FROM canales
ON CONFLICT DO NOTHING;

INSERT INTO source_artists (artist_source_id, artist_id, name, isrc)
SELECT s.id, m.artist_id, m.name, m.isrc
FROM migrated_manual m JOIN artist_sources s ON s.canal_id = m.canal_id AND s.type = 'manual';

ALTER TABLE artist_sources DROP CONSTRAINT artist_sources_type_check;
ALTER TABLE artist_sources ADD CONSTRAINT artist_sources_type_check CHECK (type IN ('seed_playlist', 'manual'));
