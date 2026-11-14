-- orden que el User le dio a sus playlists arrastrandolas; vale para todos sus Canales y no toca Spotify
ALTER TABLE playlist_copies ADD COLUMN manual_position integer;
