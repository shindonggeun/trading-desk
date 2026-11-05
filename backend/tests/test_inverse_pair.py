import asyncio

from desk import underlying as under
from desk.leverage_analyst import build_prompt, fake_analyst, parse_holder_exit, parse_pair, strip_extras
from desk.plan import parse_plan
from desk.related import inverse_pair
from tests.test_underlying import _ctx, _run, _soxx, _today


def test_the_pair_is_the_same_underlying_the_other_way_same_multiple_first():
    assert inverse_pair("SOXL") == ("SOXS", -3) and inverse_pair("soxs") == ("SOXL", 3)
    assert inverse_pair("TQQQ") == ("SQQQ", -3) and inverse_pair("122630.KS") == ("252670.KS", -2)
    assert inverse_pair("NVDL") == ("NVD", -2)          # 2x long -> the 2x inverse, not the -1x
    assert inverse_pair("SOXX") is None and inverse_pair("MSTU") is None   # not leveraged · no inverse listed


def _pair_ctx(**kw):
    return _ctx(pair="SOXS", pair_leverage=-3.0, live_pair={"price": 20.0}, **kw)


def test_the_prompt_names_the_pair_and_asks_for_a_buy_of_it_against_the_product():
    text = build_prompt(_pair_ctx())
    assert "SOXS is the -3x inverse product on SOXX: price 20.0 (live)" in text
    assert "is not a short of SOXL" in text and "BUY of SOXS" in text and "keep their usual meaning" in text
    assert "'Holder Exit: <SOXL price or none>'" in text and "lines of their own" in text
    assert "Inverse Pair: SOXS / Pair Entry:" in text and "else 'Inverse Pair: none'" in text
    assert "Inverse Pair" not in build_prompt(_ctx())   # no pair known: nothing about it
    inverse = build_prompt(_ctx(product="SOXS", leverage=-3.0, pair="SOXL", pair_leverage=3.0,
                                live_pair={"price": 61.0, "source": "close"}))
    assert "A bullish SOXX call (your SOXS rating Underweight/Sell" in inverse and "BUY of SOXL" in inverse
    assert "SOXL is the +3x product on SOXX: price 61.0 (last close)" in inverse


def test_the_pair_line_is_read_only_as_a_plausible_buy_of_the_pair():
    ctx = _pair_ctx()
    ok = parse_pair("**Rating**: Hold\n...\n**Inverse Pair**: SOXS / **Pair Entry**: 20.4 / Pair Stop: 18.9 / Pair Target: 24", ctx)
    assert ok == {"ticker": "SOXS", "leverage": -3.0, "entry_price": 20.4, "stop_loss": 18.9, "price_target": 24.0,
                  "reward_risk": 2.4, "price": 20.0, "price_source": None}
    assert parse_pair("Inverse Pair: none", ctx) is None
    assert parse_pair("Inverse Pair: SQQQ / Pair Entry: 20 / Pair Stop: 19 / Pair Target: 23", ctx) is None   # other ticker
    assert parse_pair("Inverse Pair: SOXS / Pair Entry: 20 / Pair Stop: 21 / Pair Target: 18", ctx) is None   # not a buy
    assert parse_pair("Inverse Pair: SOXS / Pair Entry: 441 / Pair Stop: 430 / Pair Target: 470", ctx) is None  # SOXX prices
    line = "Inverse Pair: SOXS / Pair Entry: 20 / Pair Stop: 19 / Pair Target: 23"
    assert parse_pair(line, _ctx()) is None                                           # no pair
    assert parse_pair(line, _ctx(pair="SOXS", pair_leverage=-3.0)) is None             # no price to check against
    assert parse_pair(line, ctx, "long") is None                                       # a long SOXL call: no SOXS buy
    assert parse_pair(line, ctx, "short")["entry_price"] == 20
    big = parse_pair("Inverse Pair: 252670.KS / Pair Entry: 1,950 / Pair Stop: 1,880 / Pair Target: 2,100",
                     _ctx(pair="252670.KS", pair_leverage=-2.0, live_pair={"price": 1960.0}))
    assert big["entry_price"] == 1950 and big["reward_risk"] == 2.14


def test_the_pair_line_reads_in_the_shapes_models_write():
    ctx = _pair_ctx()
    for text in ("**Inverse Pair**: **SOXS** / **Pair Entry**: **$20.4** / Pair Stop: $18.9 / Pair Target: 24",
                 "Inverse Pair: SOXS / Pair Entry: 20.4달러 / Pair Stop: 18.9달러 / Pair Target: 24달러",
                 "Inverse Pair: SOXS / Pair Entry: 20.4 (SOXS) / Pair Stop: 18.9 (SOXS) / Pair Target: 24 (SOXS)",
                 "Inverse Pair: SOXS\nPair Entry: 20.4\nPair Stop: 18.9\nPair Target: 24"):
        got = parse_pair(text, ctx)
        assert got and (got["entry_price"], got["stop_loss"], got["price_target"]) == (20.4, 18.9, 24.0), text
    assert parse_pair("Inverse Pair: SOXS / Pair Entry: 20.4 / Pair Stop: 18.9", ctx) is None   # a field missing


def test_the_pair_and_holder_lines_never_reach_the_product_plan():
    text = ("**Rating**: Underweight\n\n**Executive Summary**: 보유분 축소. Inverse Pair: SOXS / Pair Entry: 58 / "
            "Pair Stop: 55 / Pair Target: 64\n\nHolder Exit: 57")
    clean = strip_extras(text)
    assert "Inverse Pair" not in clean and "Holder Exit" not in clean and "보유분 축소." in clean
    plan = parse_plan({"final_trade_decision": clean, "trader_investment_plan": clean})
    assert plan["entry_price"] is None and "Pair" not in (plan["executive_summary"] or "")
    assert parse_holder_exit(text, _ctx()) == 57.0
    assert parse_holder_exit("Holder Exit: 441", _ctx()) is None      # SOXX's number, not SOXL's (60)
    assert parse_holder_exit("Holder Exit: none", _ctx()) is None


def test_the_fake_analyst_names_the_pair_only_against_the_product():
    bearish = fake_analyst(_pair_ctx(u_plan={"rating": "Sell"}))
    assert "Inverse Pair: SOXS / Pair Entry: 20.00 / Pair Stop: 18.80 / Pair Target: 22.40" in bearish
    assert "Holder Exit: 58.20" in bearish
    assert parse_pair(bearish, _pair_ctx())["ticker"] == "SOXS"
    assert "Inverse Pair: none" in fake_analyst(_pair_ctx())     # Buy SOXL: no pair trade


def test_the_underlying_note_tags_every_price_and_asks_for_triggers_on_hold():
    note = under.prompt_note("SOXL", "SOXX", 3.0, False)
    assert "write 'SOXX' next to each price" in note and "long trigger and a short trigger" in note


def test_a_past_date_prices_the_pair_at_its_close_up_to_that_day():
    from desk import runner

    class Prices:
        async def candles(self, ticker, interval, count):
            return {"bars": [{"at": "2026-10-30T04:00:00+00:00", "close": 19.5},
                             {"at": "2026-11-02T05:00:00+00:00", "close": 20.25},
                             {"at": "2026-11-03T05:00:00+00:00", "close": 21.0}], "source": "yahoo"}

    class Request:
        ticker, trade_date = "SOXL", "2026-11-02"

    manager = runner.RunManager.__new__(runner.RunManager)
    manager.prices = Prices()
    got = asyncio.run(manager._pair_price(Request(), "SOXS"))
    assert got == {"price": 20.25, "source": "close", "fetched_at": "2026-11-02T05:00:00+00:00"}

    class Broken:
        async def candles(self, *_a):
            raise ConnectionError("down")
    manager.prices = Broken()
    assert asyncio.run(manager._pair_price(Request(), "SOXS")) is None


def test_a_soxl_run_on_a_bearish_soxx_carries_the_soxs_buy(db_url, monkeypatch):
    asked = []

    def analyst(ctx):
        asked.append(ctx)
        return ("**Rating**: Underweight\n\n**Entry Price**: 58\n\n**Stop Loss**: 61.5\n\n**Price Target**: 51\n\n"
                "**Executive Summary**: 보유 SOXL 은 61.5 위에서 버티고 58 아래면 축소.\n\n"
                "Holder Exit: 57.5\n\nInverse Pair: SOXS / Pair Entry: 21 / Pair Stop: 19.8 / Pair Target: 24")
    done, _, _ = _run(db_url, monkeypatch, "SOXL", analyst, _soxx("Underweight", 441, 458.5, 420),
                      day=_today(), quotes={"SOXL": 60.0, "SOXX": 450.0, "SOXS": 21.0})
    plan = done["plan"]
    assert asked[0].pair == "SOXS" and asked[0].live_pair["price"] == 21.0
    assert plan["bias"] == "short" and plan["pair"]["ticker"] == "SOXS"
    assert plan["pair"]["entry_price"] == 21 and plan["pair"]["reward_risk"] == 2.5 and plan["pair"]["price"] == 21.0
    assert plan["holder_exit"] == 57.5 and plan["entry_price"] == 58 and "Pair" not in plan["executive_summary"]


def test_a_soxs_run_on_a_bullish_soxx_offers_a_soxl_buy(db_url, monkeypatch):
    def analyst(ctx):
        assert ctx.pair == "SOXL" and ctx.inverse
        return ("**Rating**: Underweight\n\n**Entry Price**: 20\n\n**Stop Loss**: 21.5\n\n**Price Target**: 17\n\n"
                "Holder Exit: 19.6\n\nInverse Pair: SOXL / Pair Entry: 60 / Pair Stop: 56 / Pair Target: 70")
    done, _, _ = _run(db_url, monkeypatch, "SOXS", analyst, _soxx("Buy", 450, 438, 480),
                      day=_today(), quotes={"SOXS": 20.0, "SOXX": 450.0, "SOXL": 60.0})
    plan = done["plan"]
    assert plan["pair"]["ticker"] == "SOXL" and plan["pair"]["leverage"] == 3 and plan["holder_exit"] == 19.6
    assert plan["entry_price"] == 20 and "Inverse Pair" not in (plan.get("executive_summary") or "")


def test_no_pair_price_or_a_broken_line_still_ends_the_run(db_url, monkeypatch):
    asked = []

    def analyst(ctx):
        asked.append(ctx)
        return "**Rating**: Hold\n\n**Executive Summary**: 관망\n\nInverse Pair: SOXS / Pair Entry: abc"
    done, _, _ = _run(db_url, monkeypatch, "SOXL", analyst, _soxx("Hold", 441, 458.5, 420))
    assert done["status"] == "done" and "pair" not in done["plan"]
    assert asked[0].pair is None      # a past date without a close for SOXS: no pair offered at all
