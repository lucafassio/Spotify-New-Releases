-- portada de Spotify para pintar la Portada; null es que la playlist no tiene y queda el degradado
ALTER TABLE playlist_copies ADD COLUMN image_url text;
