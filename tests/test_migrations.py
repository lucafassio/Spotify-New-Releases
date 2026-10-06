import psycopg
import pytest

from app.db import connect
from app.migrate import MIGRATIONS_LOCK, apply_migrations

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
