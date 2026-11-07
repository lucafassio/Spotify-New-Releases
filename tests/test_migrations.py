import uuid

import psycopg
import pytest
from psycopg import sql

from app.db import connect
from app.migrate import MIGRATIONS_LOCK, apply_migrations, list_migrations

# manual_source_artists paso a source_artists en 0002 (#14); checkpoints se fue en 0003 (ADR 0007)
ADR_0006_TABLES = {
    'users',
    'canales',
    'artist_sources',
    'source_artists',
    'whitelist_artists',
    'lotes',
    'lote_items',
    'saved_sources',
    'saved_tracks',
    'runs',
    'deezer_artists',
    'album_links',
    'playlist_copies',
    'playlist_copy_artists',
}


def test_adr_0006_tables_exist(db):
    rows = db.execute(
        'SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()'
    ).fetchall()
    tables = {row[0] for row in rows}

    assert ADR_0006_TABLES <= tables


def test_migrations_are_idempotent(db):
    assert apply_migrations(db) == []


def test_migrations_release_their_lock(db):
    apply_migrations(db)

    # si el lock quedara tomado, la web y el Tick se colgarian esperando al proximo migrate
    with connect() as other:
        assert other.execute('SELECT pg_try_advisory_lock(%s)', (MIGRATIONS_LOCK,)).fetchone()[0]
        other.execute('SELECT pg_advisory_unlock(%s)', (MIGRATIONS_LOCK,))


def test_run_is_one_row_per_user_and_day(db):
    user_id = db.execute(
        "INSERT INTO users (spotify_user_id, connection_mode) VALUES ('u1', 'shared') RETURNING id"
    ).fetchone()[0]
    db.execute("INSERT INTO runs (user_id, date, status) VALUES (%s, '2026-10-02', 'fallido')", (user_id,))

    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute("INSERT INTO runs (user_id, date, status) VALUES (%s, '2026-10-02', 'exitoso')", (user_id,))


def test_deleting_canal_cascades_to_lotes(db):
    user_id = db.execute(
        "INSERT INTO users (spotify_user_id, connection_mode) VALUES ('u2', 'shared') RETURNING id"
    ).fetchone()[0]
    canal_id = db.execute(
        "INSERT INTO canales (user_id, target_playlist_id, target_created_by_bot) VALUES (%s, 'p1', true) RETURNING id",
        (user_id,),
    ).fetchone()[0]
    lote_id = db.execute(
        "INSERT INTO lotes (canal_id, week_start) VALUES (%s, '2026-09-26') RETURNING id", (canal_id,)
    ).fetchone()[0]
    db.execute(
        "INSERT INTO lote_items (lote_id, track_uri, album_id, artist_name, disc_number, track_number, release_date) VALUES (%s, 'spotify:track:t1', 'a1', 'Artista', 1, 1, '2026-10-02')",
        (lote_id,),
    )

    db.execute('DELETE FROM canales WHERE id = %s', (canal_id,))
    # otros tests commitean Lotes en el mismo schema: se cuenta solo el de este
    remaining = db.execute('SELECT count(*) FROM lote_items WHERE lote_id = %s', (lote_id,)).fetchone()[0]

    assert remaining == 0


def test_every_canal_moves_to_lista_manual_with_the_same_whitelist(db_schema):
    schema = f'test_{uuid.uuid4().hex[:12]}'
    schema_id = sql.Identifier(schema)

    with connect() as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(schema_id))

    try:
        with connect(search_path=schema) as conn:
            migrations = list_migrations()
            last = next(index for index, migration in enumerate(migrations) if migration[0].startswith('0006'))
            for _, migration_sql in migrations[:last]:
                conn.execute(migration_sql)

            user_id = conn.execute("INSERT INTO users (spotify_user_id, connection_mode) VALUES ('u1', 'shared') RETURNING id").fetchone()[0]
            canal_id = conn.execute("INSERT INTO canales (user_id, target_playlist_id, target_created_by_bot) VALUES (%s, 't1', true) RETURNING id", (user_id,)).fetchone()[0]
            empty_id = conn.execute("INSERT INTO canales (user_id, target_playlist_id, target_created_by_bot) VALUES (%s, 't2', true) RETURNING id", (user_id,)).fetchone()[0]
            seed = conn.execute("INSERT INTO artist_sources (canal_id, type, playlist_id, include_feats) VALUES (%s, 'seed_playlist', 's1', false) RETURNING id", (canal_id,)).fetchone()[0]
            followed = conn.execute("INSERT INTO artist_sources (canal_id, type) VALUES (%s, 'followed') RETURNING id", (canal_id,)).fetchone()[0]
            manual = conn.execute("INSERT INTO artist_sources (canal_id, type) VALUES (%s, 'manual') RETURNING id", (canal_id,)).fetchone()[0]
            rows = [
                (seed, 'main', 'Principal', False, 'SEED1'),
                (followed, 'main', 'Principal', False, None),
                (seed, 'guest', 'Invitado', True, 'SEED2'),
                (followed, 'fol', 'Seguido', False, None),
                (manual, 'man', 'Manual', False, None),
            ]
            for row in rows:
                conn.execute('INSERT INTO source_artists (artist_source_id, artist_id, name, feat, isrc) VALUES (%s, %s, %s, %s, %s)', row)
            for artist_id in ('main', 'fol', 'man'):
                conn.execute("INSERT INTO whitelist_artists (canal_id, artist_id, added_at) VALUES (%s, %s, '2026-01-02')", (canal_id, artist_id))
            before = conn.execute('SELECT artist_id, added_at FROM whitelist_artists WHERE canal_id = %s ORDER BY artist_id', (canal_id,)).fetchall()
            conn.commit()

            for _, migration_sql in migrations[last:]:
                conn.execute(migration_sql)
            conn.commit()

            after = conn.execute('SELECT artist_id, added_at FROM whitelist_artists WHERE canal_id = %s ORDER BY artist_id', (canal_id,)).fetchall()
            sources = conn.execute('SELECT canal_id, type FROM artist_sources ORDER BY canal_id').fetchall()
            artists = conn.execute(
                'SELECT sa.artist_id, sa.name, sa.feat, sa.isrc FROM source_artists sa JOIN artist_sources s ON s.id = sa.artist_source_id WHERE s.canal_id = %s ORDER BY sa.artist_id',
                (canal_id,),
            ).fetchall()

            assert after == before
            assert sources == [(canal_id, 'manual'), (empty_id, 'manual')]
            assert artists == [('fol', 'Seguido', False, None), ('main', 'Principal', False, 'SEED1'), ('man', 'Manual', False, None)]

            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("INSERT INTO artist_sources (canal_id, type) VALUES (%s, 'followed')", (empty_id,))
            conn.rollback()
    finally:
        with connect() as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(schema_id))
