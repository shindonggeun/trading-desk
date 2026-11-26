"""#1020 a closed futures session's minute bars outlive a restart or a deploy, and are taken whether a screen looked
or not: saved to a file on the shared volume (desk.futures_quote.BARS_DIR, a tmp folder here - tests/conftest.py)
by whoever read them and by the ``futures_close_quote`` job at the close and just after it."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from desk import demand
from desk import futures_quote as fq
from tests.test_futures_quote import FakeRest, at, is_open, redis  # noqa: F401 - the redis fixture


class BarsGone(FakeRest):
    """KIS answering a closed session's minutes empty (``closed``: the divisions doing so) - every price too."""

    def __init__(self, closed=()):
        super().__init__()
        self.closed = set(closed)

    async def get(self, path, tr_id, params):
        if params["FID_COND_MRKT_DIV_CODE"] in self.closed:
            self.calls.append((tr_id, params))
            return {"rt_cd": "0", "output1": {"futs_prpr": ""}, "output2": []}
        return await super().get(path, tr_id, params)


def _restart(rest, when):
    return fq.FuturesQuote(rest, now=lambda: when, is_open=is_open)


def test_nobody_looked_the_close_read_alone_keeps_the_night_for_the_morning_after_a_restart():
    rest = BarsGone()
    job = fq.FuturesQuote(rest, now=lambda: at("2026-11-17", "05:59") + timedelta(seconds=30), is_open=is_open)
    assert asyncio.run(job.read_close()) == "night"                      # the collector's job, no screen read before
    rest.closed = {"CM"}                                                  # 11:37: KIS has the closed night no more
    morning = _restart(rest, at("2026-11-17", "11:37"))                   # a deploy in between: nothing in memory
    for interval, n in (("1m", 720), ("5m", 144), ("15m", 48)):
        got = asyncio.run(morning.candles("night", interval))
        assert got["held"] is True and got["session"] == "night" and len(got["bars"]) == n
        assert got["bars"][0]["t"].startswith("2026-11-16T18:00")
    quote = asyncio.run(morning.quote("night"))                           # the card's stand-in from those bars
    assert quote["from_bars"] is True and quote["bar_at"].startswith("2026-11-17T05:59")


def test_the_read_after_the_bell_keeps_the_last_minute():
    rest = BarsGone()
    now, clock = [at("2026-11-17", "05:59") + timedelta(seconds=30)], [0.0]
    job = fq.FuturesQuote(rest, now=lambda: now[0], clock=lambda: clock[0], is_open=is_open)
    asyncio.run(job.read_close())
    assert fq.load_bars("night", "2026-11-16")[-1]["t"].startswith("2026-11-17T05:59")
    now[0] = at("2026-11-17", "06:00") + timedelta(seconds=fq.CLOSE_BARS_AFTER_SEC)
    clock[0] += 90.0
    assert asyncio.run(job.read_close()) == "night"
    assert fq.load_bars("night", "2026-11-16")[-1]["t"].startswith("2026-11-17T06:00")
    now[0] = at("2026-11-17", "06:05")                                    # past it: nothing to read
    assert asyncio.run(job.read_close()) is None


def test_the_day_session_is_kept_the_same_way():
    rest = BarsGone()
    job = fq.FuturesQuote(rest, now=lambda: at("2026-11-17", "15:44") + timedelta(seconds=30), is_open=is_open)
    assert asyncio.run(job.read_close()) == "day"
    rest.closed = {"F"}
    evening = _restart(rest, at("2026-11-17", "17:30"))
    got = asyncio.run(evening.candles("day", "15m"))
    assert got["held"] is True and got["bars"][0]["t"].startswith("2026-11-17T08:45") and len(got["bars"]) == 28


def test_split_roles_a_restarted_collector_shares_the_saved_bars_and_the_api_never_asks_kis(redis):  # noqa: F811
    first = BarsGone()
    asyncio.run(fq.FuturesQuote(first, now=lambda: at("2026-11-17", "05:59") + timedelta(seconds=30),
                                is_open=is_open).read_close())
    rest, asked = BarsGone({"CM"}), BarsGone({"CM"})
    when = at("2026-11-17", "07:10")
    collector, api = _restart(rest, when), _restart(asked, when)

    async def go():
        demand.configure("collector")
        await collector.serve("candles:night:2026-11-16")
        demand.configure("api")
        return await api.candles("night", "5m")

    got = asyncio.run(go())
    assert got["held"] is True and len(got["bars"]) == 144 and asked.calls == []


def test_a_file_of_another_session_or_a_broken_one_is_not_taken(tmp_path):
    bar = {"t": "2026-11-13T18:00:00+09:00", "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 1}
    assert fq.save_bars("night", "2026-11-13", [bar])                          # Friday's night
    rest = BarsGone({"CM"})
    assert asyncio.run(_restart(rest, at("2026-11-17", "11:37")).candles("night"))["bars"] == []   # Monday's night
    path = fq.BARS_DIR / "futures-bars-night.json"
    for broken in ("{", json.dumps({"session": "night", "date": "2026-11-16", "bars": [{"t": "x", "c": "1"}]}),
                   json.dumps({"session": "night", "date": "2026-11-16", "bars": [{"t": "b", "o": 1, "h": 1, "l": 1,
                                                                                    "c": 1, "v": 1}] * 2})):
        path.write_text(broken, encoding="utf-8")
        assert fq.load_bars("night", "2026-11-16") is None
        assert asyncio.run(_restart(rest, at("2026-11-17", "11:37")).candles("night"))["bars"] == []


def test_a_shorter_read_never_replaces_a_longer_one():
    bar = {"o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 1}
    long = [{**bar, "t": f"2026-11-16T18:0{m}:00+09:00"} for m in range(5)]
    assert fq.save_bars("night", "2026-11-16", long)
    assert not fq.save_bars("night", "2026-11-16", long[:3])                   # e.g. another process's earlier read
    assert not fq.save_bars("night", "2026-11-16", long)                       # the same: not written again
    assert fq.load_bars("night", "2026-11-16") == long
    settled = {**long[-1], "c": 2.0}
    assert fq.save_bars("night", "2026-11-16", [settled])                      # one minute again, settled
    assert fq.load_bars("night", "2026-11-16") == [*long[:-1], settled]
    assert fq.save_bars("night", "2026-11-17", long[:1])                       # the next session replaces it
    assert fq.load_bars("night", "2026-11-16") is None


def test_while_it_trades_the_bars_are_saved_at_most_every_few_minutes():
    clock = [0.0]
    now = [at("2026-11-16", "22:23")]
    quotes = fq.FuturesQuote(FakeRest(), now=lambda: now[0], clock=lambda: clock[0], is_open=is_open)
    asyncio.run(quotes.candles("night"))
    first = fq.load_bars("night", "2026-11-16")[-1]["t"]
    now[0], clock[0] = at("2026-11-16", "22:24"), fq.CACHE_OPEN_SEC + 1       # a new read, a minute later
    asyncio.run(quotes.candles("night"))
    assert fq.load_bars("night", "2026-11-16")[-1]["t"] == first              # not yet
    now[0], clock[0] = at("2026-11-16", "22:29"), fq.BARS_SAVE_SEC + 20
    asyncio.run(quotes.candles("night"))
    assert fq.load_bars("night", "2026-11-16")[-1]["t"].startswith("2026-11-16T22:29")


class LastMinuteSettles(FakeRest):
    """The night's rows end at 05:59 (no 06:00 row); its close is 781 at 05:59:30 and 785 once it settles."""

    def __init__(self):
        super().__init__()
        self.close = 781.0

    async def get(self, path, tr_id, params):
        got = await super().get(path, tr_id, params)
        if tr_id == fq.CANDLE_TR:
            rows = [r for r in got["output2"] if r["stck_cntg_hour"][:4] != "0600"]
            if rows and rows[0]["stck_cntg_hour"][:4] == "0559":
                rows[0] = {**rows[0], "futs_prpr": str(self.close)}
            got = {**got, "output2": rows}
        return got


def test_the_last_bar_settling_after_the_bell_is_saved_though_its_minute_is_the_same():
    rest = LastMinuteSettles()
    now, clock = [at("2026-11-17", "05:59") + timedelta(seconds=30)], [0.0]
    job = fq.FuturesQuote(rest, now=lambda: now[0], clock=lambda: clock[0], is_open=is_open)
    asyncio.run(job.read_close())
    assert fq.load_bars("night", "2026-11-16")[-1]["c"] == 781.0
    rest.close, now[0], clock[0] = 785.0, at("2026-11-17", "06:01"), 90.0
    assert asyncio.run(job.read_close()) == "night"
    saved = fq.load_bars("night", "2026-11-16")
    assert saved[-1]["t"].startswith("2026-11-17T05:59") and saved[-1]["c"] == 785.0
    # the read after the bell was one short page (KIS ended it early): the night's earlier minutes stay, here too
    assert len(saved) == 720 and len(asyncio.run(job.candles("night"))["bars"]) == 720


def test_writers_at_once_each_use_a_temporary_file_of_their_own():
    from concurrent.futures import ThreadPoolExecutor

    bar = {"o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 1}
    lists = [[{**bar, "t": f"2026-11-16T{18 + m // 60:02d}:{m % 60:02d}:00+09:00"} for m in range(n)]
             for n in range(100, 140)]
    with ThreadPoolExecutor(8) as pool:                    # the api's and the collector's saves, both PID 1
        list(pool.map(lambda bars: fq.save_bars("night", "2026-11-16", bars), lists))
    assert fq.load_bars("night", "2026-11-16") is not None
    assert not list(fq.BARS_DIR.glob("*.tmp"))


def test_bars_failing_at_the_close_fail_the_job_so_it_runs_again_after_the_bell():
    class CandlesDown(FakeRest):
        down = True

        async def get(self, path, tr_id, params):
            if tr_id == fq.CANDLE_TR and self.down:
                raise RuntimeError("KIS 500")
            return await super().get(path, tr_id, params)

    rest = CandlesDown()
    now, clock = [at("2026-11-17", "05:59") + timedelta(seconds=30)], [0.0]
    job = fq.FuturesQuote(rest, now=lambda: now[0], clock=lambda: clock[0], is_open=is_open)
    try:
        asyncio.run(job.read_close())
        raise AssertionError("the job must fail")
    except RuntimeError:
        pass
    assert [t for t, _ in rest.calls].count(fq.PRICE_TR) == 1           # the close quote was read first
    rest.down, now[0], clock[0] = False, at("2026-11-17", "06:00"), 30.0 + fq.CACHE_FAIL_SEC
    assert asyncio.run(job.read_close()) == "night"                     # desk.jobs' retry, 30 s on
    assert fq.load_bars("night", "2026-11-16") is not None


def test_fridays_night_answers_through_the_weekend_until_the_monday_open():
    rest = BarsGone()
    asyncio.run(fq.FuturesQuote(rest, now=lambda: at("2026-11-14", "05:59") + timedelta(seconds=30),
                                is_open=is_open).read_close())        # Friday's night, ending Saturday 06:00
    rest.closed = {"CM"}
    for when in (at("2026-11-14", "12:00"), at("2026-11-15", "20:00"), at("2026-11-16", "08:44")):
        got = asyncio.run(_restart(rest, when).candles("night"))
        assert got["held"] is True and got["session_date"] == "2026-11-13" and len(got["bars"]) == 720


def test_a_file_with_times_that_are_not_times_or_too_big_is_not_taken():
    path = fq.BARS_DIR / "futures-bars-night.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    plain = {"o": 1, "h": 1, "l": 1, "c": 1, "v": 1}
    for bars in ([{**plain, "t": "a"}, {**plain, "t": "b"}],
                 [{**plain, "t": "2026-11-16T18:00:00"}]):                 # no zone
        path.write_text(json.dumps({"session": "night", "date": "2026-11-16", "bars": bars}), encoding="utf-8")
        assert fq.load_bars("night", "2026-11-16") is None
    good = [{**plain, "t": "2026-11-16T18:00:00+09:00"}]
    path.write_text(json.dumps({"session": "night", "date": "2026-11-16", "bars": good, "pad": "x" * 600_000}),
                    encoding="utf-8")
    assert fq.load_bars("night", "2026-11-16") is None


def test_a_killed_writers_temporary_file_is_dropped_within_the_hour():
    import os

    fq.BARS_DIR.mkdir(parents=True, exist_ok=True)
    old, new = fq.BARS_DIR / "futures-bars-night.json.abc.tmp", fq.BARS_DIR / "futures-bars-night.json.def.tmp"
    for f in (old, new):
        f.write_text("{", encoding="utf-8")
    hour_ago = old.stat().st_mtime - fq.STALE_TMP_SEC - 1
    os.utime(old, (hour_ago, hour_ago))
    bar = {"t": "2026-11-16T18:00:00+09:00", "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 1}
    assert fq.save_bars("night", "2026-11-16", [bar])
    assert not old.exists() and new.exists()                                    # a writer at work now is left alone
