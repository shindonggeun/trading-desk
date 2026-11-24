from datetime import date, datetime, timedelta

import pytest

from desk import kr_flow_board as board
from desk.market_calendar import KST, is_open

TODAY = date(2026, 11, 24)
PREVIOUS = date(2026, 11, 23)
FRIDAY = date(2026, 11, 20)
NOW = datetime(2026, 11, 24, 16, 30, tzinfo=KST)
MARKET = "kospi"
SEC = "005930.KS"
HYNIX = "000660.KS"


def _view(
    publication: board.Publication, *, days: int = 2, market: str = MARKET,
    favorites: tuple[dict, ...] = ({"ticker": SEC},),
    favorite_markets: dict[str, str] | None = None,
) -> dict:
    return board.build_board(market, board.sessions(NOW, days), publication=publication,
                             now=NOW, favorites=favorites, favorite_markets=favorite_markets)


def _turnover(ticker=SEC, as_of=TODAY, value=100_000, **extra) -> board.Turnover:
    extra.setdefault("trade_scope", "all_sessions")
    return board.Turnover(ticker, extra.pop("market", MARKET), as_of, "krx_turnover", value, **extra)


def _flow(ticker=SEC, as_of=TODAY, buy=12_000, sell=8_000, **extra) -> board.ForeignFlow:
    extra.setdefault("trade_scope", "all_sessions")
    extra.setdefault("venue", "krx")
    extra.setdefault("unit", "KRW")
    return board.ForeignFlow(ticker, extra.pop("market", MARKET), as_of, "kis_stock_daily", buy, sell, **extra)


def _ownership(ticker=SEC, as_of=PREVIOUS, percent=47.5, **extra) -> board.Ownership:
    extra.setdefault("denominator", "listed_shares")
    return board.Ownership(ticker, extra.pop("market", MARKET), as_of, "krx_ownership", percent, **extra)


def test_sessions_use_korean_regular_close_and_skip_holidays():
    before_close = datetime(2026, 3, 3, 15, 29, tzinfo=KST)
    after_close = before_close + timedelta(minutes=1)
    # March 2 is the Monday holiday for March 1, followed by the next open session.
    assert board.sessions(before_close, 1) == (date(2026, 2, 27),)
    history = board.sessions(after_close)
    assert len(history) == 22
    assert history[-2:] == (date(2026, 2, 27), date(2026, 3, 3))
    assert all(is_open("kr", day) for day in history)
    assert board.sessions(datetime(2026, 11, 22, 20, tzinfo=KST), 1) == (FRIDAY,)
    with pytest.raises(ValueError, match="days"):
        board.sessions(NOW, 23)


def test_confirmed_trade_ratios_and_independent_ownership_date():
    disclosed_at = NOW - timedelta(hours=6)
    publication = board.Publication(
        turnover=[_turnover(name="삼성전자"), _turnover(HYNIX, value=90_000, name="SK하이닉스")],
        foreign_flow=[_flow(), _flow(HYNIX, buy=6_000, sell=14_000)],
        ownership=[_ownership(published_at=disclosed_at), _ownership(HYNIX, percent=0),
                   _ownership(as_of=TODAY, percent=62, published_at=NOW + timedelta(hours=6))],
        rankings=[board.RankingDay(MARKET, TODAY, "krx_turnover", NOW - timedelta(minutes=30))],
    )
    got = _view(publication, favorites=({"ticker": HYNIX, "name": "SK하이닉스"}, {"ticker": SEC}))
    assert got["status"] == "partial"
    assert got["ranking"]["as_of"] == TODAY.isoformat()
    samsung, hynix = got["ranking"]["items"]
    assert (samsung["ticker"], hynix["ticker"]) == (SEC, HYNIX)
    assert samsung["latest"]["participation_pct"] == pytest.approx(10)
    assert samsung["latest"]["buy_sell_imbalance"] == pytest.approx(0.2)
    assert hynix["latest"]["buy_sell_imbalance"] == pytest.approx(-0.4)
    assert samsung["latest"]["turnover"]["unit"] == "KRW"
    assert samsung["latest"]["foreign_flow"]["source"] == "kis_stock_daily"
    assert samsung["latest"]["ownership"]["status"] == "not_published"
    assert samsung["latest"]["ownership"]["ratio_pct"] is None
    assert samsung["latest_ownership"] == {
        "as_of": PREVIOUS.isoformat(), "source": "krx_ownership", "status": "available",
        "published_at": disclosed_at.isoformat(), "ratio_pct": 47.5, "unit": "percent",
    }
    assert hynix["latest_ownership"]["ratio_pct"] == 0
    assert [row["ticker"] for row in got["favorites"]["items"]] == [HYNIX, SEC]
    points = got["favorites"]["items"][1]["history"]
    assert points[0]["turnover"]["status"] == "no_data"
    assert points[0]["ownership"]["ratio_pct"] == 47.5
    assert points[1]["ownership"]["ratio_pct"] is None


@pytest.mark.parametrize(("total", "buy", "sell", "net", "participation", "part_status", "bias", "bias_status"), [
    (100, 0, 0, None, 0, "available", None, "zero_denominator"),
    (100, 10, 10, None, 10, "available", 0, "available"),
    (0, 0, 0, None, None, "zero_denominator", None, "zero_denominator"),
    (100, 7, None, None, None, "missing_input", None, "missing_input"),
    (100, None, None, 13, None, "missing_input", None, "missing_input"),
    (100, None, None, 0, None, "missing_input", None, "missing_input"),
    (100, None, None, None, None, "no_data", None, "no_data"),
    (None, 20, 10, None, None, "no_data", 1 / 3, "available"),
    (100, 120, 12, None, None, "inconsistent_amounts", None, "inconsistent_amounts"),
])
def test_zero_denominators_missing_and_net_only(total, buy, sell, net, participation, part_status, bias, bias_status):
    got = _view(board.Publication(
        turnover=[_turnover(value=total)], foreign_flow=[_flow(buy=buy, sell=sell, net_krw=net)],
    ))["favorites"]["items"][0]["history"][-1]
    assert got["participation_pct"] == participation
    assert got["participation_status"] == part_status
    assert got["buy_sell_imbalance"] == bias
    assert got["imbalance_status"] == bias_status
    assert got["foreign_flow"]["buy_krw"] == buy
    assert got["foreign_flow"]["sell_krw"] == sell
    assert got["foreign_flow"]["net_krw"] == (buy - sell if buy is not None and sell is not None else net)


@pytest.mark.parametrize(("turnover_extra", "flow_extra", "field", "status"), [
    ({}, {"venue": "nxt"}, "foreign_flow", "scope_mismatch"),
    ({}, {"venue": "consolidated"}, "foreign_flow", "scope_mismatch"),
    ({"venue": "nxt"}, {}, "turnover", "scope_mismatch"),
    ({}, {"unit": "million_krw"}, "foreign_flow", "unit_mismatch"),
    ({}, {"unit": None}, "foreign_flow", "unit_mismatch"),
    ({}, {"venue": None}, "foreign_flow", "scope_mismatch"),
    ({}, {"market": "kosdaq"}, "foreign_flow", "no_data"),
])
def test_different_market_venue_or_unverified_amount_unit_does_not_join(turnover_extra, flow_extra, field, status):
    response = _view(board.Publication(
        turnover=[_turnover(**turnover_extra)], foreign_flow=[_flow(**flow_extra)],
    ))
    got = response["favorites"]["items"][0]["history"][-1]
    assert got[field]["status"] == status
    assert got["participation_pct"] is None
    assert got["participation_status"] == status
    if field == "foreign_flow":
        assert got["buy_sell_imbalance"] is None
        assert response["sources"]["foreign_flow"]["status"] == "no_data"


@pytest.mark.parametrize(("turnover_scope", "flow_scope", "status", "bias"), [
    ("regular", "all_sessions", "scope_mismatch", 0.2),
    ("all_sessions", "regular", "scope_mismatch", 0.2),
    (None, "all_sessions", "no_data", 0.2),
    ("all_sessions", None, "no_data", None),
    ("unverified", "unverified", "no_data", None),
])
def test_same_venue_needs_matching_confirmed_execution_windows(turnover_scope, flow_scope, status, bias):
    # Buy amount exceeds the unrelated denominator. It must not invalidate the same-row imbalance.
    got = _view(board.Publication(
        turnover=[_turnover(HYNIX, value=100, trade_scope=turnover_scope)],
        foreign_flow=[_flow(HYNIX, buy=120, sell=80, trade_scope=flow_scope)],
    ), favorites=({"ticker": HYNIX},))["favorites"]["items"][0]["history"][-1]
    assert got["turnover"]["value_krw"] == 100
    assert got["foreign_flow"]["buy_krw"] == 120
    assert got["participation_pct"] is None
    assert got["participation_status"] == status
    assert got["buy_sell_imbalance"] == bias
    assert got["imbalance_status"] == ("available" if bias is not None else "no_data")


def test_ownership_requires_listed_share_basis_and_preserves_older_valid_day():
    publication = board.Publication(ownership=[
        _ownership(as_of=PREVIOUS, percent=0),
        _ownership(as_of=TODAY, percent=75, denominator="foreign_limit"),
        _ownership(HYNIX, as_of=TODAY, percent=20, denominator=None),
        _ownership(HYNIX, as_of=PREVIOUS, percent=50, published_at=NOW - timedelta(hours=2)),
        _ownership(HYNIX, as_of=PREVIOUS, percent=5, denominator="foreign_limit", published_at=NOW),
    ])
    samsung, hynix = _view(publication, favorites=({"ticker": SEC}, {"ticker": HYNIX}))["favorites"]["items"]
    for row in (samsung, hynix):
        assert row["history"][-1]["ownership"]["ratio_pct"] is None
        assert row["history"][-1]["ownership"]["status"] == "no_data"
        assert row["latest_ownership"]["as_of"] == PREVIOUS.isoformat()
    assert samsung["latest_ownership"]["ratio_pct"] == 0
    assert hynix["latest_ownership"]["ratio_pct"] == 50
    wrong_basis = _view(board.Publication(ownership=[_ownership(denominator="foreign_limit")]))
    assert wrong_basis["sources"]["ownership"]["status"] == "no_data"


def test_completed_rank_only_for_selected_market_and_stable_top_50():
    turnover = [_turnover(f"{i:06d}.KS", as_of=PREVIOUS, value=100) for i in reversed(range(1, 53))]
    turnover.extend([
        _turnover("999999.KS", as_of=PREVIOUS, value=90_000, venue="nxt"),
        _turnover("900001.KS", value=12_000),  # A stock total does not certify a market-wide ranking.
        _turnover("0126Z0.KQ", value=90_000, market="kosdaq"),
        _turnover("999999.KQ", value=90_001, market="kosdaq", confirmed=False),
    ])
    publication = board.Publication(
        turnover=turnover, ownership=[_ownership(as_of=PREVIOUS)],
        rankings=[board.RankingDay(MARKET, PREVIOUS, "krx_turnover"),
                  board.RankingDay(MARKET, TODAY, "krx_turnover", confirmed=False),
                  board.RankingDay("kosdaq", TODAY, "krx_turnover")],
    )
    got = _view(publication, days=1, favorites=())
    rank = got["ranking"]
    assert got["sessions"] == [TODAY.isoformat()]
    assert rank["as_of"] == PREVIOUS.isoformat()
    assert rank["status"] == "stale"
    assert len(rank["items"]) == 50
    assert [(row["ticker"], row["rank"]) for row in rank["items"]] == [(f"{i:06d}.KS", i) for i in range(1, 51)]
    assert got["sources"]["foreign_flow"]["status"] == "source_unavailable"
    kq = _view(publication, market="kosdaq", favorites=({"ticker": SEC}, {"ticker": "0126Z0"}),
               favorite_markets={"0126Z0": "KOSDAQ"})
    assert [row["ticker"] for row in kq["ranking"]["items"]] == ["0126Z0.KQ"]
    assert [row["ticker"] for row in kq["favorites"]["items"]] == ["0126Z0.KQ"]


def test_missing_source_and_no_observations_remain_distinct():
    got = _view(board.Publication(ownership=[]))
    assert got["status"] == "no_data"
    assert got["ranking"]["status"] == "source_unavailable"
    past, last = got["favorites"]["items"][0]["history"]
    assert [past["date"], last["date"]] == [PREVIOUS.isoformat(), TODAY.isoformat()]
    assert last["turnover"]["status"] == "source_unavailable"
    assert last["foreign_flow"]["status"] == "source_unavailable"
    assert last["ownership"]["status"] == "no_data"


def test_ranking_holdings_cutoff_and_holdings_only_partial_result():
    publication = board.Publication(
        turnover=[_turnover(as_of=FRIDAY)],
        ownership=[_ownership(as_of=FRIDAY, percent=48), _ownership(as_of=PREVIOUS, percent=50)],
        rankings=[board.RankingDay(MARKET, FRIDAY, "krx_turnover")],
    )
    got = _view(publication, days=1)
    ranked = got["ranking"]["items"][0]
    favorite = got["favorites"]["items"][0]
    assert ranked["latest"]["ownership"]["ratio_pct"] == ranked["latest_ownership"]["ratio_pct"] == 48
    assert favorite["history"][0]["ownership"]["ratio_pct"] is None
    assert favorite["latest_ownership"]["ratio_pct"] == 50
    assert got["favorites"]["status"] == "partial"
