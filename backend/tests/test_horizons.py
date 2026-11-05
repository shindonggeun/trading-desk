"""#63: trading horizons (scalp · intraday · short swing · swing) pick bars, ATR multiples and
the deciding agents' instructions. No horizon = swing = the desk before horizons."""

import asyncio
from datetime import datetime, timedelta

import pytest

from desk import horizons, sr
from desk.levels import fill_levels
from desk.plan import parse_plan
from desk.profile import desk_portfolio
from tests.test_api import _client, _wait_done
from tests.test_sr import DAILY, WEEKLY, _zigzag


def test_four_horizons_with_swing_as_the_default():
    assert list(horizons.HORIZONS) == ["scalp", "intraday", "short_swing", "swing"]
    assert horizons.get(None).id == horizons.DEFAULT_HORIZON == "swing"
    swing = horizons.get("swing")
    # swing is exactly the desk's old frame
    assert (swing.primary, swing.secondary, swing.primary_k, swing.secondary_k) == ("1d", "1w", sr.SWING_DAILY, sr.SWING_WEEKLY)
    assert (swing.max_distance_atr, swing.max_distance_pct) == (sr.MAX_DISTANCE_ATR, sr.MAX_DISTANCE_PCT)
    assert (swing.stop_atr, swing.target_atr) == (1.5, 3.0)


def test_mandate_weighs_three_directions_and_asks_for_entry_invalidation_exit():
    text = horizons.horizon_mandate(horizons.get("intraday"), atr=0.9)
    assert "For holding period and level spacing this overrides the investor mandate" in text
    assert "rules on direction and sizing still apply" in text
    assert "long, short and Hold carry equal weight" in text
    assert "단타" not in text and "5분봉" not in text   # prompts stay English
    assert "'Entry: <now|limit|stop> / Invalidation: <text> / Exit by: <time or condition>'" in text
    assert "Right after the final levels line" in text   # the PM's levels line (desk.profile) stays as is
    assert "5m bars and their ATR14 (0.9)" in text and "never overnight" in text
    assert "reference map, not a trigger" in horizons.horizon_mandate(horizons.get("scalp"))


def test_swing_keeps_the_prompt_it_had_before_horizons():
    assert horizons.horizon_mandate(horizons.get("swing"), atr=6.0) is None


def test_mandate_follows_the_frame_the_levels_really_came_from():
    scalp = horizons.get("scalp")
    fell_back = horizons.horizon_mandate(scalp, atr=6.5, frame=horizons.get(None))
    assert "1d bars and their ATR14 (6.5)" in fell_back and "1m bars were not available" in fell_back
    assert "stop about 1.5 ATR" in fell_back   # daily ATR with daily multiples, not 2 x daily ATR
    no_levels = horizons.horizon_mandate(scalp, levels_below=False)
    assert "listed below" not in no_levels and "1m volatility" in no_levels


def test_valid_until_counts_from_the_quote_the_agents_saw():
    base = "2026-11-05T01:00:00+00:00"
    assert horizons.valid_until(horizons.get("scalp"), base) == "2026-11-05T01:15:00+00:00"
    assert horizons.valid_until(horizons.get("swing"), base) is None
    assert horizons.valid_until(horizons.get("intraday"), None) is None


def test_no_horizon_and_swing_give_the_same_levels():
    assert sr.compute(DAILY, WEEKLY, price=100.0) == sr.compute(DAILY, WEEKLY, price=100.0, horizon="swing")


def _intraday(n: int, low: float, high: float, period: int, step_min: int) -> list[dict]:
    start = datetime(2026, 11, 4, 14, 30)
    return [{"at": (start + timedelta(minutes=i * step_min)).isoformat() + "+00:00", "open": c, "high": c + 0.1,
             "low": c - 0.1, "close": c, "volume": 100.0} for i, c in enumerate(_zigzag(n, low, high, period))]


def test_intraday_levels_come_from_five_minute_bars_and_the_prior_session():
    five = _intraday(390, 99.0, 101.0, 24, 5)
    result = sr.compute(five, DAILY, price=100.0, horizon="intraday")
    bases = " ".join(lv["basis"] for lv in result["levels"])
    assert "5분봉" in bases
    assert {lv["timeframe"] for lv in result["levels"]} <= {"5m", "1d"}
    # an intraday plan has no use for zones 5% away
    assert all(abs(lv["price"] - 100.0) <= 0.05 * 100.0 for lv in result["levels"])
    assert result["atr"] < 1.0


def test_with_daily_secondary_bars_the_analysis_day_is_not_a_finished_session():
    days = [{"at": f"2026-11-0{d}T05:00:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
            for d in (3, 4, 5)]
    _, finished = sr._until(days, days, "NVDA", "2026-11-05", secondary_days=1)
    assert [b["at"][:10] for b in finished] == ["2026-11-03", "2026-11-04"]


class _Prices:
    def __init__(self, intraday_bars):
        self.calls, self.intraday = [], intraday_bars

    async def candles(self, ticker, interval, count):
        self.calls.append((interval, count))
        bars = {"1d": DAILY, "1w": WEEKLY}.get(interval, self.intraday)
        return {"bars": bars[-count:] if bars else [], "source": "yahoo"}


def test_support_resistance_reads_the_horizons_bars_and_caches_per_horizon():
    sr._CACHE.clear()
    source = _Prices(_intraday(390, 99.0, 101.0, 24, 5))
    intraday = asyncio.run(sr.support_resistance(source, "NVDA", horizon="intraday"))
    swing = asyncio.run(sr.support_resistance(source, "NVDA"))
    assert ("5m", 390) in source.calls and ("1w", 156) in source.calls
    assert intraday["intervals"] == {"primary": "5m", "secondary": "1d"} and intraday["horizon"] == "intraday"
    assert swing["intervals"] == {"primary": "1d", "secondary": "1w"} and swing["horizon"] == "swing"
    assert "5m and 1d bars" in sr.prompt_note(intraday) and "daily and weekly bars" in sr.prompt_note(swing)


def test_missing_intraday_bars_fall_back_to_the_swing_frame_and_say_so():
    sr._CACHE.clear()
    result = asyncio.run(sr.support_resistance(_Prices([]), "NVDA", horizon="scalp"))
    assert result["fallback"]["from"] == "1m" and result["intervals"]["primary"] == "1d"
    assert result["horizon"] == "scalp" and "were not available" in sr.prompt_note(result)


def _plan(bias):
    return {"bias": bias, "entry_price": None, "stop_loss": None, "price_target": None}


def test_levels_use_the_horizons_atr_and_multiples():
    asked = []

    def prices(ticker, day, interval="1d"):
        asked.append(interval)
        return (100.0, 0.5) if interval == "5m" else (100.0, 6.0)
    plan = fill_levels(_plan("long"), "NVDA", "2026-11-04", prices, horizon="intraday")
    assert asked == ["5m"] and plan["atr_interval"] == "5m"
    assert (plan["entry_price"], plan["stop_loss"], plan["price_target"]) == (100.0, 99.0, 102.0)   # 2 / 4 ATR
    swing = fill_levels(_plan("long"), "NVDA", "2026-11-04", prices)
    assert (swing["stop_loss"], swing["price_target"]) == (91.0, 118.0) and "atr_interval" not in swing


def test_levels_without_intraday_bars_use_daily_atr_with_swing_multiples():
    def prices(ticker, day, interval="1d"):
        if interval != "1d":
            raise ValueError("Yahoo keeps 1m bars for a week")
        return 100.0, 6.0
    plan = fill_levels(_plan("short"), "NVDA", "2025-01-02", prices, horizon="scalp")
    assert (plan["stop_loss"], plan["price_target"]) == (109.0, 82.0)
    assert "swing multiples" in plan["atr_warning"] and "atr_interval" not in plan
    assert "levels_warning" not in plan   # that key means "the live quote is not trusted"


def test_summary_closing_line_is_parsed():
    plan = parse_plan({"final_trade_decision": (
        "**Rating**: Hold\n\n**Executive Summary**: 관망.\n"
        "Entry Price: 182 / Stop Loss: 178 / Price Target: 190\n"
        "Entry: Stop / Invalidation: 178 아래 15분 마감 / Exit by: 오늘 장 마감 전\n\n**Time Horizon**: 당일")})
    assert (plan["entry_type"], plan["invalidation"], plan["exit_by"]) == ("stop", "178 아래 15분 마감", "오늘 장 마감 전")
    bare = parse_plan({"final_trade_decision": "**Rating**: Buy\n\n**Executive Summary**: 매수."})
    assert (bare["entry_type"], bare["invalidation"], bare["exit_by"]) == (None, None, None)


def test_horizon_mandate_sits_between_the_style_and_the_levels_note():
    portfolio = desk_portfolio("balanced", None, "LEVELS NOTE", horizon_mandate="HORIZON MANDATE")
    text = portfolio.render("NVDA")
    assert text.index("balanced swing investor") < text.index("HORIZON MANDATE") < text.index("LEVELS NOTE")


def test_runs_take_a_horizon_and_plans_carry_it(db_url):
    with _client(db_url) as client:
        config = client.get("/api/config").json()
        assert [h["id"] for h in config["horizons"]] == list(horizons.HORIZONS)
        assert config["default_horizon"] == "swing"
        assert client.post("/api/runs", json={"ticker": "NVDA", "horizon": "weekly"}).status_code == 422
        assert client.get("/api/levels?ticker=NVDA&horizon=weekly").status_code == 422

        default = client.post("/api/runs", json={"ticker": "NVDA"}).json()
        assert default["options"]["horizon"] == "swing"
        swing = _wait_done(client, default["id"])["plan"]
        intraday_run = client.post("/api/runs", json={"ticker": "NVDA", "horizon": "intraday"}).json()
        intraday = _wait_done(client, intraday_run["id"])["plan"]
    assert swing["horizon"] == "swing" and intraday["horizon"] == "intraday"
    gap = lambda p: p["entry_price"] - p["stop_loss"]
    assert gap(intraday) < gap(swing)
    assert intraday["time_horizon"] == "당일" and intraday["entry_type"] == "limit"
    assert swing["valid_until"] is None


@pytest.mark.parametrize("horizon", list(horizons.HORIZONS))
def test_every_horizon_has_a_mandate_and_a_dict(horizon):
    h = horizons.get(horizon)
    assert h.to_dict() == {"id": h.id, "label": h.label, "holding": h.holding}
    mandate = horizons.horizon_mandate(h)
    assert mandate is None if h.id == "swing" else h.holding_en in mandate


def test_scalp_keeps_daily_zones_from_days_ago_alongside_minute_bars():
    # 1-minute bars span hours; the daily swings behind them are days to months old and must
    # not decay to nothing on the minutes' half-life (that made every zone weigh zero).
    one = _intraday(200, 99.8, 100.2, 30, 1)
    result = sr.compute(one, DAILY, price=100.0, horizon="scalp")
    assert result["levels"] and result["atr"] < 1.0


def test_prior_session_is_the_day_before_the_minute_bars_session_not_the_trade_date():
    # KST 2026-11-06 01:00 = New York 11-05 12:00: the 11-05 daily bar is still trading.
    sr._CACHE.clear()
    five = [{"at": f"2026-11-05T{14 + i // 12:02d}:{(i % 12) * 5:02d}:00+00:00", "open": 100.0, "high": 100.2,
             "low": 99.8, "close": 100.0, "volume": 1.0} for i in range(40)]
    days = [{"at": f"2026-10-{d:02d}T04:00:00+00:00", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
             "volume": 1.0} for d in range(10, 31)]
    days += [{"at": "2026-11-04T05:00:00+00:00", "open": 100, "high": 104.0, "low": 96.0, "close": 100, "volume": 1},
             {"at": "2026-11-05T05:00:00+00:00", "open": 100, "high": 130.0, "low": 70.0, "close": 100, "volume": 1}]

    class Bars:
        async def candles(self, ticker, interval, count):
            return {"bars": five if interval == "5m" else days, "source": "yahoo"}
    result = asyncio.run(sr.support_resistance(Bars(), "NVDA", "2026-11-06", horizon="intraday"))
    bases = {lv["basis"] for lv in result["levels"]}
    prices = {lv["price"] for lv in result["levels"]}
    assert 130.0 not in prices and 70.0 not in prices              # today's unfinished bar is not "prior"
    assert any("전일" in b for b in bases)


def test_entry_line_with_stop_is_not_read_as_the_stop_loss():
    plan = parse_plan({
        "trader_investment_plan": "**Entry Price**: 186.5\n\n**Stop Loss**: 180",
        "final_trade_decision": ("**Rating**: Buy\n\n**Executive Summary**: 돌파 매수, 손절 181. "
                                 "Entry: stop on a break of 186.5 / Invalidation: 181 아래 / Exit by: 오늘\n\n"
                                 "**Price Target**: 195")})
    assert plan["stop_loss"] != 186.5 and plan["entry_type"] == "stop"
