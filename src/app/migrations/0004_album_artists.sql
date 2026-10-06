-- artistas de un album de Spotify (principales y de cada track): si ninguno esta en la Whitelist no volvemos a pedir el album (ADR 0007)
ALTER TABLE album_links ADD COLUMN artist_ids text[];
