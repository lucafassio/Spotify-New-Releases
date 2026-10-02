-- schema inicial de ADR 0006
-- sin nombres de schema a proposito: los tests aplican esto sobre un schema efimero via search_path

CREATE TABLE users (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    spotify_user_id text NOT NULL UNIQUE,
    connection_mode text NOT NULL CHECK (connection_mode IN ('shared', 'own')),
    client_id text,
    client_secret_enc text,
    -- null mientras el User esta Desconectado (enmienda #19)
    refresh_token_enc text,
    disconnected_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE canales (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    target_playlist_id text NOT NULL,
    target_created_by_bot boolean NOT NULL,
    -- null es Retencion Acumulativa
    retencion_n integer CHECK (retencion_n > 0),
    guardado_filter boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, target_playlist_id)
);

CREATE TABLE artist_sources (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canal_id bigint NOT NULL REFERENCES canales (id) ON DELETE CASCADE,
    type text NOT NULL CHECK (type IN ('seed_playlist', 'manual', 'followed')),
    playlist_id text,
    CHECK ((type = 'seed_playlist') = (playlist_id IS NOT NULL))
);

CREATE TABLE manual_source_artists (
    artist_source_id bigint NOT NULL REFERENCES artist_sources (id) ON DELETE CASCADE,
    artist_id text NOT NULL,
    PRIMARY KEY (artist_source_id, artist_id)
);

CREATE TABLE whitelist_artists (
    canal_id bigint NOT NULL REFERENCES canales (id) ON DELETE CASCADE,
    artist_id text NOT NULL,
    added_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (canal_id, artist_id)
);

-- por User y no por Canal: queda huerfano si el artista sale de todos los Canales (ADR 0006)
CREATE TABLE checkpoints (
    user_id bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    artist_id text NOT NULL,
    album_group text NOT NULL CHECK (album_group IN ('album', 'single', 'appears_on')),
    total integer NOT NULL,
    first_page_ids text[] NOT NULL,
    PRIMARY KEY (user_id, artist_id, album_group)
);

CREATE TABLE lotes (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canal_id bigint NOT NULL REFERENCES canales (id) ON DELETE CASCADE,
    week_start date NOT NULL,
    status text NOT NULL DEFAULT 'abierto' CHECK (status IN ('abierto', 'cerrado')),
    closed_at timestamptz,
    delivered_at timestamptz,
    attempt_count integer NOT NULL DEFAULT 0,
    UNIQUE (canal_id, week_start)
);

CREATE TABLE lote_items (
    lote_id bigint NOT NULL REFERENCES lotes (id) ON DELETE CASCADE,
    track_uri text NOT NULL,
    album_id text NOT NULL,
    artist_name text NOT NULL,
    disc_number integer NOT NULL,
    track_number integer NOT NULL,
    release_date date NOT NULL,
    status text NOT NULL DEFAULT 'pendiente' CHECK (status IN ('pendiente', 'insertado', 'retirado', 'descartado')),
    PRIMARY KEY (lote_id, track_uri)
);

-- source es 'liked' o el id de una playlist propia; liked no tiene snapshot_id
CREATE TABLE saved_sources (
    user_id bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    source text NOT NULL,
    snapshot_id text,
    PRIMARY KEY (user_id, source)
);

CREATE TABLE saved_tracks (
    user_id bigint NOT NULL,
    source text NOT NULL,
    track_id text NOT NULL,
    PRIMARY KEY (user_id, source, track_id),
    FOREIGN KEY (user_id, source) REFERENCES saved_sources (user_id, source) ON DELETE CASCADE
);

-- una fila por (User, fecha ART), un reintento el mismo dia hace upsert (ADR 0006)
CREATE TABLE runs (
    user_id bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    date date NOT NULL,
    status text NOT NULL CHECK (status IN ('en_curso', 'exitoso', 'fallido')),
    PRIMARY KEY (user_id, date)
);
