from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

import pytest

from app import web, web_cache
from app.web_cache import WebCache


def test_cache_is_scoped_bounded_and_returns_copies():
    cache = WebCache(limit=2)
    first = cache.get((1, 'library'), lambda: [{'name': 'Uno'}])
    first[0]['name'] = 'Mutado'
    assert cache.get((1, 'library'), lambda: []) == [{'name': 'Uno'}]
    assert cache.get((2, 'library'), lambda: [{'name': 'Dos'}]) == [{'name': 'Dos'}]
    cache.get((3, 'library'), lambda: [])
    assert len(cache.entries) == 2
    assert (1, 'library') not in cache.entries


def test_expired_cache_is_reloaded_and_failures_are_not_cached():
    cache = WebCache()
    cache.get((1, 'library'), lambda: 'old', ttl=-1)
    load = Mock(side_effect=ValueError('fallo'))
    with pytest.raises(ValueError):
        cache.get((1, 'library'), load)
    assert cache.get((1, 'library'), lambda: 'new') == 'new'


def test_one_load_for_concurrent_requests():
    cache = WebCache()
    started = Event()
    release = Event()

    def load():
        started.set()
        assert release.wait(5)
        return 'shared'

    duplicate = Mock(return_value='duplicate')
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(cache.get, (1, 'library'), load)
        assert started.wait(5)
        second = executor.submit(cache.get, (1, 'library'), duplicate)
        release.set()
        assert first.result() == second.result() == 'shared'
    assert not duplicate.called


def test_invalidation_during_load_prevents_stale_cache():
    cache = WebCache()

    def load():
        cache.clear(1)
        return 'stale'

    cache.get((1, 'library'), load)
    assert cache.get((1, 'library'), lambda: 'fresh') == 'fresh'


def test_snapshot_change_uses_new_artists_and_clear_keeps_other_users():
    cache = WebCache()
    cache.get((1, 'artists', 'p1', 'v1'), lambda: ['old'])
    cache.get((2, 'library'), lambda: ['other'])
    assert cache.get((1, 'artists', 'p1', 'v2'), lambda: ['new']) == ['new']
    cache.clear(1)
    assert list(cache.entries) == [(2, 'library')]


def test_library_lasts_five_minutes_and_a_hit_does_not_touch_the_db(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(web_cache.time, 'monotonic', lambda: now[0])
    fetch = Mock(return_value=[{'id': 'p1'}])
    save = Mock()
    monkeypatch.setattr(web.spotify, 'get_my_playlists', fetch)
    monkeypatch.setattr(web.copias, 'save_library', save)
    monkeypatch.setattr(web.canales, 'sync_target_names', Mock())
    monkeypatch.setattr(web, 'get_token', lambda *args: 'token')
    monkeypatch.setattr(web, 'library_cache', WebCache())
    user, conn = {'id': 7}, Mock(autocommit=False)

    web.sync_library(conn, user)
    now[0] += 4 * 60
    web.sync_library(conn, user)
    web.get_library(conn, user)

    assert fetch.call_count == save.call_count == conn.commit.call_count == 1

    now[0] += 61
    assert web.sync_library(conn, user) == [{'id': 'p1'}]
    assert fetch.call_count == save.call_count == 2
