"""#628 the shared bounded cache: time to live per entry, the bound, #391's short life for failures."""

import threading

import pytest

from desk.ttl_cache import TTLCache


def _cache(max_items=3, ttl=10.0):
    now = [0.0]
    return TTLCache(max_items, ttl, clock=lambda: now[0]), now


def test_an_entry_lives_its_ttl_and_ahead_reads_it_again_early():
    c, now = _cache()
    c.put("a", 1)
    now[0] = 9.9
    assert c.get("a") == 1 and c.get("a", ahead=1.0) is None      # the prewarm refreshes it before it expires
    assert c.get("a") == 1                                          # still there for everyone else
    now[0] = 10.0
    assert c.get("a") is None and len(c) == 0 and c.get("a", "none") == "none"


def test_each_entry_may_live_its_own_time_and_zero_keeps_nothing():
    c, now = _cache()
    c.put("ok", {"rows": 3})
    c.put("failed", "Binance 429", ttl=2)                           # #391: a failure kept briefly
    c.put("empty", [], ttl=0)                                       # or not at all
    assert c.get("empty") is None and len(c) == 2
    now[0] = 2.0
    assert c.get("failed") is None and c.get("ok") == {"rows": 3}
    c.put("ok", "gone", ttl=0)                                      # ttl 0 also drops the old value
    assert c.get("ok") is None


def test_entry_gives_the_age_so_a_caller_can_judge_freshness_itself():
    c, now = _cache(ttl=100)
    c.put("t", None)                                                # None is a value (e.g. "no contract")
    now[0] = 7.0
    assert c.entry("t") == (7.0, None) and c.entry("missing") is None
    now[0] = 100.0
    assert c.entry("t") is None


def test_the_bound_drops_expired_entries_first_then_the_least_recently_used():
    c, now = _cache(max_items=3)
    c.put("short", 1, ttl=1)
    c.put("a", 1)
    c.put("b", 2)
    now[0] = 5.0
    c.put("c", 3)                                                   # "short" has expired: it goes, not "a"
    assert [c.get(k) for k in ("a", "b", "c")] == [1, 2, 3]
    c.get("a")                                                      # "a" used: "b" is now the oldest
    c.put("d", 4)
    assert c.get("b") is None and len(c) == 3 and c.get("a") == 1 and c.get("d") == 4


def test_pop_clear_now_and_bad_arguments():
    c, _ = _cache()
    c.put("a", 1)
    assert c.pop("a") == 1 and c.pop("a", "x") == "x"
    c.put("b", 2, now=50.0)                                         # a caller's own clock
    assert c.get("b", now=59.0) == 2 and c.get("b", now=60.0) is None
    c.put("c", 3)
    c.put("short", 4, ttl=1)
    assert c.values(now=5.0) == [3]                                 # unexpired only, a copy
    c.clear()
    assert len(c) == 0
    with pytest.raises(ValueError):
        TTLCache(0, 10)
    with pytest.raises(ValueError):
        TTLCache(10, 0)


def test_threads_share_it_without_going_over_the_bound():
    c = TTLCache(50, 60)

    def work(n):
        for i in range(500):
            c.put((n, i), i)
            c.get((n, i - 1))

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(c) == 50
