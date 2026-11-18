"""#605 leveraged / inverse ETFs' net assets and units, and the KTB yields, read from krx_daily (#601).
The rows are hand-made in KRX's documented shape (tests/fixtures/krx/etp_days.json, kts_days.json - no call)."""

import asyncio
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from desk import krx, krx_etp
from desk import morning_brief as mb
from desk.db import krx_daily, krx_days, make_engine, migrate
from tests.test_morning_brief import NOW, _sources

FIX = Path(__file__).parent / "fixtures" / "krx"
TODAY = date(2026, 11, 18)
AT = datetime(2026, 11, 18, 0, 0, tzinfo=UTC)


@pytest.fixture
def engine(db_url):
    migrate(db_url)
    eng = make_engine(db_url)
    yield eng
    eng.dispose()


def _store(engine, *names: str) -> None:
    store = krx.KrxDaily(engine, client=None)
    for name, api in (("etp_days", krx_etp.ETF_API), ("kts_days", krx_etp.KTB_API)):
        if name in names:
            for day, rows in json.loads((FIX / f"{name}.json").read_text(encoding="utf-8")).items():
                store.store(api, datetime.strptime(day, "%Y%m%d").date(), rows, AT)


@pytest.fixture
def stored(engine):
    _store(engine, "etp_days", "kts_days")
    return engine


def test_the_isin_form_of_a_short_code():
    assert krx_etp.isin("122630") == "KR7122630007" and krx_etp.isin("005930") == "KR7005930003"


def test_net_assets_units_streak_and_nav_gap_of_the_latest_day(stored):
    got = krx_etp.etf_flows(stored, TODAY)
    assert got["date"] == "2026-11-17"
    by = {i["code"]: i for i in got["items"]}
    assert list(by) == ["122630", "252670", "114800"]              # ETFS order; KODEX 200 is not watched
    lev = by["122630"]
    assert lev["net_assets"] == 2_600_000_000_000 and lev["net_change_pct"] == 4.0
    assert lev["units_change"] == 3_000_000 and round(lev["flow"]) == 73_500_360_000
    assert lev["streak"] == 3                                      # 11/12 → 11/16 across the empty 11/13
    assert lev["nav_gap_pct"] == 0.08
    inv2 = by["252670"]                                            # stored by its ISIN
    assert inv2["net_change_pct"] == -5.66 and inv2["streak"] == -1 and inv2["nav_gap_pct"] == -0.17
    new = by["114800"]                                             # one day only: no change, no streak
    assert new["net_change_pct"] is None and new["flow"] is None and new["streak"] == 0
    assert krx_etp.etf_line(lev) == "KODEX 레버리지 순자산 26,000억(+4.00%), 좌수 3일 연속 증가(전일 대비 +735억), NAV 괴리 +0.08%"
    assert krx_etp.etf_line(inv2) == "KODEX 200선물인버스2X 순자산 10,000억(-5.66%), 좌수 감소(전일 대비 -417억), NAV 괴리 -0.17%"


def test_the_benchmark_ktb_yields_and_their_change(stored):
    got = krx_etp.ktb_yields(stored, TODAY)
    assert got["date"] == "2026-11-17"
    assert got["years"][3] == {"yield": 2.512, "change_bp": 2.1, "name": "국고02750-2809(25-5)"}   # 지표, not the busier 경과
    assert got["years"][10]["yield"] == 2.853 and got["years"][10]["change_bp"] == -0.5
    assert krx_etp.ktb_line(got) == "국고 3년 2.512%(+2.1bp) · 국고 10년 2.853%(-0.5bp)"


def test_the_yield_change_is_the_same_issue_and_none_across_a_benchmark_roll(engine):
    days = json.loads((FIX / "kts_days.json").read_text(encoding="utf-8"))
    store = krx.KrxDaily(engine, client=None)
    # 11/16: the 3-year benchmark tag sat on another issue (yield 2.505) - 11/17's issue is still compared with itself
    rolled = [{**r, "GOVBND_ISU_TP_NM": "경과" if r["ISU_CD"] == "KR103502GE90" else
               "지표" if r["ISU_CD"] == "KR103502GD18" else r["GOVBND_ISU_TP_NM"]} for r in days["20261116"]]
    store.store(krx_etp.KTB_API, date(2026, 11, 16), rolled, AT)
    store.store(krx_etp.KTB_API, date(2026, 11, 17), days["20261117"], AT)
    assert krx_etp.ktb_yields(engine, TODAY)["years"][3]["change_bp"] == 2.1        # 2.512 - 2.491, not - 2.505
    # the new benchmark not traded the day before: no change rather than another issue's
    store.store(krx_etp.KTB_API, date(2026, 11, 16), [r for r in days["20261116"] if r["ISU_CD"] != "KR103502GE90"], AT)
    got = krx_etp.ktb_yields(engine, TODAY)["years"]
    assert got[3]["yield"] == 2.512 and got[3]["change_bp"] is None and got[10]["change_bp"] == -0.5


def test_nothing_stored_or_nothing_recent_says_nothing(engine):
    assert krx_etp.summary(engine, TODAY) == {"etf": None, "ktb": None}
    _store(engine, "etp_days", "kts_days")
    assert krx_etp.summary(engine, date(2026, 11, 30)) == {"etf": None, "ktb": None}      # 13 days old
    assert krx_etp.etf_flows(engine, date(2026, 11, 16))["date"] == "2026-11-16"            # never a later day
    assert krx_etp.tips_view(engine, date(2026, 11, 30)) == {"etf_flow": [], "ktb": None}


def test_the_tips_say_which_side_the_money_is_on(stored):
    view = krx_etp.tips_view(stored, TODAY)
    texts = {r["code"]: r["text"] for r in view["etf_flow"]}
    assert texts["122630"].endswith("— 상승 베팅 자금이 3일째 들어오고 있어요")
    assert "자금이" not in texts["252670"]                          # one day down is no run
    assert view["ktb"]["text"] == "국고 3년 2.512%(+2.1bp) · 국고 10년 2.853%(-0.5bp)"
    assert view["ktb"]["years"]["3"]["change_bp"] == 2.1


def _brief_bundle(engine):
    return mb.bundle(asyncio.run(mb.collect(_sources(krx=lambda: krx_etp.summary(engine, TODAY)), NOW)), NOW)


def test_the_morning_brief_says_them_with_their_date(stored):
    b = _brief_bundle(stored)
    assert [i["name"] for i in b["krx"]["etf"]["items"]] == ["KODEX 레버리지", "KODEX 200선물인버스2X"]   # brief ones
    flow = mb.rules(b)["kr_flow"]
    assert (" 레버리지 · 인버스 ETF(11/17 기준): KODEX 레버리지 순자산 26,000억(+4.00%), 좌수 3일 연속 증가(전일 대비 +735억), "
            "NAV 괴리 +0.08% / KODEX 200선물인버스2X 순자산 10,000억(-5.66%), 좌수 감소(전일 대비 -417억), NAV 괴리 -0.17%.") in flow
    assert flow.endswith(" 국채 금리(11/17 기준): 국고 3년 2.512%(+2.1bp) · 국고 10년 2.853%(-0.5bp).")
    check = mb.allowed_numbers(b)
    assert mb._ok(flow, check, 2000) == flow                       # the desk's own line passes its number check


@pytest.mark.parametrize(("sentence", "kept"), [
    ("KODEX 레버리지는 좌수가 3일 연속 늘고 순자산이 4.00% 올랐어요.", True),
    ("11/17 기준 국고 3년 금리는 2.512%로 2.1bp 올랐어요.", True),
    ("KODEX 레버리지 순자산이 5.66% 줄었어요.", False),             # the inverse's number on the leveraged
    ("국고 10년은 2.1bp 올랐어요.", False),                         # the 3-year's change on the 10-year
    ("국고 3년 금리가 2.1bp 내렸어요.", False),                     # +2.1bp said as a fall
    ("KODEX 레버리지 좌수가 4일 연속 늘었어요.", False),             # not the streak
    ("11/18 기준 국고 10년 2.853%예요.", False),                    # not the data's date
])
def test_a_model_sentence_on_them_is_checked_like_the_rest(stored, sentence, kept):
    check = mb.allowed_numbers(_brief_bundle(stored))
    assert (mb._ok(sentence, check, 300) is not None) is kept


def test_without_krx_rows_the_brief_is_as_before_and_a_failure_is_named(engine):
    b = _brief_bundle(engine)
    assert b["krx"] == {"etf": None, "ktb": None} and "ETF" not in mb.rules(b)["kr_flow"]

    def down():
        raise RuntimeError("db down")
    b = mb.bundle(asyncio.run(mb.collect(_sources(krx=down), NOW)), NOW)
    assert "krx" in b["missing"] and b["krx"] == {"etf": None, "ktb": None}


def test_a_missing_trading_day_is_no_change_and_ends_the_streak(engine):
    _store(engine, "etp_days", "kts_days")
    with engine.begin() as conn:     # 11/16 never collected (a failed day not caught up yet)
        conn.execute(krx_days.delete().where(krx_days.c.bas_dd == date(2026, 11, 16)))
        conn.execute(krx_daily.delete().where(krx_daily.c.bas_dd == date(2026, 11, 16)))
    lev = next(i for i in krx_etp.etf_flows(engine, TODAY)["items"] if i["code"] == "122630")
    assert lev["net_change_pct"] is None and lev["flow"] is None and lev["streak"] == 0   # not 11/12 → 11/17 as one day
    assert krx_etp.ktb_yields(engine, TODAY)["years"][3]["change_bp"] is None


def test_the_model_kr_flow_is_read_whole_and_a_cut_sentence_is_never_kept(stored):
    b = _brief_bundle(stored)
    draft = mb.rules(b)
    answer = {k: draft[k] for k in mb.FIELDS}
    assert len(draft["kr_flow"]) > 300
    assert mb.merge(draft, answer, mb.allowed_numbers(b))["kr_flow"] == draft["kr_flow"]   # the KTB line too
    cut = mb._ok("코스피는 조용했어요. KODEX 레버리지 좌수 3일 연속 증가했고 순자산이 늘었어요", mb.allowed_numbers(b), 30)
    assert cut == "코스피는 조용했어요."
