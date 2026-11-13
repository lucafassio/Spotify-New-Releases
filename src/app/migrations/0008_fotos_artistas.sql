-- #23: foto de cada artista con la url final del CDN de Deezer, asi el HTML no pasa por nuestro servidor
-- url null es que Deezer no tiene foto: no se vuelve a buscar hasta un dia despues de checked_at
CREATE TABLE artist_photos (
    spotify_artist_id text PRIMARY KEY,
    url text,
    checked_at timestamptz NOT NULL DEFAULT now()
);
