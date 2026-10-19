import json

import httpx
import pytest
from obslog import RunConfig, start_run

from app import canales, tick, users
from app.deezer import API_URL as DEEZER_URL
from app.spotify import ACCOUNTS_URL, API_URL
from test_entregas import (
    FRIDAY,
    SATURDAY,
    add_lote,
    item,
    mock_post,
    new_canal,
    playlist,
)
from test_runs import (
    THURSDAY,
    deezer_album,
    mock_deezer_artist,
    mock_spotify_album,
    new_user,
    track,
    uid,
)


@pytest.fixture
def events(tmp_path):
    '''
    Corrida de obslog en una carpeta temporal; devuelve una funcion que lee los eventos del run.jsonl.
        Returns:
            read (function): devuelve los eventos logueados hasta el momento
    '''
    with start_run(RunConfig(logs_dir=str(tmp_path), target='test', quiet=True)):
        def read():
            path = next(tmp_path.glob('dev/test/*/run.jsonl'))
            return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]

        yield read


def by_event(entries, event):
    return [entry for entry in entries if entry['event'] == event]


def test_needs_run_until_explored_today_with_nothing_pending(db):
    canal = new_canal(db)

    assert tick.needs_run(db, canal['user_id'], THURSDAY)

    db.execute("INSERT INTO runs (user_id, date, status) VALUES (%s, %s, 'fallido')", (canal['user_id'], THURSDAY))
    assert tick.needs_run(db, canal['user_id'], THURSDAY)

    db.execute("UPDATE runs SET status = 'exitoso' WHERE user_id = %s", (canal['user_id'],))
    assert not tick.needs_run(db, canal['user_id'], THURSDAY)

    add_lote(db, canal, SATURDAY, [item()])
    assert tick.needs_run(db, canal['user_id'], THURSDAY)


def test_tick_runs_user_and_logs_calls(db, db_schema, spotify, events):
    user = new_user(db, spotify)
    a = uid('a')
    canal_id = canales.create_canal(db, user['id'], uid('p'), 'Radar', True)
    canales.add_manual_artist(db, canal_id, a, 'Artista A')
    db.execute("UPDATE whitelist_artists SET added_at = '2026-09-01T12:00:00-03:00' WHERE canal_id = %s", (canal_id,))
    db.commit()
    single = deezer_album('2026-09-28')
    mock_deezer_artist(spotify, 'Artista A', [single])
    mock_spotify_album(spotify, single, [a], [track(1, a), track(2, a)])

    assert tick.tick_user(user, THURSDAY, db_schema) == 'exitoso'

    calls = by_event(events(), 'run_calls')[0]
    assert calls['spotify_calls'] == 3
    assert calls['deezer_calls'] == 3
    assert by_event(events(), 'run_done')[0]['found'] == 2
    assert by_event(events(), 'canal_pending')[0]['pending'] == 2

    # el mismo dia ya no hay nada que hacer y no se llama a nadie
    spotify.reset()
    assert tick.tick_user(user, THURSDAY, db_schema) == 'al dia'
    assert not spotify.calls


def test_tick_logs_user_without_canales(db, db_schema, spotify, events):
    # un amigo que entro y nunca armo un Canal tiene que verse en el log del Tick (#20)
    user = new_user(db, spotify)

    assert tick.tick_user(user, THURSDAY, db_schema) == 'exitoso'
    assert by_event(events(), 'no_canales')


def test_tick_logs_whitelist_size_per_canal(db, db_schema, spotify, events):
    # un Canal con la Whitelist vacia no explora nada: el log lo tiene que decir (#20)
    user = new_user(db, spotify)
    empty_id = canales.create_canal(db, user['id'], uid('p'), 'Vacio', True)
    full_id = canales.create_canal(db, user['id'], uid('p'), 'Lleno', True)
    canales.add_manual_artist(db, full_id, uid('a'), 'Artista A')
    db.commit()
    spotify.get(f'{DEEZER_URL}/search/artist').respond(json={'data': []})

    assert tick.tick_user(user, THURSDAY, db_schema) == 'exitoso'

    pending = {entry['canal_id']: entry['artists'] for entry in by_event(events(), 'canal_pending')}
    assert pending == {empty_id: 0, full_id: 1}
    assert not by_event(events(), 'no_canales')


def test_tick_skips_disconnected_user(db, db_schema, spotify, events):
    user_id = users.save_shared_login(db, uid('u'), 'refresh')['user_id']
    users.mark_disconnected(db, user_id)
    db.commit()

    assert tick.tick_user(users.get_user(db, user_id), THURSDAY, db_schema) == 'desconectado'
    assert not spotify.calls


def test_tick_logs_quota_exceeded(db, db_schema, spotify, events):
    user = new_user(db, spotify)
    spotify.get(f'{API_URL}/me/playlists').mock(return_value=httpx.Response(429, headers={'retry-after': '86000'}, json={'error': {'status': 429, 'reason': 'QUOTA_EXCEEDED'}}))

    assert tick.tick_user(user, THURSDAY, db_schema) == 'quota'
    assert by_event(events(), 'quota_exceeded')[0]['retry_after'] == '86000'


def test_tick_logs_each_canal_entrega(db, db_schema, spotify, events):
    canal = new_canal(db)
    other_id = canales.create_canal(db, canal['user_id'], uid('p'), 'Otro', True)
    canales.add_manual_artist(db, other_id, 'a1', 'a1')
    db.execute('UPDATE canales SET guardado_filter = false WHERE id = %s', (other_id,))
    other = canales.get_canal(db, canal['user_id'], other_id)
    db.commit()
    add_lote(db, canal, SATURDAY, [item()])
    add_lote(db, other, SATURDAY, [item(), item(number=2)])
    spotify.post(f'{ACCOUNTS_URL}/api/token').respond(json={'access_token': 'token'})
    spotify.get(f'{DEEZER_URL}/search/artist').respond(json={'data': []})
    spotify.get(f'{API_URL}/me/playlists').respond(json={'items': [playlist(canal), playlist(other)], 'next': None})
    mock_post(spotify, canal, httpx.Response(500))
    mock_post(spotify, other)

    assert tick.tick_user(users.get_user(db, canal['user_id']), FRIDAY, db_schema) == 'fallido'

    failed = by_event(events(), 'entrega_failed')
    assert [entry['canal_id'] for entry in failed] == [canal['id']]
    assert 'traceback' in failed[0]

    done = by_event(events(), 'entrega_done')
    assert [(entry['canal_id'], entry['inserted'], entry['target_total']) for entry in done] == [(other['id'], 2, 2)]


def test_tick_summary_counts_outcomes(monkeypatch, events):
    outcomes = iter(['exitoso', 'quota', 'al dia'])
    monkeypatch.setattr(tick, 'connect', lambda search_path=None: FakeConn(3))
    monkeypatch.setattr(tick, 'tick_user', lambda user, today, search_path: next(outcomes))

    summary = tick.run_tick(THURSDAY)

    assert summary['users'] == 3
    assert summary['runs exitosos'] == 1
    assert summary['runs fallidos'] == 1
    assert summary['al dia'] == 1
    assert summary['quota de spotify agotada']


class FakeConn:
    '''
    Conexion de mentira que devuelve n Users vacios, para contar resultados sin DB.
    '''

    def __init__(self, n):
        self.n = n

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self, row_factory=None):
        return self

    def execute(self, query):
        return self

    def fetchall(self):
        return [{'id': n} for n in range(1, self.n + 1)]
