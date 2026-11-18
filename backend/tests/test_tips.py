"""#330: rate / dollar regimes, the tips that go with them and their backtest evidence."""

from __future__ import annotations

import pytest

from desk import primary_trend, tips, tips_bt
from tests.test_api import _client


@pytest.fixture(autouse=True)
def _no_index_fetch(monkeypatch):
    """#460: /api/tips also reads the indices' trend - never from Yahoo in these tests."""
    monkeypatch.setattr(primary_trend.Cache, "view", lambda self: [])


def _rows(values: list[float], start: int = 0) -> list[dict]:
    return [{"day": f"2020-{1 + (start + i) // 28:02d}-{1 + (start + i) % 28:02d}", "close": v} for i, v in enumerate(values)]


def _series(irx_from: float, irx_to: float, tnx_from: float, tnx_to: float, dxy_pct: float, qqq_pct: float,
            spike: float = 0.0) -> tips.Series:
    n = tips.TREND + 1

    def path(a: float, b: float) -> list[float]:
        return [a + (b - a) * i / (n - 1) for i in range(n)]

    tnx = path(tnx_from, tnx_to - spike) + [tnx_to]
    return {"^IRX": _rows(path(irx_from, irx_to) + [irx_to]), "^TNX": _rows(tnx),
            "DX-Y.NYB": _rows(path(100, 100 * (1 + dxy_pct / 100)) + [100 * (1 + dxy_pct / 100)]),
            "QQQ": _rows(path(300, 300 * (1 + qqq_pct / 100)) + [300 * (1 + qqq_pct / 100)])}


def test_the_regime_reads_the_fed_the_ten_year_the_dollar_and_a_flat_market():
    g = tips.regime(_series(3.0, 3.6, 3.5, 4.1, 4.0, 2.0))
    assert (g["rates"], g["yield10"], g["dollar"], g["sideways"]) == ("hiking", "rising", "strong", True)
    assert 0.55 < g["values"]["bill_3m_change_pp"] <= 0.6
    g = tips.regime(_series(5.0, 4.5, 4.5, 4.0, -4.0, 12.0))
    assert (g["rates"], g["yield10"], g["dollar"], g["sideways"]) == ("cutting", "falling", "weak", False)
    assert tips.regime(_series(4.0, 4.1, 4.0, 4.6, 0, 0, spike=0.5))["yield_spike"] is True
    short = tips.regime({"^IRX": _rows([1.0, 2.0])})
    assert short["rates"] is None and short["dollar"] is None and short["sideways"] is None


EVIDENCE = {"as_of": "2026-11-13", "tips": {
    "gold-miners-tightening": {"weak": False, "since": "2000-04-04", "split": "2015",
                               "held_out": {"60": {"n": 195, "median": -8.0, "all_median": 1.5, "win": 30.0}}},
    "commodities-strong-dollar": {"weak": True, "held_out": {"60": {"n": 500, "median": -1.0, "all_median": 0.5}}}}}


def test_tips_light_up_in_their_regime_and_a_weak_one_says_so():
    g = tips.regime(_series(3.0, 3.6, 3.5, 4.1, 4.0, 2.0))
    out = tips.view(g, EVIDENCE)
    rows = {r["id"]: r for r in out["tips"]}
    assert rows["gold-miners-tightening"]["active"] and not rows["gold-miners-tightening"]["weak"]
    assert rows["commodities-strong-dollar"]["active"] and rows["commodities-strong-dollar"]["weak"]
    assert rows["growth-yield-spike"]["active"] is False and rows["growth-yield-spike"]["known"] is True
    assert rows["leverage-decay-sideways"]["weak"] is True          # no evidence = weak
    assert [r["id"] for r in tips.view(g, EVIDENCE, "gdx")["tips"]] == ["gold-miners-tightening"]
    assert [r["id"] for r in tips.view(g, EVIDENCE, "GDXU")["tips"]] == ["gold-miners-tightening", "leverage-decay-sideways"]
    assert tips.view(g, EVIDENCE, "AAPL")["tips"] == []


def test_the_agents_note_carries_only_active_tips_with_evidence():
    hiking = tips.regime(_series(3.0, 3.6, 3.5, 4.1, 4.0, 2.0))
    note = tips.prompt_note("NUGT", hiking, EVIDENCE)
    assert note and "gold-miners-tightening" in note and "median -8.0% vs 1.5%" in note
    assert tips.prompt_note("UCO", hiking, EVIDENCE) is None        # active but weak
    calm = tips.regime(_series(4.0, 4.0, 4.0, 4.0, 0, 10))
    assert tips.prompt_note("NUGT", calm, EVIDENCE) is None
    assert tips.prompt_note("NUGT", None, EVIDENCE) is None


def test_the_cache_keeps_a_regime_and_retries_a_failure_soon():
    calls, now = [], [0.0]

    def fetch(symbols):
        calls.append(symbols)
        if len(calls) == 1:
            raise ConnectionError("yahoo")
        return _series(3.0, 3.6, 3.5, 4.1, 4.0, 2.0)

    c = tips.Cache(fetch, clock=lambda: now[0], evidence=EVIDENCE)
    assert c.view() is None and c.view() is None and len(calls) == 1
    now[0] = c.fail_ttl + 1
    assert c.view()["regime"]["rates"] == "hiking" and len(calls) == 2
    now[0] += c.ttl - 1
    c.view()
    assert len(calls) == 2


def test_the_backtest_compares_regime_days_with_all_days_in_both_periods():
    # a year of rising dollar (the proxy falls), then a year of falling dollar (the proxy rises)
    n = 4800
    days = [f"{2000 + i // 240}-{1 + (i % 240) // 20:02d}-{1 + i % 20:02d}" for i in range(n)]
    dollar, proxy = [], []
    d, p = 100.0, 100.0
    for i in range(n):
        strong = (i % 480) < 240
        d *= 1.001 if strong else 0.999
        p *= 0.998 if strong else 1.003
        dollar.append({"day": days[i], "close": d})
        proxy.append({"day": days[i], "close": p})
    series = {"DX-Y.NYB": dollar, "DBC": proxy}
    tip = next(t for t in tips.TIPS if t.id == "commodities-strong-dollar")
    regs = tips_bt.regimes(series, days)
    ev = tips_bt.evidence(tip, days, [r["close"] for r in proxy], regs)
    assert ev["train"]["60"]["n"] > 0 and ev["held_out"]["60"]["n"] > 0
    assert ev["train"]["60"]["vs_all"] < 0 and ev["held_out"]["60"]["vs_all"] < 0
    assert ev["weak"] is False and ev["train"]["episodes"] >= 2
    assert round(tips_bt.forward([100, 110, 99], 0, 2, "return"), 6) == -1.0
    assert round(tips_bt.forward([100, 110, 99], 0, 2, "decay"), 2) == -6.0    # 1.3 x 0.7 - 1 = -9%, less 3 x -1%


def test_the_shipped_evidence_covers_every_tip():
    ev = tips.load_evidence()
    assert set(ev["tips"]) == {t.id for t in tips.TIPS}
    for t in ev["tips"].values():
        assert {"train", "held_out", "weak", "since"} <= set(t)


def test_the_tips_api(db_url, monkeypatch):
    monkeypatch.setattr(tips.Cache, "regime", lambda self: tips.regime(_series(3.0, 3.6, 3.5, 4.1, 4.0, 2.0)))
    with _client(db_url) as client:
        out = client.get("/api/tips").json()
        assert out["regime"]["rates"] == "hiking" and len(out["tips"]) == len(tips.TIPS)
        assert out["etf_flow"] == [] and out["ktb"] is None            # #605 no KRX rows stored: said as nothing
        assert [t["id"] for t in client.get("/api/tips?ticker=gdx").json()["tips"]] == ["gold-miners-tightening"]
        assert client.get("/api/tips?ticker=../x").status_code == 422


def test_the_tips_api_without_the_series_is_503(db_url, monkeypatch):
    monkeypatch.setattr(tips.Cache, "regime", lambda self: None)
    with _client(db_url) as client:
        assert client.get("/api/tips").status_code == 503


def test_the_tips_api_is_closed_without_a_session(db_url):
    with _client(db_url, login=False) as client:
        assert client.get("/api/tips").status_code == 401


def test_a_partial_fetch_is_asked_again_soon_not_in_hours():
    calls, now = [], [0.0]

    def fetch(symbols):
        calls.append(1)
        full = _series(3.0, 3.6, 3.5, 4.1, 4.0, 2.0)
        return {k: v for k, v in full.items() if k != "^IRX"} if len(calls) == 1 else full

    c = tips.Cache(fetch, clock=lambda: now[0], evidence=EVIDENCE)
    assert c.regime()["rates"] is None
    now[0] = c.fail_ttl + 1
    assert c.regime()["rates"] == "hiking" and len(calls) == 2


def test_the_backtest_judges_a_day_on_the_bars_up_to_it_only():
    days = [f"2010-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(150)]
    base = {"DX-Y.NYB": [{"day": d, "close": 100 + i * 0.1} for i, d in enumerate(days)]}
    later = {"DX-Y.NYB": [dict(r, close=r["close"] * (3 if i > 100 else 1)) for i, r in enumerate(base["DX-Y.NYB"])]}
    a, b = tips_bt.regimes(base, days), tips_bt.regimes(later, days)
    assert a[:101] == b[:101] and a[120] != b[120]
