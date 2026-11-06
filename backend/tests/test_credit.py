import asyncio
import json
from datetime import date, timedelta

import httpx

from desk import credit


def _days(n: int, end: date = date(2026, 11, 5)) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out   # newest first, like KOFIA


def _tables(n=260, last_ratio_bump=0.0, forced_pct=0.9):
    funds, loans = [], []
    for i, d in enumerate(_days(n)):
        deposits = 88_000_000 + i * 10_000
        ratio = 0.30 + (0.0001 * (n - i)) + (last_ratio_bump if i == 0 else 0)   # rising over the year
        loans_total = deposits * ratio
        key = d.strftime("%Y%m%d")
        funds.append({"TMPV1": key, "TMPV2": deposits, "TMPV3": 1, "TMPV4": 1, "TMPV5": 900_000, "TMPV6": 8_420,
                      "TMPV7": forced_pct if i == 0 else 0.5})
        loans.append({"TMPV1": key, "TMPV2": loans_total, "TMPV3": loans_total * 0.78, "TMPV4": loans_total * 0.22,
                      "TMPV5": 1, "TMPV6": 1, "TMPV7": 0, "TMPV8": 0, "TMPV9": 1})
    return funds, loans


def test_merge_keeps_common_days_oldest_first_with_the_ratio():
    funds = [{"TMPV1": "20261105", "TMPV2": 88_533_870, "TMPV5": 904_064, "TMPV6": 8_420, "TMPV7": 0.9},
             {"TMPV1": "20261104", "TMPV2": 89_227_320, "TMPV5": 896_804, "TMPV6": 3_900, "TMPV7": 0.4},
             {"TMPV1": "20261103", "TMPV2": 0}]
    loans = [{"TMPV1": "20261105", "TMPV2": 28_771_509, "TMPV3": 22_320_468, "TMPV4": 6_451_041},
             {"TMPV1": "20261104", "TMPV2": 28_644_621, "TMPV3": 22_163_004, "TMPV4": 6_481_617},
             {"TMPV1": "20261103", "TMPV2": 28_000_000}, {"TMPV1": "20261031", "TMPV2": 1}]
    rows = credit.merge(funds, loans)
    assert [r["date"] for r in rows] == ["2026-11-04", "2026-11-05"]           # no deposits on 11-03, no funds on 10-31
    assert rows[-1]["ratio"] == 32.5 and rows[-1]["forced_sale_pct"] == 0.9


def test_summary_percentile_level_changes_and_stress():
    series = credit.merge(*_tables())
    s = credit.summarise(series)
    assert s["date"] == "2026-11-05" and s["ratio_percentile_1y"] == 100 and s["level"] == "hot"
    assert s["ratio_change_1w"] > 0 and s["ratio_change_1m"] > s["ratio_change_1w"]
    assert s["forced_sale_eok"] == 84.2 and not s["stress"]
    assert s["verdict"] == "빚투 과열 구간(1년 최고 수준)"
    cold = credit.summarise(credit.merge(*_tables(last_ratio_bump=-0.2, forced_pct=3.1)))
    assert cold["level"] == "cold" and cold["stress"] and "반대매매 비중 3.1%" in cold["verdict"]
    assert cold["verdict"].startswith("빚투 위축 구간(1년 최저 수준)")
    assert credit.summarise([]) is None


def test_refresh_keeps_the_last_series_on_failure_and_view_cuts_days():
    funds, loans = _tables()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)["dmSearch"]
        calls.append(body["OBJ_NM"])
        assert request.headers["referer"].startswith("https://freesis.kofia.or.kr")
        return httpx.Response(200, json={"ds1": funds if body["OBJ_NM"] == credit.FUNDS else loans})

    cache = credit.CreditCache()

    async def go(h):
        async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as client:
            return await cache.refresh(client, today=date(2026, 11, 6))
    assert asyncio.run(go(handler)) and calls == [credit.FUNDS, credit.CREDIT]
    kept = list(cache.series)
    assert not asyncio.run(go(lambda r: httpx.Response(500))) and cache.error == "HTTPStatusError"
    assert cache.series == kept
    assert not asyncio.run(go(lambda r: httpx.Response(200, json={"oops": 1}))) and cache.error == "TypeError"
    view = cache.view(30)
    assert view["summary"]["date"] == "2026-11-05" and view["source"] == "kofia"
    assert all(r["date"] >= "2026-10-06" for r in view["series"]) and len(view["series"]) <= 23
    assert view["series"][-1]["deposits_trillion"] == 88.0


def test_prompt_note():
    assert credit.prompt_note(credit.CreditCache()) is None
    note = credit.prompt_note(credit.CreditCache(series=credit.merge(*_tables())))
    assert "Korean retail leverage" in note and "percentile" in note and "Forced sales 8.42bn KRW" in note
    assert credit.is_korean("005930.KS") and credit.is_korean("035720.kq") and not credit.is_korean("NVDA")


def test_credit_api(db_url, monkeypatch):
    from tests.test_api import OWNER, PASSWORD, _client

    monkeypatch.setattr(credit, "CACHE", credit.CreditCache(series=credit.merge(*_tables())))
    with _client(db_url, login=False) as client:
        assert client.get("/api/market/credit").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        body = client.get("/api/market/credit", params={"days": 9999}).json()
        assert body["summary"]["level"] == "hot" and len(body["series"]) == 260
        assert len(client.get("/api/market/credit", params={"days": 1}).json()["series"]) <= 6   # at least 7 days


def test_kofia_string_numbers_and_gaps():
    funds = [{"TMPV1": "20261105", "TMPV2": "88,533,870", "TMPV5": "904064", "TMPV6": "8420", "TMPV7": ""},
             {"TMPV1": "20261104", "TMPV2": "NaN"}, {"TMPV1": "20261103", "TMPV2": "-"}]
    loans = [{"TMPV1": "20261105", "TMPV2": "28771509", "TMPV3": "22320468", "TMPV4": "6451041"},
             {"TMPV1": "20261104", "TMPV2": "28644621"}, {"TMPV1": "20261103", "TMPV2": "1"}]
    rows = credit.merge(funds, loans)
    assert [r["date"] for r in rows] == ["2026-11-05"] and rows[0]["ratio"] == 32.5
    s = credit.summarise(rows)
    assert s["ratio_change_1w"] is None and s["credit_change_1m_pct"] is None   # too little history
    note = credit.prompt_note(credit.CreditCache(series=rows))
    assert "None" not in note and "Change:" not in note and "Forced sales 8.42bn KRW" in note


def test_mid_range_percentile_and_limits():
    rows = [{"date": (date(2026, 1, 1) + timedelta(days=i)).isoformat(), "ratio": float(r), "credit": 1.0,
             "credit_kospi": 1.0, "credit_kosdaq": 0.0, "deposits": 1.0, "forced_sale": 0, "forced_sale_pct": 0.1}
            for i, r in enumerate([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])]
    assert credit.summarise(rows[:8])["ratio_percentile_1y"] == 100
    mid = credit.summarise(rows + [{**rows[4], "date": "2026-01-20"}])           # ties count: 5 of 11 at or below
    assert mid["ratio_percentile_1y"] == 55 and mid["level"] == "normal"
    hot = credit.summarise(rows[:5] + [{**rows[3], "date": "2026-01-20"}])      # 5 of 6 -> 83
    assert hot["level"] == "hot" and hot["verdict"] == "빚투 과열 구간(1년 중 상위 17%)"


def test_an_empty_or_failed_cache_still_answers():
    view = credit.CreditCache(error="HTTPStatusError").view()
    assert view == {"summary": None, "series": [], "source": "kofia", "refreshed_at": None, "error": "HTTPStatusError"}


def test_the_credit_note_reaches_korean_same_day_runs_only(db_url):
    import time
    from datetime import timedelta as td

    from fastapi.testclient import TestClient

    from desk.api import create_app
    from desk.auth import Auth
    from desk.db import make_engine
    from desk.runner import make_fake_runner
    from desk.settings import Settings

    rendered = []
    fake = make_fake_runner(0)

    def runner(request, emit, portfolio):
        rendered.append((request.ticker, portfolio.render(request.ticker) if portfolio is not None else ""))
        return fake(request, emit, portfolio)

    app = create_app(Settings(database_url=db_url, fake_runs=True, fake_delay=0), runner=runner)
    engine = make_engine(db_url)
    Auth(engine, td(hours=1), td(hours=1)).create_user("owner", "correct horse battery")
    engine.dispose()
    with TestClient(app, base_url="https://testserver") as client:
        client.post("/api/auth/login", json={"username": "owner", "password": "correct horse battery"})
        client.app.state.manager.credit = lambda: "CREDIT NOTE: ratio 32.5%"

        def run(body):
            run_id = client.post("/api/runs", json=body).json()["id"]
            for _ in range(200):
                if client.get(f"/api/runs/{run_id}").json()["status"] in ("done", "failed"):
                    return
                time.sleep(0.02)

        run({"ticker": "005930.KS"})
        run({"ticker": "NVDA"})
        run({"ticker": "000660.KS", "trade_date": "2026-01-05"})
    notes = dict(rendered)
    assert "CREDIT NOTE" in notes["005930.KS"]
    assert "CREDIT NOTE" not in notes["NVDA"] and "CREDIT NOTE" not in notes["000660.KS"]
