"""#443: KOSPI200 futures day / night session price and minute bars (KIS F / CM)."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta

import pytest

from desk import futures_quote as fq
from desk.futures_flow import KST, KisError
from tests.test_api import OWNER, PASSWORD, _client

HOLIDAY = date(2026, 11, 18)          # a made-up Wednesday holiday for the calendar tests


def is_open(_market, d):
    return d.weekday() < 5 and d != HOLIDAY


def at(day: str, hm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hm}:00").replace(tzinfo=KST)


@pytest.mark.parametrize(("when", "now", "shown", "day"), [
    (at("2026-11-16", "08:44"), "closed", "night", "2026-11-13"),   # Monday before the open: Friday's night
    (at("2026-11-16", "08:45"), "day", "day", "2026-11-16"),
    (at("2026-11-16", "15:44"), "day", "day", "2026-11-16"),
    (at("2026-11-16", "15:45"), "closed", "day", "2026-11-16"),     # the day's close until the night opens
    (at("2026-11-16", "17:59"), "closed", "day", "2026-11-16"),
    (at("2026-11-16", "18:00"), "night", "night", "2026-11-16"),
    (at("2026-11-17", "05:59"), "night", "night", "2026-11-16"),    # past midnight: still Monday's night
    (at("2026-11-17", "06:00"), "closed", "night", "2026-11-16"),
    (at("2026-11-14", "05:00"), "night", "night", "2026-11-13"),    # Friday's night runs into Saturday
    (at("2026-11-15", "12:00"), "closed", "night", "2026-11-13"),   # Sunday
    (at("2026-11-18", "10:00"), "closed", "night", "2026-11-17"),   # holiday: no day session
    (at("2026-11-18", "20:00"), "closed", "night", "2026-11-17"),   # and no night one
])
def test_the_session_now_and_the_one_shown(when, now, shown, day):
    s = fq.futures_session(when, is_open)
    assert (s["now"], s["shown"], s["date"]) == (now, shown, day)
    assert s["end"] - s["start"] == (timedelta(hours=7) if shown == "day" else timedelta(hours=12))


# the 2026-11-16 22:23 KST night answer (#443 probe), numbers as KIS sends them
NIGHT = {"futs_prpr": "782.75", "futs_prdy_vrss": "-6.60", "futs_prdy_ctrt": "-0.84", "futs_oprc": "795.70",
         "futs_hgpr": "796.30", "futs_lwpr": "779.45", "acml_vol": "8679", "hts_otst_stpl_qty": "158173",
         "otst_stpl_qty_icdc": "517", "futs_sdpr": "789.35", "hts_kor_isnm": "F 202612"}


def test_the_night_quote_is_against_the_days_close_and_the_day_against_the_settlement():
    q = fq.normalize_quote(NIGHT, "night")
    assert (q["price"], q["change"], q["change_pct"], q["prev_close"]) == (782.75, -6.6, -0.84, 789.35)
    assert (q["volume"], q["open_interest"], q["open_interest_change"], q["name"]) == (8679, 158173, 517, "F 202612")
    assert q["prev_close_basis"] == "주간 종가" and fq.normalize_quote(NIGHT, "day")["prev_close_basis"] == "전일 정산가"


def _row(t: datetime, c: float, v: int = 1, label_day: date | None = None) -> dict:
    return {"stck_bsop_date": (label_day or t.date()).strftime("%Y%m%d"), "stck_cntg_hour": t.strftime("%H%M%S"),
            "futs_oprc": str(c), "futs_hgpr": str(c + 1), "futs_lwpr": str(c - 1), "futs_prpr": str(c), "cntg_vol": str(v)}


def test_a_night_bar_after_midnight_labelled_with_the_opening_day_moves_to_the_next_day():
    s = fq.last_session("night", at("2026-11-17", "01:00"), is_open)
    late = _row(at("2026-11-17", "00:30"), 780.0, label_day=date(2026, 11, 16))
    assert fq.bar_time(late, s) == at("2026-11-17", "00:30")
    assert fq.bar_time(_row(at("2026-11-17", "00:31"), 780.0), s) == at("2026-11-17", "00:31")   # either label works


def test_minute_bars_come_oldest_first_and_resample_from_the_sessions_start():
    s = fq.last_session("day", at("2026-11-16", "09:00"), is_open)
    rows = [_row(at("2026-11-16", "08:45") + timedelta(minutes=m), 800 + m, v=10) for m in range(12)][::-1]
    rows.append(_row(at("2026-11-16", "08:44"), 1.0))                  # before the open: left out
    one = fq.normalize_bars(rows, s)
    assert [b["c"] for b in one] == [800 + m for m in range(12)]
    five = fq.resample(one, 5, s["start"])
    assert [b["t"][11:16] for b in five] == ["08:45", "08:50", "08:55"]
    assert (five[0]["o"], five[0]["c"], five[0]["h"], five[0]["l"], five[0]["v"]) == (800, 804, 805, 799, 50)


class FakeRest:
    """KIS stand-in: a quote per division and minute pages of 102 newest-first rows."""

    def __init__(self, fail: bool = False):
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail

    async def get(self, path, tr_id, params):
        self.calls.append((tr_id, params))
        if self.fail:
            raise KisError(f"{tr_id}: HTTP 500 EGW00201")
        if tr_id == fq.PRICE_TR:
            return {"rt_cd": "0", "output1": NIGHT if params["FID_COND_MRKT_DIV_CODE"] == "CM" else {**NIGHT, "futs_prpr": "789.35"}}
        end = datetime.strptime(params["FID_INPUT_DATE_1"] + params["FID_INPUT_HOUR_1"], "%Y%m%d%H%M%S").replace(tzinfo=KST)
        end = end.replace(second=0)
        return {"rt_cd": "0", "output2": [_row(end - timedelta(minutes=i), 780.0) for i in range(fq.PAGE_ROWS)]}


def test_the_night_candles_page_back_to_18_00_on_division_cm():
    rest = FakeRest()
    quotes = fq.FuturesQuote(rest, now=lambda: at("2026-11-16", "22:23"), is_open=is_open)
    out = asyncio.run(quotes.candles())
    assert out["session"] == "night" and out["market"] == "night" and out["interval"] == "1m"
    assert out["bars"][0]["t"].startswith("2026-11-16T18:00") and out["bars"][-1]["t"].startswith("2026-11-16T22:23")
    assert len(out["bars"]) == 4 * 60 + 23 + 1
    assert {p["FID_COND_MRKT_DIV_CODE"] for _, p in rest.calls} == {"CM"} and len(rest.calls) == 3   # 102 x 3 >= 264
    q = asyncio.run(quotes.quote())
    assert (q["price"], q["prev_close_basis"], q["contract"]["code"]) == (782.75, "주간 종가", "A01612")
    day = asyncio.run(quotes.quote("day"))
    assert (day["session"], day["market"], day["price"]) == ("day", "night", 789.35)


def test_a_failed_refresh_hands_back_the_last_answer_marked_stale():
    clock = [0.0]
    rest = FakeRest()
    quotes = fq.FuturesQuote(rest, now=lambda: at("2026-11-16", "22:23"), clock=lambda: clock[0], is_open=is_open)
    first = asyncio.run(quotes.quote())
    rest.fail, clock[0] = True, 100.0
    again = asyncio.run(quotes.quote())
    assert again["stale"] is True and again["price"] == first["price"] and "EGW00201" in again["error"]
    with pytest.raises(KisError):
        asyncio.run(fq.FuturesQuote(FakeRest(fail=True), now=lambda: at("2026-11-16", "22:23"), is_open=is_open).quote())


def test_the_routes_need_a_session_and_say_unsupported_without_kis(db_url):
    with _client(db_url, login=False) as client:
        assert client.get("/api/market/futures/quote").status_code == 401
        assert client.get("/api/market/futures/candles").status_code == 401
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        q = client.get("/api/market/futures/quote").json()
        assert q["supported"] is False and q["error"]
        assert client.get("/api/market/futures/candles?interval=5m").json()["bars"] == []
        assert client.get("/api/market/futures/quote?session=evening").status_code == 422
        assert client.get("/api/market/futures/candles?interval=2m").status_code == 422
