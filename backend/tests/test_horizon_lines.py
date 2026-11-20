"""#696 one decision, four horizons: the PM's line per horizon - asked for, read, attached and scored."""

import asyncio
from datetime import UTC, datetime, timedelta

from desk import all_horizons, language, outcome
from desk.plan import HORIZON_LABELS, parse_plan, split_horizon_lines

DECISION = """**Rating**: Overweight

**Executive Summary**: 182 눌림 진입. Entry Price: 182.5 / Stop Loss: 171 / Price Target: 205
Entry: limit / Invalidation: 171 아래 마감 / Exit by: 2주 안에
Scalp Plan: Buy / Entry: 183.1 / Stop: 182.2 / Target: 184.9 / Why: 5분봉 지지 반등
- **Intraday Plan**: Hold / Entry: $181 / Stop: 179.5 / Target: 185 / Why: 박스 하단 대기
Short Swing Plan: Sell / Entry: 186 / Stop: 189 / Target: 180 / Why: 상단 저항
**Swing Plan:** Overweight / Entry: 182.5 / Stop: 171 / Target: 205 / Why: 추세 위

**Price Target**: 205
"""


def test_the_four_lines_are_read_apart_from_the_runs_own_levels():
    plan = parse_plan({"final_trade_decision": DECISION, "final_rating": "Overweight"})
    assert (plan["entry_price"], plan["stop_loss"], plan["price_target"]) == (182.5, 171.0, 205.0)
    assert (plan["entry_type"], plan["exit_by"]) == ("limit", "2주 안에")
    lines = plan["horizon_lines"]
    assert set(lines) == set(all_horizons.ORDER)
    assert lines["scalp"] == {"rating": "Buy", "bias": "long", "entry_price": 183.1, "stop_loss": 182.2,
                              "price_target": 184.9, "reward_risk": 2.0, "why": "5분봉 지지 반등"}
    assert (lines["intraday"]["bias"], lines["intraday"]["entry_price"]) == ("flat", 181.0)   # bold, bullet, $
    assert (lines["short_swing"]["bias"], lines["short_swing"]["price_target"]) == ("short", 180.0)
    assert lines["swing"]["entry_price"] == 182.5                                             # colon in the bold
    assert "Plan" not in plan["executive_summary"]


def test_a_line_that_contradicts_itself_loses_the_level_and_no_lines_is_no_key():
    lines, rest = split_horizon_lines("Scalp Plan: Buy / Entry: 100 / Stop: 101 / Target: 103\nother text")
    # entry and stop make a short, so the target over the entry goes (desk.plan.drop_contradiction)
    assert lines["scalp"]["price_target"] is None and lines["scalp"]["levels_rejected"] == {"price_target": 103.0}
    assert rest.strip() == "other text"
    assert "horizon_lines" not in parse_plan({"final_trade_decision": "**Rating**: Hold\n\n**Executive Summary**: 관망."})
    unrated, _ = split_horizon_lines("Swing Plan: Entry: 50 / Stop: 48 / Target: 56")
    assert unrated["swing"]["rating"] is None and unrated["swing"]["bias"] is None


def test_the_mandates_line_reads_back_and_a_translated_reason_is_the_same_plan():
    filled = "\n".join(all_horizons.mandate("swing", "AMD").splitlines()[1:5])
    filled = filled.replace("<Buy|Overweight|Hold|Underweight|Sell>", "Buy").replace("<number>", "10", 1)
    for label in HORIZON_LABELS.values():
        assert f"{label} Plan:" in filled
    lines, _ = split_horizon_lines(filled)
    assert set(lines) == set(all_horizons.ORDER) and lines["scalp"]["entry_price"] == 10.0
    state = {"final_trade_decision": DECISION, "final_rating": "Overweight"}
    translated = DECISION.replace("Why: 상단 저항", "Why: resistance above")
    assert language.same_plan(state, "final_trade_decision", translated)


def test_finish_puts_the_plan_on_its_horizon_and_the_lines_on_the_others():
    plan = {**parse_plan({"final_trade_decision": DECISION, "final_rating": "Overweight"}), "horizon": "swing"}
    plan["horizon_lines"]["scalp"]["ticker"] = "QQQ"     # a leveraged run's lines are the underlying's
    got = all_horizons.fake("TQQQ", plan, "swing")
    rows = got["horizons"]
    assert [h for h in all_horizons.ORDER if rows[h]["source"] == "model"] == ["intraday", "short_swing", "swing"]
    assert rows["scalp"]["model"] is None and set(rows["scalp"]["underlying_line"]) == set(all_horizons.MODEL_KEYS)
    assert (rows["scalp"]["underlying_line"]["origin"], rows["scalp"]["underlying_line"]["ticker"]) == ("line", "QQQ")
    assert set(rows["short_swing"]["model"]) == set(all_horizons.MODEL_KEYS)
    assert rows["intraday"]["model"]["ticker"] == "TQQQ" and rows["intraday"]["model"]["why"] == "박스 하단 대기"
    own = rows["swing"]["model"]
    assert (own["origin"], own["entry_price"], own["why"]) == ("plan", 182.5, "추세 위")
    bare = all_horizons.fake("TQQQ", {"entry_price": 50.0, "horizon": "swing"}, "swing")["horizons"]
    assert bare["scalp"]["source"] == "map" and bare["scalp"]["model"] is None


def test_the_prompt_note_and_the_class_note():
    note = all_horizons.prompt_note(all_horizons.fake("TQQQ", {"entry_price": 50.0}, "swing"))
    assert note.count("\n- ") == 4 and "Scalp (5m chart" in note and "playbook long" in note
    assert "Regime now: range" in note and all_horizons.prompt_note({"horizons": None}) is None
    assert "leveraged" in all_horizons.class_note(all_horizons.instrument_class("TQQQ"))
    assert "bond" in all_horizons.class_note(all_horizons.instrument_class("TLT"))
    assert all_horizons.class_note(all_horizons.instrument_class("AAPL")) is None


def test_a_scripted_run_asks_for_the_lines_and_carries_them(db_url):
    from datetime import timedelta as td

    from fastapi.testclient import TestClient

    from desk.api import create_app
    from desk.auth import Auth
    from desk.db import make_engine
    from desk.runner import make_fake_runner
    from desk.settings import Settings
    from tests.test_api import OWNER, PASSWORD, _wait_done

    prompts = []
    fake = make_fake_runner(0)

    def runner(request, emit, portfolio):
        prompts.append(portfolio.render(request.ticker))
        return fake(request, emit, portfolio)

    runner.fake = True
    client = TestClient(create_app(Settings(database_url=db_url, fake_delay=0, fake_runs=True), runner=runner),
                        base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, td(hours=1), td(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    with client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        created = client.post("/api/runs", json={"ticker": "TQQQ", "horizon": "intraday", "profile": "balanced"})
        run = _wait_done(client, created.json()["id"])
    assert run["status"] == "done", run["error"]
    assert "Scalp Plan: <Buy" in prompts[0] and "Instrument class: a daily-reset leveraged" in prompts[0]
    assert "balanced swing investor" not in prompts[0]
    lines = run["plan"]["horizon_lines"]
    assert set(lines) == set(all_horizons.ORDER)
    # the analysed instrument; no live quote in a scripted run, so no entry window
    assert all(line["ticker"] == "QQQ" and "valid_until" in line for line in lines.values())
    rows = run["plan"]["horizons"]
    # review #696: QQQ's prices are not TQQQ's - the line stands beside the product's slot, not in it
    assert rows["scalp"]["source"] == "map" and rows["scalp"]["model"] is None
    assert (rows["scalp"]["underlying_line"]["ticker"], rows["scalp"]["underlying_line"]["origin"]) == ("QQQ", "line")
    assert rows["intraday"]["model"]["origin"] == "plan" and rows["intraday"]["underlying_line"] is None


PLAN_AT = "2026-11-02T14:30:00+00:00"


def _bars(rows, step):
    start = datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
    return [{"at": (start + i * step).isoformat(), "open": o, "high": h, "low": low, "close": c, "volume": 1}
            for i, (o, h, low, c) in enumerate(rows)]


class _Bars:
    """5m: the intraday line fills and reaches its target; 60m: the short swing short is stopped."""

    def __init__(self, fail=()):
        self.calls, self.fail = [], fail

    async def candles(self, ticker, interval, count):
        self.calls.append((ticker, interval))
        if interval in self.fail:
            raise ConnectionError("down")
        if interval == "5m":
            return {"bars": _bars([(100.2, 100.3, 99.9, 100.1), (100.1, 101.0, 100.0, 100.8),
                                   (100.8, 102.4, 100.7, 102.2)], timedelta(minutes=5))}
        return {"bars": _bars([(99.8, 100.4, 99.7, 100.2), (100.2, 103.5, 100.1, 103.2)], timedelta(hours=1))}


def _run():
    lines = {"scalp": {"rating": "Hold", "bias": "flat", "entry_price": 100.0, "stop_loss": 99.5, "price_target": 101.0},
             "intraday": {"rating": "Buy", "bias": "long", "entry_price": 100.0, "stop_loss": 99.0,
                          "price_target": 102.0, "ticker": "SOXX", "valid_until": "2026-11-02T20:00:00+00:00"},
             "short_swing": {"rating": "Sell", "bias": "short", "entry_price": 100.0, "stop_loss": 103.0,
                             "price_target": 94.0, "ticker": "SOXX", "valid_until": "2026-11-03T20:00:00+00:00"},
             "swing": {"rating": "Hold", "bias": "flat", "entry_price": 100.0, "stop_loss": 95.0, "price_target": 110.0}}
    return {"ticker": "SOXL", "options": {}, "plan": {"bias": "flat", "horizon": "swing", "horizon_lines": lines,
                                                       "live_price": {"fetched_at": PLAN_AT}}}


def test_each_line_is_scored_on_its_own_horizons_bars_and_counted_apart():
    run, prices = _run(), _Bars()
    result = asyncio.run(outcome.score(run, prices, datetime(2026, 11, 2, 18, tzinfo=UTC)))
    assert result["status"] == "invalid"                     # the run's own plan is a Hold
    assert set(result["horizons"]) == {"intraday", "short_swing"}   # the directional lines but the run's own
    assert result["horizons"]["intraday"]["status"] == "target" and result["horizons"]["short_swing"]["status"] == "stop"
    assert result["horizons"]["intraday"]["ticker"] == "SOXX" and ("SOXX", "5m") in prices.calls
    assert result["sweep_status"] == "invalid"                # all settled: the run leaves the sweep
    stats = outcome.stats([{**run, "outcome": result}])
    by = {row["key"]: row for row in stats["by_horizon_line"]}
    assert (by["intraday"]["target"], by["short_swing"]["stop"]) == (1, 1) and stats["total"]["plans"] == 0


def test_a_settled_line_is_kept_and_a_pending_one_keeps_the_run_in_the_sweep():
    run = _run()
    first = asyncio.run(outcome.score(run, _Bars(fail=("60m",)), datetime(2026, 11, 2, 18, tzinfo=UTC)))
    assert first["horizons"]["short_swing"]["status"] == "error" and first["sweep_status"] == "error"
    again = asyncio.run(outcome.score({**run, "outcome": first}, _Bars(fail=("5m",)),
                                      datetime(2026, 11, 2, 19, tzinfo=UTC)))
    assert again["horizons"]["intraday"]["status"] == "target"      # not fetched again
    assert again["horizons"]["short_swing"]["status"] == "stop"


def test_a_korean_rating_word_is_read_and_the_mandate_asks_for_the_english_one():
    lines, _ = split_horizon_lines("Scalp Plan: 매수 / Entry: 10 / Stop: 9 / Target: 12\n"
                                   "Swing Plan: **비중축소** / Entry: 10 / Stop: 11 / Target: 8")
    assert (lines["scalp"]["rating"], lines["swing"]["bias"]) == ("Buy", "short")
    text = all_horizons.mandate("swing", "AMD")
    assert "English rating word" in text and "portfolio manager only" in text


def test_a_line_is_framed_against_the_live_price_like_the_plan():
    from desk.runner import RunManager

    lines = {"intraday": {"rating": "Buy", "bias": "long", "entry_price": 105.0, "stop_loss": 102.0,
                          "price_target": 112.0},
             "swing": {"rating": "Buy", "bias": "long", "entry_price": 99.9, "stop_loss": 101.0, "price_target": 104.0}}
    framed = RunManager._frame_lines(lines, "NVDA", {"price": 100.0}, {"last_close": 100.0},
                                     {"fetched_at": PLAN_AT})
    assert framed["intraday"]["entry_vs_live"]["kind"] == "stop"            # a breakout, not a limit
    assert framed["intraday"]["ticker"] == "NVDA" and framed["intraday"]["valid_until"]
    assert "live_price" not in framed["intraday"]
    result = outcome.judge(framed["intraday"], _bars([(100.0, 101.0, 99.5, 100.5)], timedelta(minutes=5)),
                           outcome.horizons.get("intraday"), PLAN_AT)
    assert result["status"] != "stop"                                      # never filled under 105


def test_the_runs_settled_plan_is_kept_while_a_line_is_pending_and_past_its_bars():
    # review #696: a scalp's own target settles on day one; its swing line keeps the run in the sweep
    run = _run()
    run["plan"] = {**run["plan"], "horizon": "scalp", "bias": "long", "entry_price": 100.0, "stop_loss": 99.0,
                   "price_target": 101.0}
    run["plan"]["horizon_lines"] = {"swing": {"rating": "Buy", "bias": "long", "entry_price": 100.0,
                                              "stop_loss": 95.0, "price_target": 110.0, "ticker": "SOXL"}}
    settled = {"rules_version": outcome.RULES_VERSION, "status": "target", "r": 1.0, "interval": "1m"}
    prices = _Bars()
    later = asyncio.run(outcome.score({**run, "outcome": settled}, prices, datetime(2026, 11, 4, tzinfo=UTC)))
    assert later["status"] == "target" and later["r"] == 1.0 and ("SOXL", "1m") not in prices.calls
    assert later["horizons"]["swing"]["status"] in ("waiting", "open") and later["sweep_status"] in ("waiting", "open")
    # past the 1m bars' history: the lines are still scored and kept
    late = asyncio.run(outcome.score({**run, "outcome": later}, _Bars(), datetime(2026, 11, 12, tzinfo=UTC)))
    assert late["status"] == "target" and "swing" in late["horizons"]


def test_the_per_horizon_note_is_built_from_the_maps():
    from types import SimpleNamespace

    from desk import horizons
    from desk.runner import RunManager, RunRequest
    from tests.test_all_horizons import _Prices

    me = SimpleNamespace(prices=_Prices(), risk=None)
    note = asyncio.run(RunManager._horizons_note(me, RunRequest(ticker="ZZZT", trade_date="2026-11-20"),
                                                 horizons.get("swing")))
    assert note.startswith("The desk's per-horizon reference") and note.count("\n- ") == 4
