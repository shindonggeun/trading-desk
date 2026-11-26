"""#994 many symbols' quotes in one Toss call (desk.broker.toss.TossBroker.quotes, Broker.quotes), and what that
does to the collector's demand pass: one GET /api/v1/prices for every wanted quote instead of one per ticker.

Fake HTTP only (httpx.MockTransport): no Toss, no key."""

import asyncio
import logging
import time

import fakeredis
import httpx
import pytest

from desk import demand, outbound
from desk import shared_cache as sc
from desk.broker import MockBroker, TossBroker
from desk.broker.base import QUOTES_PARALLEL
from desk.broker.toss import ALONE_MAX, BATCH_OFF_SEC, PRICES_MAX_SYMBOLS, RATE_LIMIT_RETRIES, TossError
from desk.prices import PriceSource

MARKER = "synthetic-provider-detail"
TOKEN = "synthetic-token"


class FakeToss:
    """GET /api/v1/prices as the spec has it (1-200 comma-joined symbols, else 400): a row per known symbol.

    ``statuses`` are answered in turn first (429 with Retry-After ...); ``refuse_many`` is the status of every
    many-symbol ask; ``rows_for_many`` cuts a many-symbol answer to that many rows; ``alone_status`` answers
    every one-symbol ask; ``token_status`` every token request. ``refuse_unknown`` is the status of a
    many-symbol ask holding a symbol it has no price for, ``unknown_alone`` that of such a symbol's own ask
    (None: 200 without a row; "network": no answer)."""

    def __init__(self, prices: dict, *, statuses=(), refuse_many: int | None = None, rows_for_many: int | None = None,
                 alone_status: int | None = None, token_status: int | None = None,
                 refuse_unknown: int | None = None, unknown_alone: int | str | None = None):
        self.prices = prices
        self.statuses = list(statuses)
        self.refuse_many = refuse_many
        self.rows_for_many = rows_for_many
        self.alone_status = alone_status
        self.token_status = token_status
        self.refuse_unknown = refuse_unknown
        self.unknown_alone = unknown_alone
        self.asks: list[list[str]] = []
        self.tokens = 0
        self.answer = None                     # a body to give instead of the rows

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            self.tokens += 1
            if self.token_status:
                return httpx.Response(self.token_status, text=MARKER)
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3600})
        assert request.url.path == "/api/v1/prices"
        symbols = request.url.params["symbols"].split(",")
        self.asks.append(symbols)
        if self.statuses:
            return httpx.Response(self.statuses.pop(0), text=MARKER, headers={"Retry-After": "1"})
        if not 1 <= len(symbols) <= PRICES_MAX_SYMBOLS:
            return httpx.Response(400, json={"error": {"code": "invalid-request", "message": MARKER}})
        if len(symbols) > 1 and self.refuse_many:
            return httpx.Response(self.refuse_many, json={"error": {"code": "invalid-request", "message": MARKER}})
        unknown = [s for s in symbols if s not in self.prices]
        if unknown and len(symbols) > 1 and self.refuse_unknown:
            return httpx.Response(self.refuse_unknown, json={"error": {"code": "not-found", "message": MARKER}})
        if unknown and len(symbols) == 1 and self.unknown_alone == "network":
            raise httpx.ConnectError(MARKER, request=request)
        if unknown and len(symbols) == 1 and self.unknown_alone:
            return httpx.Response(self.unknown_alone, json={"error": {"code": "not-found", "message": MARKER}})
        if len(symbols) == 1 and self.alone_status:
            return httpx.Response(self.alone_status, text=MARKER, headers={"Retry-After": "1"})
        if self.answer is not None:
            return httpx.Response(200, json=self.answer)
        rows = [{"symbol": s, "timestamp": "2026-11-25T10:00:00+09:00", "lastPrice": str(self.prices[s]),
                 "currency": "KRW" if s.isdigit() else "USD"} for s in symbols if s in self.prices]
        if len(symbols) > 1 and self.rows_for_many is not None:
            rows = rows[:self.rows_for_many]
        return httpx.Response(200, json={"result": rows})


def _broker(fake: FakeToss, redact: bool = False) -> TossBroker:
    return TossBroker("id", "secret", http=httpx.AsyncClient(base_url="https://toss.test",
                                                                transport=httpx.MockTransport(fake)),
                      redact_errors=redact)


def _singles(asks: list[list[str]]) -> list[str]:
    assert all(len(a) == 1 for a in asks)
    return sorted(a[0] for a in asks)


def _desk_logs(caplog) -> str:
    """What the desk's own loggers wrote (httpx' request lines are WARNING-only in the app, notify_channels)."""
    return "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("desk"))


def _turned_off(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "desk.broker.toss" and r.levelno >= logging.WARNING]


@pytest.fixture
def no_wait(monkeypatch):
    """_get's 429 waits pass at once."""
    async def wait(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", wait)


def test_quotes_ask_up_to_two_hundred_symbols_a_call():
    prices = {f"S{i:03d}": 100.0 + i for i in range(450)}
    fake = FakeToss(prices)
    outbound.reset()
    got = asyncio.run(_broker(fake).quotes(list(prices)))
    assert [len(a) for a in fake.asks] == [200, 200, 50]
    assert [s for a in fake.asks for s in a] == list(prices)
    assert got == {s: (p, "USD") for s, p in prices.items()}
    assert outbound.snapshot()["toss"] == 3                           # one count per GET, as quote()'s


@pytest.mark.parametrize("redact", [False, True])
def test_a_symbol_left_out_or_without_a_usable_price_is_read_alone_once_then_left_out(redact):
    fake = FakeToss({})
    fake.answer = {"result": [
        {"symbol": "nvda", "lastPrice": "181.5", "currency": "USD"},       # Toss' case is not ours
        {"symbol": "005930", "lastPrice": "74,800", "currency": "KRW"},
        {"symbol": "NOCUR", "lastPrice": "5"},
        {"symbol": "NAN", "lastPrice": "NaN", "currency": "USD"},
        {"symbol": "INF", "lastPrice": "1e999", "currency": "USD"},
        {"symbol": "ZERO", "lastPrice": "0", "currency": "USD"},
        {"symbol": "NEG", "lastPrice": "-3", "currency": "USD"},
        {"symbol": "TEXT", "lastPrice": "abc", "currency": "USD"},
        {"symbol": "CUR", "lastPrice": "10", "currency": {"reason": MARKER}},
        {"symbol": 7, "lastPrice": "10"},
        MARKER,
        {"symbol": "EXTRA", "lastPrice": "10", "currency": "USD"},          # not asked
    ]}
    broker = _broker(fake, redact)
    asked = ["NVDA", "005930", "NOCUR", "NAN", "INF", "ZERO", "NEG", "TEXT", "CUR", "GONE"]
    got = asyncio.run(broker.quotes(asked))
    assert got == {"NVDA": (181.5, "USD"), "005930": (74800.0, "KRW"), "NOCUR": (5.0, None)}
    left_out = ["CUR", "GONE", "INF", "NAN", "NEG", "TEXT", "ZERO"]
    assert fake.asks[0] == asked and _singles(fake.asks[1:]) == left_out   # each read alone once, no more
    assert broker.batching()                                           # many rows: the batch stays on


def test_symbols_asked_twice_or_in_another_case_are_asked_once_and_answered_as_asked():
    fake = FakeToss({"NVDA": 181.5, "AAPL": 230.0})
    got = asyncio.run(_broker(fake).quotes(["NVDA", "nvda", "AAPL", "NVDA"]))
    assert fake.asks == [["NVDA", "AAPL"]]
    assert got == {"NVDA": (181.5, "USD"), "nvda": (181.5, "USD"), "AAPL": (230.0, "USD")}


@pytest.mark.parametrize("redact", [False, True])
def test_a_refused_many_symbol_ask_reads_one_by_one_for_a_while_and_says_so_each_time(caplog, redact):
    prices = {"AAPL": 230.0, "NVDA": 181.5, "TSLA": 350.0}
    fake = FakeToss(prices, refuse_many=400)
    broker = _broker(fake, redact)
    with caplog.at_level(logging.INFO):
        first = asyncio.run(broker.quotes(["AAPL", "NVDA", "TSLA"]))
        tripped = len(fake.asks)
        again = asyncio.run(broker.quotes(["AAPL", "NVDA"]))           # still off: no many-symbol ask
        off = len(fake.asks)
        broker._batch_off_until = 0.0                                  # BATCH_OFF_SEC later
        fake.refuse_many = None
        later = asyncio.run(broker.quotes(["AAPL", "NVDA"]))
        on = len(fake.asks)
        fake.refuse_many = 400
        asyncio.run(broker.quotes(["AAPL", "NVDA"]))                   # refused again: off again
    assert first == {s: (p, "USD") for s, p in prices.items()}
    assert again == later == {"AAPL": (230.0, "USD"), "NVDA": (181.5, "USD")}
    assert fake.asks[0] == ["AAPL", "NVDA", "TSLA"] and _singles(fake.asks[1:tripped]) == ["AAPL", "NVDA", "TSLA"]
    assert _singles(fake.asks[tripped:off]) == ["AAPL", "NVDA"]
    assert fake.asks[off:on] == [["AAPL", "NVDA"]]
    assert fake.asks[on] == ["AAPL", "NVDA"] and _singles(fake.asks[on + 1:]) == ["AAPL", "NVDA"]
    assert not broker.batching()
    warned = _turned_off(caplog)
    assert warned == [f"toss many-symbol prices answered 400 to {n} symbols, each priced alone: quotes are read "
                      f"one symbol at a time for {BATCH_OFF_SEC:.0f} s" for n in (3, 2)]
    for secret in (MARKER, TOKEN, "AAPL", "NVDA", "TSLA"):              # a status and counts, nothing else
        assert secret not in _desk_logs(caplog)


@pytest.mark.parametrize("refused,alone", [(404, 404), (400, 400), (400, 404), (400, None)],
                         ids=["404-404", "400-400", "400-404", "400-no-row"])
@pytest.mark.parametrize("redact", [False, True])
def test_a_refused_ask_keeps_the_symbol_to_blame_out_of_many_symbol_asks_and_the_batch_on(caplog, refused,
                                                                                         alone, redact):
    """#994 security review: 20 listed and one unknown symbol, the many-symbol ask refused for that one (a 404, a
    400) - its own read refused too (a 4xx past 401 · 403 · 429: a 400 for many, a 404 alone as well), or with no
    price (None: 200 without its row), blames it alone. It is asked alone for BATCH_OFF_SEC; the other 20 stay in
    one ask: 2 GETs the next time, not 21."""
    symbols = [f"S{i:02d}" for i in range(20)]
    fake = FakeToss({s: 10.0 + i for i, s in enumerate(symbols)}, refuse_unknown=refused, unknown_alone=alone)
    broker = _broker(fake, redact)
    with caplog.at_level(logging.INFO):
        first = asyncio.run(broker.quotes([*symbols, "ZZZZ"]))
        tripped = len(fake.asks)
        again = asyncio.run(broker.quotes([*symbols, "ZZZZ"]))
    assert first == again == {s: (10.0 + i, "USD") for i, s in enumerate(symbols)}
    assert fake.asks[0] == [*symbols, "ZZZZ"] and _singles(fake.asks[1:tripped]) == sorted([*symbols, "ZZZZ"])
    assert sorted(fake.asks[tripped:]) == [symbols, ["ZZZZ"]]           # the next time: 2 GETs
    assert broker.batching() and set(broker._alone_until) == {"ZZZZ"}
    said = (f"toss many-symbol prices answered {refused} to 21 symbols: 1 of them refused alone too, asked alone "
            f"for {BATCH_OFF_SEC:.0f} s")
    assert _turned_off(caplog) == [said]
    for secret in (MARKER, TOKEN, "ZZZZ", "S00"):                       # a status and counts, nothing else
        assert secret not in _desk_logs(caplog)


@pytest.mark.parametrize("alone", [500, "network"])
def test_a_refused_ask_whose_reads_alone_fail_otherwise_judges_nothing(caplog, alone):
    """#994 security review: the batch goes off only when every symbol of a refused ask has a price alone. A read
    alone failing another way (a 5xx, the network - redacted, a TossError of no status as well) blames no one."""
    symbols = ["AAPL", "NVDA"]
    fake = FakeToss({"AAPL": 230.0, "NVDA": 181.5}, refuse_unknown=400, unknown_alone=alone)
    broker = _broker(fake, redact=True)
    with caplog.at_level(logging.WARNING):
        got = asyncio.run(broker.quotes([*symbols, "ZZZZ"]))
        asked = len(fake.asks)
        asyncio.run(broker.quotes([*symbols, "ZZZZ"]))
    assert got == {"AAPL": (230.0, "USD"), "NVDA": (181.5, "USD")}
    assert broker.batching() and broker._alone_until == {} and _turned_off(caplog) == []
    assert fake.asks[asked] == [*symbols, "ZZZZ"]                      # asked as many again


def test_symbols_kept_out_of_many_symbol_asks_are_capped():
    """#994 security review: at most ALONE_MAX at once - those past their time go first, then the soonest due.
    Never fewer than the collector's wanted keys: a blamed symbol still wanted is not let back in for room."""
    assert ALONE_MAX >= demand.MAX_KEYS
    broker = _broker(FakeToss({}))
    broker._alone_until = {f"OLD{i:02d}": 0.0 for i in range(ALONE_MAX)}   # all past their time
    broker._ask_alone(["NEW"], "answered 400 to 2 symbols")
    assert set(broker._alone_until) == {"NEW"}
    many = [f"S{i:02d}" for i in range(ALONE_MAX + 6)]
    broker._ask_alone(many, f"answered 400 to {len(many)} symbols")
    assert len(broker._alone_until) == ALONE_MAX
    assert set(broker._alone_until) == set(many[6:])                    # NEW, then the first six asked


@pytest.mark.parametrize("rows", [1, 0])
def test_a_thin_answer_whose_left_out_symbols_have_a_price_alone_turns_the_batch_off(caplog, rows):
    prices = {"AAPL": 230.0, "NVDA": 181.5, "TSLA": 350.0}
    fake = FakeToss(prices, rows_for_many=rows)
    broker = _broker(fake)
    with caplog.at_level(logging.WARNING):
        got = asyncio.run(broker.quotes(["AAPL", "NVDA", "TSLA"]))
    assert got == {s: (p, "USD") for s, p in prices.items()}
    # the row it gave stands; the others are read one by one
    assert _singles(fake.asks[1:]) == ["AAPL", "NVDA", "TSLA"][rows:]
    assert not broker.batching()
    said = (f"toss many-symbol prices answered {rows} row(s) for 3 symbols, {3 - rows} of the {3 - rows} left out "
            f"priced alone: quotes are read one symbol at a time for {BATCH_OFF_SEC:.0f} s")
    assert _turned_off(caplog) == [said]


@pytest.mark.parametrize("asked", [["AAPL", "ZZZZ"], ["YYYY", "ZZZZ"]], ids=["one-unknown", "only-unknown"])
def test_a_thin_answer_whose_left_out_symbols_have_no_price_alone_either_keeps_the_batch(caplog, asked):
    """An unlisted symbol (another exchange's, delisted, a typo) is the symbol's fault, not the batch's."""
    fake = FakeToss({"AAPL": 230.0})
    broker = _broker(fake)
    with caplog.at_level(logging.WARNING):
        got = asyncio.run(broker.quotes(asked))
        again = asyncio.run(broker.quotes(asked))
    assert got == again == ({"AAPL": (230.0, "USD")} if "AAPL" in asked else {})
    unknown = [s for s in asked if s != "AAPL"]
    assert [a for a in fake.asks if len(a) > 1] == [asked, asked]      # many-symbol again the next time
    assert _singles([a for a in fake.asks if len(a) == 1]) == sorted(unknown * 2)   # each unknown alone once a call
    assert broker.batching() and _turned_off(caplog) == []


def test_one_symbol_without_a_price_costs_one_get():
    fake = FakeToss({})
    broker = _broker(fake)
    assert asyncio.run(broker.quotes(["GONE"])) == {}
    assert fake.asks == [["GONE"]] and broker.batching()


def test_a_symbol_of_no_toss_form_is_never_sent():
    good = [f"S{i:02d}" for i in range(20)]
    fake = FakeToss({s: 1.0 for s in good})
    broker = _broker(fake)
    got = asyncio.run(broker.quotes([*good, "X^Y", "AB=C", "A,B", "", "TOO-LONG-FOR-A-SYMBOL"]))
    assert fake.asks == [good] and set(got) == set(good) and broker.batching()
    for odd in ("X^Y", "A,B", "AB=C", "TOO-LONG-FOR-A-SYMBOL", "NVDA\n"):
        with pytest.raises(TossError, match="^Not a symbol Toss lists$"):
            asyncio.run(broker.quote(odd))
    assert fake.asks == [good]                                         # quote() sends none of them either


def test_a_rate_limited_ask_waits_and_is_asked_again_as_before(monkeypatch):
    delays = []

    async def wait(delay):
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", wait)
    fake = FakeToss({"AAPL": 230.0, "NVDA": 181.5}, statuses=[429, 429])
    outbound.reset()
    got = asyncio.run(_broker(fake).quotes(["AAPL", "NVDA"]))
    assert got == {"AAPL": (230.0, "USD"), "NVDA": (181.5, "USD")}
    assert fake.asks == [["AAPL", "NVDA"]] * 3 and delays == [1.0, 1.0]
    assert outbound.snapshot()["toss"] == 3                           # a retry counts again


@pytest.mark.parametrize("statuses", [[429] * (RATE_LIMIT_RETRIES + 1), [403], [401, 401], [500]],
                         ids=["429-after-retries", "403", "401-after-renewal", "500"])
@pytest.mark.parametrize("redact", [False, True])
def test_a_many_symbol_call_still_refused_fails_whole_and_never_reads_one_by_one(no_wait, caplog, statuses, redact):
    fake = FakeToss({"AAPL": 230.0, "NVDA": 181.5}, statuses=statuses)
    broker = _broker(fake, redact)
    with caplog.at_level(logging.INFO), pytest.raises(TossError) as caught:
        asyncio.run(broker.quotes(["AAPL", "NVDA"]))
    assert caught.value.status == statuses[-1]
    assert all(a == ["AAPL", "NVDA"] for a in fake.asks)              # the source refuses: no call per symbol
    assert broker.batching()
    if redact:
        assert str(caught.value) == f"Toss GET request failed {statuses[-1]}"
        assert MARKER not in _desk_logs(caplog) and TOKEN not in _desk_logs(caplog)


@pytest.mark.parametrize("status", [403, 429])
def test_reads_alone_stop_on_a_refusal_every_next_read_would_meet(no_wait, status):
    """The batch off: 20 symbols read one by one meet a 403 (an IP not registered) or a 429 past _get's retries -
    the reads stop after the ones already under way, and the refusal is raised."""
    symbols = [f"S{i:02d}" for i in range(20)]
    fake = FakeToss({s: 1.0 for s in symbols}, alone_status=status)
    broker = _broker(fake, redact=True)
    broker._batch_off_until = time.monotonic() + BATCH_OFF_SEC
    with pytest.raises(TossError) as caught:
        asyncio.run(broker.quotes(symbols))
    assert caught.value.status == status and str(caught.value) == f"Toss GET request failed {status}"
    tries = RATE_LIMIT_RETRIES + 1 if status == 429 else 1
    assert len(set(map(tuple, fake.asks))) <= QUOTES_PARALLEL and len(fake.asks) <= QUOTES_PARALLEL * tries


def test_reads_alone_go_on_past_one_symbols_server_error():
    """A 500 is one symbol's: each is read once, none twice, and the ones that failed are left out."""
    symbols = [f"S{i:02d}" for i in range(20)]
    fake = FakeToss({s: 1.0 for s in symbols}, alone_status=500)
    broker = _broker(fake, redact=True)
    broker._batch_off_until = time.monotonic() + BATCH_OFF_SEC
    assert asyncio.run(broker.quotes(symbols)) == {}
    assert _singles(fake.asks) == symbols


def test_reads_alone_stop_when_there_is_no_token():
    fake = FakeToss({"AAPL": 230.0, "NVDA": 181.5, "TSLA": 350.0}, token_status=500)
    broker = _broker(fake, redact=True)
    broker._batch_off_until = time.monotonic() + BATCH_OFF_SEC
    with pytest.raises(TossError) as caught:
        asyncio.run(broker.quotes(["AAPL", "NVDA", "TSLA"]))
    assert caught.value.token and fake.asks == [] and fake.tokens <= QUOTES_PARALLEL
    assert MARKER not in str(caught.value)


@pytest.mark.parametrize("redact", [False, True])
def test_any_failure_getting_the_token_is_the_tokens_and_stops_the_reads_alone(redact):
    """#994 security review: the token store's Redis or database failing (no TossError of its own) is a
    TossError marked as the token's too - the reads alone stop, and nothing of the failure's text is shown
    (protected: not chained either)."""
    fake = FakeToss({f"S{i}": 1.0 for i in range(6)})
    broker = _broker(fake, redact=redact)
    broker._batch_off_until = time.monotonic() + BATCH_OFF_SEC
    calls = []

    async def access_token(revoked=None):
        calls.append(revoked)
        raise ConnectionError(MARKER)

    broker._access_token = access_token
    with pytest.raises(TossError) as caught:
        asyncio.run(broker.quotes([f"S{i}" for i in range(6)]))
    assert caught.value.token and str(caught.value) == "Toss token unavailable"
    assert fake.asks == [] and len(calls) <= QUOTES_PARALLEL
    if redact:
        assert caught.value.__cause__ is None and caught.value.__suppress_context__
    else:
        assert isinstance(caught.value.__cause__, ConnectionError)


def test_values_a_batch_read_stand_when_the_reads_alone_after_it_are_refused(no_wait):
    """30 priced and 2 left out; the left-out ones' reads meet a 403: the 30 are still answered."""
    known = {f"K{i:02d}": 1.0 for i in range(30)}
    fake = FakeToss(known, alone_status=403)
    got = asyncio.run(_broker(fake, redact=True).quotes([*known, "X1", "X2"]))
    assert set(got) == set(known)
    assert len(fake.asks) <= 1 + QUOTES_PARALLEL


def test_a_protected_brokers_malformed_answer_fails_without_its_text():
    fake = FakeToss({})
    fake.answer = [MARKER]                                             # not an object: no result to read
    with pytest.raises(TossError) as caught:
        asyncio.run(_broker(fake, redact=True).quotes(["AAPL", "NVDA"]))
    assert str(caught.value) == "Toss response is malformed" and MARKER not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("redact", [False, True])
def test_quote_takes_the_rows_quotes_takes(redact):
    """One check for both: a finite price above 0, a text currency or none, this symbol's row."""
    fake = FakeToss({})
    broker = _broker(fake, redact)
    for row, want in [({"symbol": "nvda", "lastPrice": "181.5", "currency": "USD"}, (181.5, "USD")),
                      ({"symbol": "NVDA", "lastPrice": "5"}, (5.0, None)),
                      ({"symbol": "NVDA", "lastPrice": "NaN", "currency": "USD"}, None),
                      ({"symbol": "NVDA", "lastPrice": "-3", "currency": "USD"}, None),
                      ({"symbol": "NVDA", "lastPrice": "0", "currency": "USD"}, None),
                      ({"symbol": "NVDA", "lastPrice": "10", "currency": 5}, None),
                      ({"symbol": "AAPL", "lastPrice": "10", "currency": "USD"}, None)]:
        fake.answer = {"result": [MARKER, row]}
        if want is None:
            with pytest.raises(TossError, match="no price"):
                asyncio.run(broker.quote("NVDA"))
        else:
            assert asyncio.run(broker.quote("NVDA")) == want
        assert asyncio.run(broker.quotes(["NVDA"])) == ({"NVDA": want} if want else {})


def test_the_default_quotes_reads_each_symbol_a_few_at_a_time_and_leaves_failures_out():
    class OneByOne(MockBroker):
        def __init__(self):
            self.asked, self.active, self.peak = [], 0, 0

        async def quote(self, symbol):
            self.asked.append(symbol)
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            if symbol == "BAD":
                raise ConnectionError("403 not an allowed IP")
            return 1.0, "USD"

    broker = OneByOne()
    got = asyncio.run(broker.quotes(["A", "BAD", "B", "C", "A"]))
    assert got == {"A": (1.0, "USD"), "B": (1.0, "USD"), "C": (1.0, "USD")}
    assert sorted(broker.asked) == ["A", "B", "BAD", "C"] and broker.peak == QUOTES_PARALLEL
    assert asyncio.run(MockBroker().quotes(["A"])) == {}              # no market data: nothing, no error


def test_the_broker_says_whether_its_many_symbol_read_is_on():
    broker = _broker(FakeToss({}))
    assert broker.batching() and PriceSource(broker).batch_ready()
    broker._batch_off_until = time.monotonic() + BATCH_OFF_SEC
    assert not broker.batching() and not PriceSource(broker).batch_ready()
    assert not MockBroker().batching()                                 # one by one: no batch to pass


def test_the_collectors_demand_job_passes_the_quote_batch_only_while_the_broker_batches(db_url, monkeypatch):
    """desk.api's serve_wants: the batch while Toss takes it; off, each quote's own read (PASS_SEC a pass)."""
    from tests.test_prices import _client

    class Batching(MockBroker):
        name = "toss"
        on = True

        def batching(self):
            return self.on

    passed = []

    async def serve(handlers, *, batch=None):
        passed.append((sorted(handlers), batch))
        return 0

    monkeypatch.setattr(demand, "serve", serve)
    broker = Batching()
    source = PriceSource(broker, yahoo_quote=_no_yahoo)
    with _client(db_url, source) as client:
        step = client.app.state.serve_wants
        asyncio.run(step())
        broker.on = False
        asyncio.run(step())
    # #999 the estimate namespace is served as before, by its own handler
    assert passed == [(["estimate", "futures_quote", "quote"], {"quote": source.refresh_quotes}),
                      (["estimate", "futures_quote", "quote"], None)]


# --- the collector's pass, before and after ------------------------------------------------------------------


def _no_yahoo(ticker):
    raise AssertionError(f"Yahoo asked for {ticker}")


def demand_pass(n: int, batched: bool, **options) -> tuple[int, int, int]:
    """One demand pass of the collector (demand_passes)."""
    return demand_passes(n, batched, **options)[0]


def demand_passes(n: int, batched: bool, *, unknown=(), odd=(), off=False, passes=1,
                  **fake_options) -> list[tuple[int, int, int]]:
    """Demand passes of the collector over ``n`` wanted, priced quotes (and ``unknown`` ones Toss lists no price
    for, ``odd`` ones of no Toss symbol's form) against a fake Toss (``fake_options``: FakeToss'), every key due
    in each: (keys served, GETs of /api/v1/prices, outbound.count("toss")) a pass. ``batched``: as desk.api's
    serve_wants - the ``quote`` batch while the broker batches (#994) - else each ticker's read. ``off``: the
    many-symbol read turned off a moment ago."""
    server = fakeredis.FakeServer()
    sc.use(fakeredis.FakeRedis(server=server, decode_responses=True),
           fakeredis.FakeAsyncRedis(server=server, decode_responses=True))
    token = demand.SCREEN.set(True)
    try:
        tickers = [f"T{i:03d}" for i in range(n)]
        fake = FakeToss({t: 10.0 + i for i, t in enumerate(tickers)}, **fake_options)
        broker = _broker(fake, redact=True)
        if off:
            broker._batch_off_until = time.monotonic() + BATCH_OFF_SEC
        source = PriceSource(broker, yahoo_quote=_no_yahoo)
        demand.configure("api")
        for ticker in [*tickers, *unknown, *odd]:
            demand.want("quote", ticker)
        asyncio.run(demand.flush())
        demand.configure("collector")
        out = []
        for _ in range(passes):
            demand._due.clear()                                        # every key due (a period later)
            outbound.reset()
            asked = len(fake.asks)
            batch = {"quote": source.refresh_quotes} if batched and source.batch_ready() else None
            served = asyncio.run(demand.serve({"quote": source.refresh_quote}, batch=batch))
            out.append((served, len(fake.asks) - asked, outbound.snapshot().get("toss", 0)))
        return out
    finally:
        demand.SCREEN.reset(token)
        sc.use(None, None)
        demand.configure("")


@pytest.mark.parametrize("n", [24, 60])
def test_a_demand_pass_calls_toss_once_for_every_wanted_quote(n):
    assert demand_pass(n, batched=False) == (n, n, n)                  # before: one GET per ticker
    assert demand_pass(n, batched=True) == (n, 1, 1)                   # after: ceil(n / 200)


def test_a_demand_pass_reads_an_unlisted_ticker_alone_once_and_never_sends_an_odd_one():
    assert demand_pass(20, batched=True, unknown=["ZZZZ"], odd=["X^Y"]) == (20, 2, 2)
    assert demand_pass(20, batched=False, unknown=["ZZZZ"], odd=["X^Y"]) == (20, 21, 21)


@pytest.mark.parametrize("refused,alone", [(404, 404), (400, 400), (400, 404)],
                         ids=["404-404", "400-400", "400-404"])
def test_a_demand_pass_whose_batch_one_unknown_ticker_gets_refused_keeps_the_batch(refused, alone):
    """#994 security review: 20 listed tickers and one unknown, the many-symbol ask refused for it - the first
    pass reads each alone once (1 + 21 GETs) and keeps only that one out of the batch: the next pass, 2 GETs."""
    first, second = demand_passes(20, batched=True, unknown=["ZZZZ"], passes=2,
                                  refuse_unknown=refused, unknown_alone=alone)
    assert first == (20, 22, 22) and second == (20, 2, 2)
