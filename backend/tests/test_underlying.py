import sys
from datetime import timedelta

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


# --- #82: the underlying is analysed, the leverage analyst calls the product ----------------------

def test_prompt_note_says_whose_call_it_is():
    note = under.prompt_note("soxs", "SOXX", -3.0, False)
    assert "This run analyses SOXX" in note and "-3x daily inverse product" in note
    assert "on SOXX prices only" in note and "Underlying Entry" not in note
    assert "proxy" in under.prompt_note("GDXU", "GDX", 3.0, True)
    assert under.leverage_of("aapl") is None and under.leverage_of("SOXL") == ("SOXX", 3.0, False)
    assert [under.flip_rating(r) for r in ("Buy", "Overweight", "Hold", "Underweight", "Sell")] == \
        ["Sell", "Underweight", "Hold", "Overweight", "Buy"]


def _ctx(**kw):
    from desk.leverage_analyst import LeverContext

    base = {"product": "SOXL", "base": "SOXX", "leverage": 3.0, "proxy": False, "horizon": "swing", "holding": "one to three weeks",
                "u_plan": {"rating": "Buy", "bias": "long", "entry_price": 450.0, "stop_loss": 438.0,
                        "price_target": 480.0},
                "u_decision": "**Rating**: Buy", "live": {"price": 60.0}, "live_u": {"price": 450.0},
                "sr": {"atr": 4.0, "levels": [{"kind": "support", "price": 57.0}]}, "sr_u": {"atr": 12.0, "levels": []},
                "beta": {"beta_20d": 2.99, "beta_60d": 2.98}, "daily_atr_u": 12.0}
    base.update(kw)
    return LeverContext(**base)


def test_the_analyst_prompt_carries_the_facts_and_the_inverse_rule():
    from desk.leverage_analyst import build_prompt, decay_per_day

    assert decay_per_day(_ctx()) == 0.083                   # (9-3)/2 x (12/450/1.6)^2
    assert decay_per_day(_ctx(leverage=-3.0)) == 0.167       # inverse 3x pays 6 sigma^2
    assert decay_per_day(_ctx(daily_atr_u=None)) is None     # intraday bars: no daily sigma
    prompt = build_prompt(_ctx())
    assert "turn it into a decision on SOXL".lower() in prompt.lower() and "S 57.0" in prompt and "20d 2.99" in prompt
    assert "**Entry Price**: <number>" in prompt and "about 0.083% per day" in prompt
    inverse = build_prompt(_ctx(product="SOXS", leverage=-3.0, intraday=True))
    assert "a bearish SOXX call is a reason to BUY SOXS" in inverse and "negligible within an intraday" in inverse


def _app(db_url, run, prices, analyst, monkeypatch):
    from fastapi.testclient import TestClient

    from desk import api
    from desk.auth import Auth
    from desk.db import make_engine
    from desk.settings import Settings
    from tests.test_api import OWNER, PASSWORD

    monkeypatch.setattr(api, "llm_analyst", analyst)   # the manager is built at startup
    app = api.create_app(Settings(database_url=db_url, fake_delay=0), runner=run, prices=prices)
    client = TestClient(app, base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    return client


def _run(db_url, monkeypatch, ticker, analyst, decision, day="2026-11-02", quotes=None, sr_fail=()):
    from desk import runner
    from desk.prices import Quote
    from desk.runner import RunResult
    from tests.test_api import OWNER, PASSWORD, _wait_done

    closes = {"SOXL": (60.0, 4.0, 66.0), "SOXS": (20.0, 1.5, 22.0), "SOXX": (450.0, 12.0, 470.0)}

    async def fake_sr(_prices, ticker, _day, price=None, horizon=None):
        if ticker in sr_fail:
            raise TimeoutError("slow")
        close, atr, level = closes[ticker]
        return {"ticker": ticker, "reference_price": price or close, "reference": "close", "last_close": close,
                "atr": atr, "as_of": "2026-11-02T04:00:00+00:00",
                "levels": [{"kind": "resistance", "price": level, "timeframe": "1d", "basis": "swing", "strength": 2}]}

    class Prices:
        async def quote(self, t):
            if not quotes:
                raise AssertionError("past date: no quotes")
            return Quote(t, quotes[t], "USD", "toss", "2026-11-05T15:00:00+00:00")

    monkeypatch.setattr(runner, "support_resistance", fake_sr)
    seen = []

    def run(request, emit, portfolio):
        seen.append((request.ticker, portfolio.render(request.ticker)))
        return RunResult(final_state=decision)

    with _app(db_url, run, Prices(), analyst, monkeypatch) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        rid = client.post("/api/runs", json={"ticker": ticker, "trade_date": day}).json()["id"]
        done = _wait_done(client, rid)
    return done, seen, None


def _soxx(rating: str, entry: float, stop: float, target: float) -> dict:
    """The underlying run's final state: the PM's levels on SOXX in the summary and the target field."""
    return {"final_rating": rating, "trader_investment_plan": "**Action**: " + rating,
            "final_trade_decision": f"**Rating**: {rating}\n\n**Executive Summary**: SOXX 판단. "
                                    f"Entry Price: {entry} / Stop Loss: {stop} / Price Target: {target}"
                                    f"\n\n**Price Target**: {target}"}


def _soxx_buy():
    return _soxx("Buy", 450, 438, 480)


def test_a_soxl_run_analyses_soxx_then_the_leverage_analyst_calls_soxl(db_url, monkeypatch):
    asked = []

    def analyst(ctx):
        asked.append(ctx)
        return ("**Rating**: Buy\n\n**Entry Price**: 60.5\n\n**Stop Loss**: 56\n\n**Time Horizon**: 5일 이내\n\n"
                "**Position Sizing**: 자본의 15%\n\n**Executive Summary**: SOXX 지지 확인을 SOXL 로.")

    done, seen, _ = _run(db_url, monkeypatch, "SOXL", analyst, _soxx_buy())
    plan = done["plan"]
    assert seen[0][0] == "SOXX" and "This run analyses SOXX" in seen[0][1] and "R 470.0" in seen[0][1]
    assert asked[0].u_plan["entry_price"] == 450 and asked[0].sr["last_close"] == 60.0 and not asked[0].inverse
    assert plan["rating"] == "Buy" and plan["entry_price"] == 60.5 and plan["stop_loss"] == 56
    assert plan["price_target"] == 72.0 and plan["levels_source"]["price_target"] == "converted"   # 480 -> 72
    assert plan["position_sizing"] == "자본의 15%" and plan["analysis_subject"] == "SOXX"
    sub = plan["underlying"]
    assert sub["ticker"] == "SOXX" and sub["rating"] == "Buy" and sub["entry_price"] == 450
    assert sub["support_resistance"]["last_close"] == 450.0 and sub["levels_source"]["entry_price"] == "model"
    assert done["status"] == "done" and "leverage_analyst_error" not in plan


def test_the_analysts_words_reach_the_chat(db_url, monkeypatch):
    from sqlalchemy import select

    from desk.db import events, make_engine

    done, _, _ = _run(db_url, monkeypatch, "SOXL", lambda ctx: "**Rating**: Hold\n\n**Executive Summary**: 관망",
                      _soxx_buy())
    engine = make_engine(db_url)
    with engine.connect() as conn:
        rows = conn.execute(select(events.c.type, events.c.agent).where(events.c.run_id == done["id"])
                            .order_by(events.c.seq)).all()
    engine.dispose()
    speakers = [agent for kind, agent in rows if kind == "message"]
    assert speakers[-1] == "leverage_analyst" and [kind for kind, _ in rows][-2:] == ["plan", "done"]


def test_a_soxs_run_turns_a_soxx_sell_into_a_soxs_buy(db_url, monkeypatch):
    asked = []

    def analyst(ctx):
        asked.append(ctx)
        return "**Rating**: Buy\n\n**Entry Price**: 21\n\n**Stop Loss**: 19.5\n\n**Price Target**: 25"

    sell = _soxx("Sell", 440, 452, 410)
    done, _, _ = _run(db_url, monkeypatch, "SOXS", analyst, sell, day=_today(), quotes={"SOXS": 21.0, "SOXX": 440.0})
    plan = done["plan"]
    assert asked[0].inverse and asked[0].daily_atr_u == 12.0
    assert plan["rating"] == "Buy" and plan["bias"] == "long"
    assert plan["underlying"]["rating"] == "Sell" and plan["underlying"]["bias"] == "short"
    assert plan["underlying"]["live_price"]["price"] == 440.0 and plan["live_price"]["price"] == 21.0


def _today():
    from datetime import date

    return date.today().isoformat()


def test_a_failing_analyst_leaves_the_flipped_call_and_converted_levels(db_url, monkeypatch):
    def broken(_ctx):
        raise ConnectionError("model down")

    sell = _soxx("Sell", 440, 452, 410)
    done, _, _ = _run(db_url, monkeypatch, "SOXS", broken, sell)
    plan = done["plan"]
    assert done["status"] == "done" and plan["rating"] == "Buy" and "model down" in plan["leverage_analyst_error"]
    # SOXX 440 / 452 / 410 against the closes 450 (SOXX) and 20 (SOXS), -3x
    assert plan["levels_source"] == {"entry_price": "converted", "stop_loss": "converted", "price_target": "converted"}
    assert plan["entry_price"] == 21.33 and plan["stop_loss"] == 19.73 and plan["price_target"] == 25.33


def test_a_typed_underlying_never_calls_the_leverage_analyst(db_url, monkeypatch):
    def never(_ctx):
        raise AssertionError("SOXX is not leveraged")

    done, seen, _ = _run(db_url, monkeypatch, "SOXX", never, _soxx_buy())
    assert seen[0][0] == "SOXX" and "This run analyses" not in seen[0][1]
    assert "underlying" not in done["plan"] and done["plan"]["entry_price"] == 450


def test_without_the_underlyings_bars_the_run_still_ends(db_url, monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", None)    # the product ATR fill has no Yahoo either
    done, _, _ = _run(db_url, monkeypatch, "SOXL", lambda ctx: "**Rating**: Buy\n\n**Entry Price**: 60",
                      _soxx_buy(), sr_fail=("SOXX",))
    plan = done["plan"]
    assert plan["underlying"]["entry_price"] == 450 and "support_resistance" not in plan["underlying"]
    assert plan["entry_price"] == 60 and "conversion" not in plan     # no anchor without SOXX's close


def test_an_analyst_that_turns_against_the_underlying_gets_no_borrowed_setup(db_url, monkeypatch):
    # SOXX Buy, the analyst says Sell SOXL without levels: no long levels converted onto a short call
    monkeypatch.setitem(sys.modules, "yfinance", None)     # and no Yahoo for the ATR fill
    done, _, _ = _run(db_url, monkeypatch, "SOXL", lambda ctx: "**Rating**: Sell\n\n**Executive Summary**: 감쇠가 크다",
                      _soxx_buy())
    plan = done["plan"]
    assert plan["rating"] == "Sell" and plan["bias"] == "short"
    assert "conversion" not in plan and plan["levels_source"] == {} and "levels_error" in plan


def test_a_free_written_rating_is_still_read(db_url, monkeypatch):
    done, _, _ = _run(db_url, monkeypatch, "SOXL", lambda ctx: "**Rating**: 매수 (**Buy**)\n\n**Entry Price**: 60",
                      _soxx_buy())
    assert done["plan"]["rating"] == "Buy" and "leverage_analyst_warning" not in done["plan"]


def test_an_unreadable_rating_falls_back_to_the_underlyings_call(db_url, monkeypatch):
    sell = _soxx("Sell", 440, 452, 410)
    done, _, _ = _run(db_url, monkeypatch, "SOXS", lambda ctx: "**Rating**: 적극 진입\n\n**Entry Price**: 21", sell)
    assert done["plan"]["rating"] == "Buy" and "leverage_analyst_warning" in done["plan"]


def test_an_underlying_price_in_a_product_slot_is_set_aside(db_url, monkeypatch):
    done, _, _ = _run(db_url, monkeypatch, "SOXL",
                      lambda ctx: "**Rating**: Buy\n\n**Entry Price**: 450\n\n**Stop Loss**: 56", _soxx_buy())
    plan = done["plan"]
    assert plan["implausible_levels"] == {"entry_price": 450.0}
    assert plan["entry_price"] == 60.0 and plan["levels_source"]["entry_price"] == "converted"
    assert plan["stop_loss"] == 56


def test_a_slow_analyst_is_cut_off_and_the_run_ends(db_url, monkeypatch):
    import time

    from desk import runner

    monkeypatch.setattr(runner, "LEVER_TIMEOUT_SEC", -29.9)    # backstop = 0.1 s
    done, _, _ = _run(db_url, monkeypatch, "SOXL", lambda ctx: time.sleep(1) or "**Rating**: Buy", _soxx_buy())
    assert done["status"] == "done" and "TimeoutError" in done["plan"]["leverage_analyst_error"]


def test_a_past_date_prompt_uses_the_closes_and_the_horizon_picks_the_decay_line():
    from desk.leverage_analyst import build_prompt

    past = build_prompt(_ctx(live=None, live_u=None, sr={"atr": 4.0, "last_close": 60.0, "levels": []},
                             sr_u={"atr": 12.0, "last_close": 450.0, "levels": []}))
    assert "SOXL price 60.0 (last close)" in past and "SOXX price 450.0 (last close)" in past
    assert "negligible" in build_prompt(_ctx(intraday=True))
    unmeasured = build_prompt(_ctx(daily_atr_u=None))
    assert "not measured" in unmeasured and "negligible" not in unmeasured
    assert "Exit by:" in unmeasured


def test_a_fake_run_of_a_leveraged_product_goes_end_to_end(db_url):
    from fastapi.testclient import TestClient

    from desk.api import create_app
    from desk.auth import Auth
    from desk.db import make_engine
    from desk.runner import make_fake_runner
    from desk.settings import Settings
    from tests.test_api import OWNER, PASSWORD, _wait_done

    client = TestClient(create_app(Settings(database_url=db_url, fake_delay=0, fake_runs=True),
                                   runner=make_fake_runner(0)), base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    with client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        done = _wait_done(client, client.post("/api/runs", json={"ticker": "SOXL", "trade_date": "2026-11-02"}).json()["id"])
    plan = done["plan"]
    assert done["status"] == "done" and plan["analysis_subject"] == "SOXX" and plan["underlying"]["ticker"] == "SOXX"
    assert plan["rating"] == plan["underlying"]["rating"] and plan["entry_price"] is not None


def test_the_underlyings_checklist_carries_over_so_the_filter_can_judge_the_product(db_url, monkeypatch):
    # #64: without a checklist and a win probability every leveraged plan would be 'review'
    sell = _soxx("Sell", 440, 452, 410)
    sell["final_trade_decision"] += ("\n\nWin Probability: 75% / Long Trigger: none / Short Trigger: 445"
                                     "\n\nChecklist: trend=short, location=short, momentum=short, volume=neutral, "
                                     "catalyst=short, regime=neutral")
    done, _, _ = _run(db_url, monkeypatch, "SOXS",
                      lambda ctx: "**Rating**: Buy\n\n**Entry Price**: 21\n\n**Stop Loss**: 19.5\n\n**Price Target**: 25",
                      sell)
    plan = done["plan"]
    assert plan["checklist"]["trend"] == "long" and plan["score"] == {"long": 4, "short": 0}
    assert plan["win_probability"] == 0.75 and plan["opinion"] == "buy" and plan["filter"]["passed"]
    assert plan["underlying"]["checklist"]["trend"] == "short"
