-- descripcion de la playlist, para editarla desde la Caja Playlist; la escribe save_library
ALTER TABLE playlist_copies ADD COLUMN description text;

-- Saltear lo que ya tengo deja de ser un ajuste: todos los Canales filtran sus Guardados
UPDATE canales SET guardado_filter = true WHERE NOT guardado_filter;
