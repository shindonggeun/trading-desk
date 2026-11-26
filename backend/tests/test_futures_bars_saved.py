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
