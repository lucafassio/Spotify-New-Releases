-- #14: los artistas de cada Artist Source quedan en la DB y la Whitelist se recalcula sin llamar a Spotify
-- la lista manual ya era esto; ahora tambien guarda lo que aportan Seed Playlists y follows
ALTER TABLE manual_source_artists RENAME TO source_artists;
ALTER TABLE source_artists ADD COLUMN name text NOT NULL DEFAULT '';
-- un artista que es principal en algun track de la fuente nunca queda como feat
ALTER TABLE source_artists ADD COLUMN feat boolean NOT NULL DEFAULT false;

ALTER TABLE artist_sources ADD COLUMN include_feats boolean NOT NULL DEFAULT false;
-- una sola lista manual y una sola fuente de follows por Canal, y cada Seed Playlist una vez
CREATE UNIQUE INDEX artist_sources_unique ON artist_sources (canal_id, type, coalesce(playlist_id, ''));

-- nombre de la Target Playlist para pintar la Barra lateral sin llamar a Spotify; se refresca al leer playlists
ALTER TABLE canales ADD COLUMN target_name text NOT NULL DEFAULT '';
