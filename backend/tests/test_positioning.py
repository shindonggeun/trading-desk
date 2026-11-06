import asyncio
from datetime import date, timedelta

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
    return "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n" + "\n".join(
        f"20261105|{s}|{short}|0|{total}|B,Q,N" for s, short, total in rows)


def test_parse_finra():
    assert pz.parse_finra(_finra([("NVDA", 300.5, 1000), ("BRK.B", 10, 40)]) + "\nbad|line") == {
        "NVDA": (300.5, 1000.0), "BRK.B": (10.0, 40.0)}


def test_short_volume_history_skips_holidays_and_does_not_remember_todays_miss():
    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("CNMSshvol", 1)[1][:8]
        asked.append(name)
        if name in ("20261106", "20261103"):                # today (not yet out) and a holiday
            return httpx.Response(404)
        day = int(name[-2:])
        return httpx.Response(200, text=_finra([("NVDA", 400 + day, 1000), ("BRK.B", 1, 4)]))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sv = pz.ShortVolume(client)
            first = await sv.history("NVDA", TODAY)
            asked.clear()
            again = await sv.history("NVDA", TODAY)
            asked_again = list(asked)
            return first, again, asked_again, await sv.history("BRK-B", TODAY)
    first, again, asked_again, brk = asyncio.run(go())
    assert len(first) == pz.FINRA_DAYS and first[-1]["date"] == "2026-11-05"
    assert "2026-11-03" not in [r["date"] for r in first] and first[-1]["ratio"] == 0.405
    assert first == again and asked_again == ["20261106"]   # only today's file is asked again
    assert brk[-1]["ratio"] == 0.25                        # BRK-B is BRK.B in FINRA's files


def test_summarise_short_trend_and_crowding():
    hist = [{"date": f"d{i}", "ratio": 0.40, "short": 1, "total": 1} for i in range(19)]
    rising = pz.summarise_short(hist + [{"date": "d19", "ratio": 0.52, "short": 1, "total": 1}], {"pct_float": 12.0})
    assert rising["trend"] == "rising" and rising["volume_ratio"] == 52.0 and rising["crowded"]
    steady = pz.summarise_short(hist + [{"date": "d19", "ratio": 0.42, "short": 1, "total": 1}], {"pct_float": 2.0})
    assert steady["trend"] == "steady" and not steady["crowded"] and steady["volume_ratio_20d"] == 40.1
    assert pz.summarise_short([], None) is None
    assert pz.summarise_short([], {"pct_float": 3.0})["interest"] == {"pct_float": 3.0}


# --- the whole picture -------------------------------------------------------------------------

class FakeShortVolume:
    async def history(self, ticker, today):
        return [{"date": "2026-11-05", "ratio": 0.3, "short": 3, "total": 10}] * 20


def _fake(chains=None, interest=None, fail=None):
    calls = {"chains": 0}

    async def get_chains(ticker, today):
        calls["chains"] += 1
        if fail:
            raise fail
        return chains

    p = pz.Positioning(FakeShortVolume(), chains=get_chains,
                       interest=lambda t: interest, clock=lambda: 0.0)
    return p, calls


def test_positioning_combines_caches_and_skips_non_us():
    chains = {"spot": 100.0, "iv30": 50.0, "source": "cboe",
              "chains": [(TODAY + timedelta(days=7), [_row(110, 100)], [_row(90, 300)])]}
    p, calls = _fake(chains, {"pct_float": 15.0, "days_to_cover": 3.0, "change_pct": 5.0, "as_of": "2026-10-15"})
    got = asyncio.run(p.get("nvda", None, TODAY))
    assert got["supported"] and got["options"]["spot"] == 100.0 and got["options"]["source"] == "cboe"
    assert got["options"]["lean"] == "put_heavy" and got["short"]["crowded"]
    assert "풋 쪽에 몰림" in got["verdict"] and "숏 스퀴즈" in got["verdict"]
    assert got["sources"] == ["finra", "yahoo", "cboe"]
    asyncio.run(p.get("NVDA", None, TODAY))
    assert calls["chains"] == 1                                # cached
    note = pz.prompt_note(got)
    assert "put/call OI 3.0" in note and "Call walls above: 110 (+10.0%)" in note and "CROWDED" in note
    kr = asyncio.run(p.get("005930.KS", 70000, TODAY))
    assert kr["supported"] is False and kr["reason"] and pz.prompt_note(kr) is None


def test_a_partial_answer_is_reported_and_not_cached():
    p, calls = _fake(fail=ConnectionError("cboe down"))
    got = asyncio.run(p.get("NVDA", 100.0, TODAY))
    assert got["options"] is None and got["short"]["volume_ratio"] == 30.0
    assert got["errors"] == ["options: ConnectionError"]
    asyncio.run(p.get("NVDA", 100.0, TODAY))
    assert calls["chains"] == 2


@pytest.mark.parametrize("ticker,ok", [("NVDA", True), ("BRK.B", True), ("005930.KS", False), ("^GSPC", False),
                                       ("CL=F", False), ("BTC-USD", False)])
def test_us_ticker_rule(ticker, ok):
    assert pz.is_us_ticker(ticker) is ok


# --- API -----------------------------------------------------------------------------------

def test_positioning_api(db_url, monkeypatch):
    from tests.test_api import OWNER, PASSWORD, _client

    p, _ = _fake({"spot": 100.0, "iv30": None, "source": "cboe",
                  "chains": [(date.today() + timedelta(days=7), [_row(110, 100)], [_row(90, 100)])]}, None)
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
