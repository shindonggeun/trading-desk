"""#579 NQ=F selling alarms on 5-minute bars (desk.nq_watch)."""

from datetime import UTC, datetime, timedelta

from desk import nq_watch

START = datetime(2026, 11, 10, 23, 0, tzinfo=UTC)          # 18:00 ET Tuesday: a CME day opens


def _bars(n: int = 2100, last_ret: float | None = None, last_volume: float = 100.0) -> list[dict]:
    """``n`` contiguous 5-minute bars wobbling ±0.02%, volume 100; the last one moved ``last_ret``."""
    out, close = [], 20000.0
    for i in range(n):
        ret = (0.0002 if i % 2 else -0.0002) if (last_ret is None or i < n - 1) else last_ret
        close *= 1 + ret
        out.append({"at": (START + i * nq_watch.BAR).isoformat(timespec="seconds"), "open": close,
                    "high": close, "low": close, "close": close,
                    "volume": last_volume if i == n - 1 else 100.0})
    return out


def _after(bars: list[dict]) -> datetime:
    return datetime.fromisoformat(bars[-1]["at"]) + nq_watch.BAR


def test_a_fast_drop_on_heavy_volume_is_strong_selling():
    bars = _bars(last_ret=-0.004, last_volume=400)
    got = nq_watch.judge(bars, _after(bars))
    assert [(a["rule"], a["severity"], a["volume_times"]) for a in got] == [("nq_drop", "strong", 4.0)]
    assert got[0]["value"] == -0.4 and got[0]["kind"] == "nq_alert"
    assert nq_watch.text(got[0]) == ("🔵 나스닥 선물 급락 (매도세 강함) · 15:00 · 5분 −0.40% (하위 2% −0.02%)"
                                    " · 거래량 같은 시각 평소의 4.0배")


def test_a_drop_without_volume_is_left_to_the_fifteen_minute_rule():
    bars = _bars(last_ret=-0.004, last_volume=120)
    got = nq_watch.judge(bars, _after(bars))
    assert [(a["rule"], a["severity"]) for a in got] == [("nq_drop15", "caution")]
    assert nq_watch.judge(_bars(last_ret=0.004, last_volume=400), _after(bars)) == []     # a jump up: nothing


def test_a_bar_still_trading_is_not_judged_and_a_thin_sample_says_nothing():
    bars = _bars(last_ret=-0.004, last_volume=400)
    still = nq_watch.judge(bars, _after(bars) - timedelta(minutes=1))
    assert still == nq_watch.judge(bars[:-1], _after(bars[:-1])) and "nq_drop" not in [a["rule"] for a in still]
    short = _bars(n=150, last_ret=-0.004, last_volume=400)
    assert nq_watch.judge(short, _after(short)) == []


def test_two_percent_under_the_sessions_high_is_flagged():
    bars = _bars()
    bars[-1] = {**bars[-1], "close": bars[-1]["close"] * 0.98, "low": bars[-1]["close"] * 0.98}
    got = nq_watch.judge(bars, _after(bars))
    high = [a for a in got if a["rule"] == "nq_from_high"]
    assert len(high) == 1 and high[0]["severity"] == "caution"
    assert nq_watch.text(high[0]).startswith("🔵 나스닥 선물 장중 고점 대비 −2.0")


def test_cme_hours_skip_the_daily_break_and_the_weekend():
    et = (lambda d, h, m: datetime(2026, 11, d, h, m, tzinfo=nq_watch.US.zone))
    assert nq_watch.cme_open(et(17, 10, 0))                  # Tuesday morning
    assert not nq_watch.cme_open(et(17, 17, 30))             # the daily break
    assert nq_watch.cme_open(et(17, 18, 0))
    assert not nq_watch.cme_open(et(20, 17, 0))              # Friday's close
    assert not nq_watch.cme_open(et(21, 12, 0))              # Saturday
    assert not nq_watch.cme_open(et(22, 17, 59))
    assert nq_watch.cme_open(et(22, 18, 0))                  # Sunday evening opens Monday
    assert nq_watch.seconds_to_next(datetime(2026, 11, 17, 15, 3, 0, tzinfo=UTC)) == 120 + nq_watch.SETTLE_SEC
