"""#1010 the quote board's NXT price is the trade as last read (desk.estimate.NXT_TTL_SEC), stamped with its own
time - not the copy inside an estimate, which waits behind the estimate's cache and carries its compute time."""

import asyncio
import threading
from datetime import UTC, datetime, timedelta

import pytest

from desk.estimate import ESTIMATE_TTL_SEC, NXT_TTL_SEC
from tests.test_api import OWNER, PASSWORD
from tests.test_estimate import ANCHOR, Clock, _client, _estimator

FRIDAY_17H = datetime(2026, 11, 6, 8, 0, tzinfo=UTC)          # 17:00 KST, NXT after-market open


class Naver:
    """Naver's NXT quote: each read is a later trade (+100 won, +5 s); counts the reads."""

    def __init__(self):
        self.reads = 0
        self.lock = threading.Lock()

    def __call__(self, code):
        with self.lock:
            self.reads += 1
            n = self.reads
        return 220000.0 + 100 * (n - 1), datetime(2026, 11, 6, 7, 59, 30, tzinfo=UTC) + timedelta(seconds=5 * (n - 1)), True


@pytest.fixture
def anchors():
    keys = [("SAMSUNGUSDT", f"2026-11-06T07:5{m}:00+00:00") for m in range(8, 10)]
    for key in keys:
        ANCHOR[key] = "165.00"
    yield
    for key in keys:
        ANCHOR.pop(key, None)


def test_the_nxt_cache_is_shorter_than_the_estimates():
    assert NXT_TTL_SEC < ESTIMATE_TTL_SEC and NXT_TTL_SEC <= 5


def test_the_board_takes_the_newer_nxt_trade_while_the_estimate_is_still_cached(db_url, monkeypatch, anchors):
    async def post(calendar, ticker, now=None):
        return "post"

    monkeypatch.setattr("desk.api.sessions.session", post)
    naver, clock = Naver(), Clock()
    est, _ = _estimator(FRIDAY_17H, clock=clock, after_hours=naver)
    with _client(db_url, est) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        first = client.get("/api/estimates?tickers=005930.KS").json()["items"][0]
        clock.t += NXT_TTL_SEC + 1                                  # the estimate (ESTIMATE_TTL_SEC) is still cached
        second = client.get("/api/estimates?tickers=005930.KS").json()["items"][0]
    assert (first["state"], first["quote_source"], first["price"]) == ("live", "nxt", 220000.0)
    assert second["price"] == 220100.0 and naver.reads == 2
    assert second["fetched_at"] == second["traded_at"] == "2026-11-06T07:59:35+00:00"   # the trade's own time
    assert second["change_pct"] == round((220100 / 218000 - 1) * 100, 2)               # the same base as before
    assert second["change_split"]["extended"]["pct"] == round((220100 / 218000 - 1) * 100, 2)


def test_askers_of_one_code_at_once_share_one_naver_read(anchors):
    naver = Naver()
    est, _ = _estimator(FRIDAY_17H, after_hours=naver)

    async def go():
        return await asyncio.gather(*(est._nxt_trade("005930") for _ in range(5)))

    got = asyncio.run(go())
    assert naver.reads == 1 and len({g[0] for g in got}) == 1
