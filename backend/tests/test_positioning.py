import asyncio
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from desk import positioning as pz

TODAY = date(2026, 11, 6)   # Friday


def _row(strike, oi, volume=0, iv=None):
    return {"strike": strike, "oi": oi, "volume": volume, "iv": iv}


# --- options arithmetic ---------------------------------------------------------------------

def test_max_pain_is_where_holders_collect_least():
    calls = [_row(90, 100), _row(100, 500), _row(110, 1000)]
    puts = [_row(90, 1000), _row(100, 500), _row(110, 100)]
    assert pz.max_pain(calls, puts) == 100
    assert pz.max_pain([], []) is None


def test_summarise_options_walls_ratios_lean_and_moves():
    near = TODAY + timedelta(days=7)
    later = TODAY + timedelta(days=28)
    far = TODAY + timedelta(days=90)                      # beyond OPTION_DAYS: ignored
    chains = [
        (near, [_row(100, 200, 50, 0.40), _row(105, 900, 300, 0.42), _row(110, 400, 20)],
               [_row(95, 1500, 400), _row(100, 300, 60, 0.44), _row(90, 700)]),
        (later, [_row(105, 600, 10), _row(120, 2000, 5)], [_row(90, 2500, 30)]),
        (far, [_row(150, 99999)], [_row(50, 99999)]),
    ]
    o = pz.summarise_options(chains, 101.0, TODAY, iv30=60.0)
    assert o["expiries"] == [near.isoformat(), later.isoformat()]
    assert o["call_oi"] == 4100 and o["put_oi"] == 5000
    assert o["put_call_oi"] == 1.22 and o["lean"] == "put_heavy"
    assert o["near"]["put_call_oi"] == round(2500 / 1500, 2)
    assert [w["strike"] for w in o["call_walls"]] == [120, 105, 110]      # above 101 only, by OI
    assert [w["strike"] for w in o["put_walls"]] == [90, 95, 100]
    assert o["call_walls"][0]["distance_pct"] == round((120 / 101 - 1) * 100, 1)
    assert o["max_pain"]["expiry"] == later.isoformat()                   # the heaviest expiry
    m = o["implied_move"]
    assert m["expiry"] == near.isoformat() and m["iv_pct"] == 42.0       # ATM (100) call + put
    assert m["move_30d_pct"] == round(60 * (30 / 365) ** 0.5, 1)
    assert pz.summarise_options([], 100, TODAY) is None
    assert pz.summarise_options(chains, 0, TODAY) is None


def test_lean_thresholds():
    one = lambda c, p: [(TODAY + timedelta(days=3), [_row(100, c)], [_row(100, p)])]
    assert pz.summarise_options(one(100, 60), 100, TODAY)["lean"] == "call_heavy"
    assert pz.summarise_options(one(100, 100), 100, TODAY)["lean"] == "balanced"
    assert pz.summarise_options(one(0, 100), 100, TODAY)["put_call_oi"] is None


def test_parse_cboe_keeps_the_tickers_own_root_and_the_window():
    payload = {"data": {"current_price": 183.86, "iv30": 62.0, "options": [
        {"option": "NVDA261113C00185000", "open_interest": 3112, "volume": 9891, "iv": 0.75},
        {"option": "NVDA261113P00180000", "open_interest": 15030, "volume": 8860, "iv": 0.0},
        {"option": "B1VDA261113C00185000", "open_interest": 999, "volume": 1, "iv": 0.5},   # adjusted root
        {"option": "NVDA270115C00200000", "open_interest": 5, "volume": 1, "iv": 0.5},     # too far
        {"option": "NVDAbad", "open_interest": 5},
    ]}}
    got = pz.parse_cboe(payload, "NVDA", TODAY)
    assert got["spot"] == 183.86 and got["iv30"] == 62.0 and got["source"] == "cboe"
    (day, calls, puts), = got["chains"]
    assert day == date(2026, 11, 13)
    assert calls == [{"strike": 185.0, "oi": 3112, "volume": 9891, "iv": 0.75}]
    assert puts == [{"strike": 180.0, "oi": 15030, "volume": 8860, "iv": None}]   # 0 IV = unknown
    assert pz.parse_cboe({"data": {"options": []}}, "NVDA", TODAY) is None
    brk = {"data": {"options": [{"option": "BRKB261113C00500000", "open_interest": 1}]}}
    assert pz.parse_cboe(brk, "BRK.B", TODAY)["chains"]


# --- short volume -----------------------------------------------------------------------------

def _finra(rows):
    return ("Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n" + "\n".join(
        f"20261105|{s}|{short}|0|{total}|B,Q,N" for s, short, total in rows)).encode()


def test_finra_row_searches_the_raw_file():
    raw = _finra([("NV", 1, 2), ("NVDA", 300.5, 1000), ("BRK.B", 10, 40)])
    assert pz.finra_row(raw, "NVDA") == (300.5, 1000.0) and pz.finra_row(raw, "NV") == (1.0, 2.0)
    assert pz.finra_row(raw, "BRK.B") == (10.0, 40.0) and pz.finra_row(raw, "AMD") is None


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_short_volume_history_holidays_recent_misses_and_bad_bodies():
    asked, clock = [], Clock()
    late = {"20261105"}                                   # yesterday's file not posted yet at first

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("CNMSshvol", 1)[1][:8]
        asked.append(name)
        if name in late or name in ("20261106", "20261013"):   # today, yesterday (late), a holiday long ago
            return httpx.Response(404)
        if name == "20261104":
            return httpx.Response(200, text="<html>maintenance</html>")   # not a FINRA file: never kept
        day = int(name[-2:])
        return httpx.Response(200, content=_finra([("NVDA", 400 + day, 1000), ("BRK.B", 1, 4)]))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sv = pz.ShortVolume(client, clock=clock)
            first = await sv.history("NVDA", TODAY)
            asked.clear()
            again = await sv.history("NVDA", TODAY)
            asked_again = list(asked)
            late.clear()
            clock.t += pz.MISS_TTL_SEC + 1
            asked.clear()
            later = await sv.history("NVDA", TODAY)
            return first, again, asked_again, sorted(asked), later, await sv.history("BRK-B", TODAY)
    first, again, asked_again, asked_later, later, brk = asyncio.run(go())
    dates = [r["date"] for r in first]
    assert "2026-11-05" not in dates and "2026-11-04" not in dates and "2026-10-13" not in dates
    assert first == again and asked_again == []      # misses and bad bodies wait MISS_TTL (never kept)
    assert asked_later == ["20261104", "20261105", "20261106"]   # recent misses retried; the old holiday is not
    assert later[-1]["date"] == "2026-11-05" and later[-1]["ratio"] == 0.405
    assert brk[-1]["ratio"] == 0.25                              # BRK-B is BRK.B in FINRA's files


def test_a_finra_outage_with_nothing_kept_is_an_error():
    def handler(request):
        return httpx.Response(503)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await pz.ShortVolume(client).history("NVDA", TODAY)
    with pytest.raises(httpx.HTTPError):
        asyncio.run(go())


def test_summarise_short_trend_and_crowding():
    hist = [{"date": f"d{i}", "ratio": 0.40, "short": 1, "total": 1} for i in range(19)]
    rising = pz.summarise_short(hist + [{"date": "d19", "ratio": 0.52, "short": 1, "total": 1}], {"pct_float": 12.0})
    assert rising["trend"] == "rising" and rising["volume_ratio"] == 52.0 and rising["crowded"] and rising["days"] == 20
    steady = pz.summarise_short(hist + [{"date": "d19", "ratio": 0.42, "short": 1, "total": 1}], {"pct_float": 2.0})
    assert steady["trend"] == "steady" and not steady["crowded"] and steady["volume_ratio_20d"] == 40.1
    assert pz.summarise_short([], None) is None
    assert pz.summarise_short([], {"pct_float": 3.0})["interest"] == {"pct_float": 3.0}


def test_short_interest_units():
    got = pz.short_interest_from({"shortPercentOfFloat": 0.013, "shortRatio": 2.84, "sharesShort": 300,
                                  "sharesShortPriorMonth": 250, "dateShortInterest": 1792022400})
    assert got == {"pct_float": 1.3, "days_to_cover": 2.84, "shares": 300, "change_pct": 20.0, "as_of": "2026-10-15"}
    assert pz.short_interest_from({}) is None


# --- the whole picture -------------------------------------------------------------------------

class FakeShortVolume:
    async def history(self, ticker, today):
        return [{"date": "2026-11-05", "ratio": 0.3, "short": 3, "total": 10}] * 20


def _fake(chains=None, interest=None, fail=None):
    calls, clock = {"chains": 0, "spot": 0}, Clock()

    async def get_chains(ticker, today):
        calls["chains"] += 1
        if fail:
            raise fail
        return chains

    p = pz.Positioning(FakeShortVolume(), chains=get_chains, interest=lambda t: interest, clock=clock)
    return p, calls, clock


def test_positioning_combines_caches_and_skips_non_us():
    chains = {"spot": 100.0, "iv30": 50.0, "source": "cboe",
              "chains": [(TODAY + timedelta(days=7), [_row(110, 100)], [_row(90, 300)])]}
    p, calls, _ = _fake(chains, {"pct_float": 15.0, "days_to_cover": 3.0, "change_pct": 5.0, "as_of": "2026-10-15"})

    async def spot_of():
        calls["spot"] += 1
        return 101.0
    assert p.needs_spot("NVDA")
    got = asyncio.run(p.get("nvda", today=TODAY, spot_of=spot_of))
    assert got["supported"] and got["options"]["spot"] == 101.0 and got["options"]["source"] == "cboe"
    assert got["options"]["lean"] == "put_heavy" and got["short"]["crowded"]
    assert "풋 쪽에 몰림" in got["verdict"] and "숏 스퀴즈" in got["verdict"]
    assert got["sources"] == ["finra", "yahoo", "cboe"]
    asyncio.run(p.get("NVDA", today=TODAY, spot_of=spot_of))
    assert calls == {"chains": 1, "spot": 1} and not p.needs_spot("NVDA")   # cached: no quote asked
    note = pz.prompt_note(got)
    assert "put/call OI 3.0" in note and "Call walls above: 110 (+8.9%)" in note and "CROWDED" in note
    kr = asyncio.run(p.get("005930.KS", 70000, TODAY))
    assert kr["supported"] is False and kr["reason"] and pz.prompt_note(kr) is None
    assert set(kr) == set(got)                                   # one shape for every answer


def test_a_failed_source_is_reported_kept_briefly_and_the_others_still_answer():
    p, calls, clock = _fake(fail=ConnectionError("cboe down"))
    got = asyncio.run(p.get("NVDA", 100.0, TODAY))
    assert got["options"] is None and got["short"]["volume_ratio"] == 30.0
    assert got["errors"] == ["options: ConnectionError"]
    asyncio.run(p.get("NVDA", 100.0, TODAY))
    assert calls["chains"] == 1                                  # the failure is kept for FAIL_TTL
    clock.t += pz.FAIL_TTL_SEC + 1
    asyncio.run(p.get("NVDA", 100.0, TODAY))
    assert calls["chains"] == 2


def test_an_expiry_today_is_left_out():
    chains = [(TODAY, [_row(110, 9999)], [_row(90, 9999)]), (TODAY + timedelta(days=7), [_row(110, 10)], [_row(90, 10)])]
    o = pz.summarise_options(chains, 100, TODAY)
    assert o["expiries"] == [(TODAY + timedelta(days=7)).isoformat()] and o["call_oi"] == 10


def test_junk_atm_iv_falls_back_to_the_next_priced_strike():
    exp = TODAY + timedelta(days=10)
    chains = [(exp, [_row(100, 10, iv=7.5), _row(105, 10, iv=0.5)], [_row(100, 10, iv=None)])]
    assert pz.summarise_options(chains, 100, TODAY)["implied_move"]["iv_pct"] == 50.0


@pytest.mark.parametrize("ticker,ok", [("NVDA", True), ("BRK.B", True), ("005930.KS", False), ("^GSPC", False),
                                       ("CL=F", False), ("BTC-USD", False)])
def test_us_ticker_rule(ticker, ok):
    assert pz.is_us_ticker(ticker) is ok


# --- API -----------------------------------------------------------------------------------

def test_positioning_api(db_url, monkeypatch):
    from tests.test_api import OWNER, PASSWORD, _client

    p, _, _ = _fake({"spot": 100.0, "iv30": None, "source": "cboe",
                  "chains": [(datetime.now(UTC).date() + timedelta(days=7), [_row(110, 100)], [_row(90, 100)])]}, None)
    monkeypatch.setattr(pz, "POSITIONING", p)
    with _client(db_url, login=False) as client:
        assert client.get("/api/positioning", params={"ticker": "NVDA"}).status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        one = client.get("/api/positioning", params={"ticker": "nvda"}).json()
        assert one["ticker"] == "NVDA" and one["options"]["lean"] == "balanced"
        assert client.get("/api/positioning", params={"ticker": "bad ticker"}).status_code == 422
        batch = client.get("/api/positioning/batch", params={"tickers": "NVDA,005930.KS,NVDA"}).json()
        assert [i["ticker"] for i in batch["items"]] == ["NVDA", "005930.KS"]
        assert batch["items"][1]["supported"] is False
        many = ",".join(f"T{i}" for i in range(31))
        assert client.get("/api/positioning/batch", params={"tickers": many}).status_code == 422
