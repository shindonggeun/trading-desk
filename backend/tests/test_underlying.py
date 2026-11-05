from datetime import timedelta

import pytest

from desk import underlying as under
from desk.levels import fill_levels
from desk.plan import parse_plan

ANCHOR = {"source": "close", "product": 60.0, "underlying": 450.0}


def _decision(summary: str, rating: str = "Buy") -> dict:
    return {"final_rating": rating, "trader_investment_plan": "**Action**: Buy",
            "final_trade_decision": f"**Rating**: {rating}\n\n**Executive Summary**: {summary}\n\n**Price Target**: 75"}


def test_underlying_labels_are_read_and_never_taken_for_the_products_levels():
    plan = parse_plan(_decision("SOXX 지지에서 진입. Entry Price: 60 / Stop Loss: 55 / Price Target: 75 / "
                                "Underlying Entry Price: 450 / Underlying Stop Loss: 440 / Underlying Price Target: 480"))
    assert (plan["entry_price"], plan["stop_loss"], plan["price_target"]) == (60, 55, 75)
    assert plan["underlying_levels"] == {"entry_price": 450, "stop_loss": 440, "price_target": 480}
    # only the underlying given: the product's entry must not pick up 450 from the prose
    only = parse_plan(_decision("진입은 SOXX 기준. Underlying Entry: 450 / Underlying Stop: 440 / Underlying Target: 480"))
    assert only["entry_price"] is None and only["stop_loss"] is None
    assert "underlying_levels" not in parse_plan(_decision("Entry Price: 60 / Stop Loss: 55"))


def test_a_3x_product_gets_missing_levels_converted_both_ways():
    plan = {"bias": "long", "entry_price": None, "stop_loss": 57.0, "price_target": None,
            "underlying_levels": {"entry_price": 450.0, "price_target": 480.0}}
    product, sub = under.split(plan, "SOXX", 3.0, False, ANCHOR)
    assert product["entry_price"] == 60.0 and product["price_target"] == 72.0          # +6.67% x 3 = +20%
    assert sub["stop_loss"] == 442.5                                                    # -5% / 3
    assert product["levels_source"] == {"entry_price": "converted", "stop_loss": "model", "price_target": "converted"}
    assert sub["levels_source"]["stop_loss"] == "converted" and sub["bias"] == "long"
    assert product["conversion"]["anchor"] == ANCHOR and "underlying_levels" not in product
    assert "levels_mismatch" not in product


def test_an_inverse_product_flips_the_underlying_side():
    plan = {"bias": "long", "entry_price": 20.0, "stop_loss": 18.0, "price_target": 26.0}
    anchor = {"source": "live", "product": 20.0, "underlying": 450.0}
    _, sub = under.split(plan, "SOXX", -3.0, False, anchor)
    assert sub["bias"] == "short"
    assert sub["entry_price"] == 450.0 and sub["stop_loss"] == 465.0 and sub["price_target"] == 405.0
    assert sub["stop_loss"] > sub["entry_price"] > sub["price_target"]


def test_no_anchor_means_no_conversion_and_impossible_prices_are_dropped():
    plan = {"bias": "long", "entry_price": None, "stop_loss": None, "price_target": None,
            "underlying_levels": {"stop_loss": 250.0}}
    product, _ = under.split(plan, "SOXX", 3.0, False, None)
    assert product["stop_loss"] is None and "conversion" not in product
    crashed, _ = under.split(plan, "SOXX", 3.0, False, ANCHOR)        # SOXX -44% x 3 < -100%
    assert crashed["stop_loss"] is None


def test_anchors_pair_like_with_like():
    sr = {"last_close": 60.0, "as_of": "2026-11-04T04:00:00+00:00"}
    sr_u = {"last_close": 450.0, "as_of": "2026-11-04T04:00:00+00:00", "ticker": "SOXX"}
    live, live_u = {"price": 62.0, "source": "toss"}, {"price": 455.0, "source": "toss"}
    assert under.anchors(live, live_u, sr, sr_u) == {"source": "live", "product": 62.0, "underlying": 455.0}
    assert under.anchors(live, None, sr, sr_u)["source"] == "close"           # never one live, one close
    assert under.anchors({"price": 6.0, "source": "toss"}, live_u, sr, sr_u)["source"] == "close"  # split-like
    assert under.anchors(live, {**live_u, "source": "yahoo"}, sr, sr_u)["source"] == "close"     # two feeds
    assert under.anchors(live, live_u, sr, None) is None
    assert under.anchors(None, None, sr, {**sr_u, "as_of": "2026-11-03T04:00:00+00:00"}) is None  # other day
    btc = {"last_close": 100000.0, "as_of": "2026-11-04T00:00:00+00:00", "ticker": "BTC-USD"}
    assert under.anchors(None, None, sr, btc) is None                          # 24/7: no shared close


def test_atr_fill_keeps_the_converted_marker():
    plan = {"bias": "long", "entry_price": 60.0, "stop_loss": None, "price_target": 72.0,
            "levels_source": {"entry_price": "converted", "price_target": "model"}}
    filled = fill_levels(plan, "SOXL", "2026-11-04", lambda *_: (60.0, 4.0))
    assert filled["levels_source"] == {"entry_price": "converted", "stop_loss": "atr", "price_target": "model"}


def test_prompt_note_asks_for_both_sets_and_explains_inverse():
    sr_u = {"reference_price": 450.0, "atr": 12.0,
            "levels": [{"kind": "resistance", "price": 470.0, "timeframe": "1d"}]}
    note = under.prompt_note("soxs", "SOXX", -3.0, False, sr_u, None)
    assert "-3x daily product on SOXX" in note and "a long SOXS is a short on SOXX" in note
    assert "Underlying Entry: <SOXX>" in note and "R 470.0 (1d)" in note
    assert "proxy" in under.prompt_note("GDXU", "GDX", 3.0, True, None, None)
    assert under.leverage_of("aapl") is None and under.leverage_of("SOXL") == ("SOXX", 3.0, False)


def test_a_soxl_run_carries_both_sides(db_url, monkeypatch):
    from fastapi.testclient import TestClient

    from desk import api, runner
    from desk.auth import Auth
    from desk.db import make_engine
    from desk.runner import RunResult
    from desk.settings import Settings
    from tests.test_api import OWNER, PASSWORD, _wait_done

    def sr_for(_prices, ticker, _day, price=None):
        close, atr, level = {"SOXL": (60.0, 4.0, 66.0), "SOXX": (450.0, 12.0, 470.0)}[ticker]
        return {"reference_price": close, "reference": "close", "last_close": close, "atr": atr,
                "levels": [{"kind": "resistance", "price": level, "timeframe": "1d", "basis": "swing", "strength": 2}]}

    async def fake_sr(*args, **kwargs):
        return sr_for(*args, **kwargs)

    monkeypatch.setattr(runner, "support_resistance", fake_sr)
    seen = []

    def run(request, emit, portfolio):
        seen.append(portfolio.render(request.ticker))
        return RunResult(final_state=_decision(
            "Underlying Entry: 450 / Underlying Stop: 438 / Underlying Target: 480"))

    class Prices:
        async def quote(self, _ticker):
            raise AssertionError("past date: no quotes")

    client = TestClient(api.create_app(Settings(database_url=db_url, fake_delay=0), runner=run, prices=Prices()),
                        base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    with client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        done = _wait_done(client, client.post("/api/runs", json={"ticker": "SOXL", "trade_date": "2026-11-02"}).json()["id"])
    plan = done["plan"]
    assert "SOXL is a +3x daily product on SOXX" in seen[0] and "R 470.0 (1d)" in seen[0]
    assert plan["entry_price"] == 60.0 and plan["stop_loss"] == 55.2 and plan["price_target"] == 75   # 75: model
    assert plan["levels_source"] == {"entry_price": "converted", "stop_loss": "converted", "price_target": "model"}
    sub = plan["underlying"]
    assert sub["ticker"] == "SOXX" and sub["entry_price"] == 450 and sub["price_target"] == 480
    assert sub["support_resistance"]["last_close"] == 450.0 and sub["reward_risk"] == 2.5
    assert plan["conversion"]["anchor"]["source"] == "close"
    assert "levels_mismatch" not in plan          # 75 vs 480 -> 72: within 10%
    assert pytest.approx(plan["support_resistance"]["atr"]) == 4.0


def test_two_sets_that_disagree_are_kept_and_flagged():
    plan = {"bias": "long", "entry_price": 60.0, "stop_loss": 55.0, "price_target": 90.0,
            "underlying_levels": {"entry_price": 450.0, "price_target": 480.0}}
    product, sub = under.split(plan, "SOXX", 3.0, False, ANCHOR)
    assert product["price_target"] == 90.0 and sub["price_target"] == 480.0
    assert product["levels_mismatch"] == {"price_target": {"product": 90.0, "underlying": 480.0, "implied_product": 72.0}}


def test_the_summary_keeps_what_the_pm_wrote_and_label_variants_parse():
    text = ("SOXX 450달러 지지에서 진입. Entry Price: 60 / Stop Loss: 55 / Price Target: 75 / "
            "Underlying SOXX Entry Price: 450 / Underlying Stop: 440 / Underlying Target Price: 480")
    plan = parse_plan(_decision(text))
    assert plan["executive_summary"] == text
    assert plan["entry_price"] == 60 and plan["underlying_levels"] == {"entry_price": 450, "stop_loss": 440,
                                                                       "price_target": 480}


def test_the_pms_underlying_level_beats_the_traders_draft():
    state = _decision("리스크 토론 후 SOXX 기준으로 조정. Underlying Entry: 444 / Underlying Stop: 432")
    state["trader_investment_plan"] = "**Action**: Buy\n\n**Entry Price**: 62\n\n**Stop Loss**: 57"
    plan = parse_plan(state)
    assert plan["entry_price"] == 62 and plan["levels_origin"]["entry_price"] == "trader"
    product, sub = under.split(plan, "SOXX", 3.0, False, ANCHOR)
    assert product["entry_price"] == 57.6 and product["levels_source"]["entry_price"] == "converted"
    assert product["trader_levels"] == {"entry_price": 62.0, "stop_loss": 57.0}
    assert sub["entry_price"] == 444 and "levels_mismatch" not in product


def _app(db_url, run, prices):
    from fastapi.testclient import TestClient

    from desk import api
    from desk.auth import Auth
    from desk.db import make_engine
    from desk.settings import Settings
    from tests.test_api import OWNER, PASSWORD

    client = TestClient(api.create_app(Settings(database_url=db_url, fake_delay=0), runner=run, prices=prices),
                        base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    return client


def test_a_same_day_inverse_run_anchors_on_live_quotes_and_flips_the_underlying(db_url, monkeypatch):
    from datetime import date

    from desk import runner
    from desk.prices import Quote
    from desk.runner import RunResult
    from tests.test_api import OWNER, PASSWORD, _wait_done

    async def fake_sr(_prices, ticker, _day, price=None):
        close, atr = {"SOXS": (20.0, 1.5), "SOXX": (450.0, 12.0)}[ticker]
        return {"ticker": ticker, "reference_price": price or close, "reference": "live", "last_close": close,
                "atr": atr, "as_of": "2026-11-04T04:00:00+00:00", "levels": []}

    class Prices:
        async def quote(self, ticker):
            return Quote(ticker, {"SOXS": 21.0, "SOXX": 440.0}[ticker], "USD", "toss", "2026-11-05T15:00:00+00:00")

    monkeypatch.setattr(runner, "support_resistance", fake_sr)

    def run(request, emit, portfolio):
        return RunResult(final_state=_decision("SOXS long. Entry Price: 21 / Stop Loss: 19.5", rating="Buy"))

    with _app(db_url, run, Prices()) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        rid = client.post("/api/runs", json={"ticker": "SOXS", "trade_date": date.today().isoformat()}).json()["id"]
        plan = _wait_done(client, rid)["plan"]
    sub = plan["underlying"]
    assert plan["conversion"]["anchor"] == {"source": "live", "product": 21.0, "underlying": 440.0}
    assert sub["bias"] == "short" and sub["entry_price"] == 440.0 and sub["stop_loss"] > sub["entry_price"]
    assert sub["live_price"]["price"] == 440.0 and sub["entry_vs_live"]["kind"] == "now"


def test_without_the_underlyings_bars_the_models_underlying_levels_still_stand(db_url, monkeypatch):
    from desk import runner
    from desk.runner import RunResult
    from tests.test_api import OWNER, PASSWORD, _wait_done

    async def only_soxl(_prices, ticker, _day, price=None):
        if ticker == "SOXX":
            raise TimeoutError("slow")
        return {"ticker": ticker, "reference_price": 60.0, "reference": "close", "last_close": 60.0, "atr": 4.0,
                "as_of": "2026-11-02T04:00:00+00:00", "levels": []}

    class Prices:
        async def quote(self, _ticker):
            raise AssertionError("past date")

    monkeypatch.setattr(runner, "support_resistance", only_soxl)
    monkeypatch.setattr("desk.levels.last_close_and_atr", lambda *_: (450.0, 12.0))

    def run(request, emit, portfolio):
        return RunResult(final_state=_decision(
            "Entry Price: 60 / Stop Loss: 55 / Underlying Entry: 450 / Underlying Stop: 440 / Underlying Target: 480"))

    with _app(db_url, run, Prices()) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        rid = client.post("/api/runs", json={"ticker": "SOXL", "trade_date": "2026-11-02"}).json()["id"]
        plan = _wait_done(client, rid)["plan"]
    assert plan["underlying"]["entry_price"] == 450 and "support_resistance" not in plan["underlying"]
    assert "conversion" not in plan and "underlying_levels" not in plan
