import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace

import httpx
import pytest

from desk import analysis_sources as sources

NOW = datetime(2026, 11, 24, 16, tzinfo=UTC)
TODAY = NOW.date()


@pytest.mark.parametrize("status", [403, 404, 451])
def test_path_failure_stops_calls_and_logs_once_until_a_probe(status, caplog):
    clock, calls = [0.0], []

    def respond(request):
        calls.append(request)
        return httpx.Response(status if len(calls) == 1 else 200, json={"events": []})

    source = sources.PredictionEvidence(transport=httpx.MockTransport(respond), clock=lambda: clock[0],
                                        utc_now=lambda: NOW)

    async def check():
        assert await source.read("SOXL", TODAY) == sources.Evidence("unavailable", f"http_{status}")
        clock[0] = 899
        assert await source.read("SPY", TODAY) == sources.Evidence("unavailable", f"http_{status}")
        assert len(calls) == 1
        clock[0] = 900
        assert await source.read("SOXL", TODAY) == sources.Evidence("empty")

    asyncio.run(check())
    assert len(calls) == 2 and all(request.method == "GET" for request in calls)
    assert all(str(request.url.copy_with(query=None)) == sources.POLYMARKET_SEARCH for request in calls)
    assert caplog.messages == [f"analysis source=polymarket reason=http_{status} circuit=open"]


def test_reset_retries_once_and_zero_quote_remains_a_real_value(caplog):
    waits, calls = [], []

    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadError("synthetic reset", request=request)
        return httpx.Response(200, json={"events": [{"markets": [{
            "active": True, "closed": False, "question": "Test event",
            "endDate": "2026-11-25T00:00:00Z", "outcomes": '["No", "Yes"]', "outcomePrices": '["1", "0"]',
        }]}]})

    async def sleep(delay):
        waits.append(delay)

    source = sources.PredictionEvidence(transport=httpx.MockTransport(respond), utc_now=lambda: NOW, sleep=sleep)
    assert asyncio.run(source.read("SOXL", TODAY)) == sources.Evidence("available", quotes=(("Test event", 0.0),))
    assert len(calls) == 2 and waits == [0.2]
    assert caplog.messages == []


def test_timeout_attempt_budget_and_transient_circuit(monkeypatch, caplog):
    monkeypatch.setattr(sources, "_ATTEMPT_TIMEOUT", 0.02)
    clock, calls, waits = [0.0], [], []

    async def respond(request):
        calls.append(request)
        await asyncio.Event().wait()

    async def sleep(delay):
        waits.append(delay)

    source = sources.PredictionEvidence(
        transport=httpx.MockTransport(respond), clock=lambda: clock[0], utc_now=lambda: NOW, sleep=sleep,
    )

    async def check():
        for _ in range(4):
            assert await source.read("SOXL", TODAY) == sources.Evidence("unavailable", "timeout")
        assert len(calls) == 6
        clock[0] = 60
        assert await source.read("SOXL", TODAY) == sources.Evidence("unavailable", "timeout")

    asyncio.run(check())
    assert len(calls) == 8 and waits == [0.2] * 4
    assert caplog.messages == [
        "analysis source=polymarket reason=timeout circuit=closed",
        "analysis source=polymarket reason=timeout circuit=open",
    ]


def test_rate_limit_redirect_and_historical_guard_do_not_fetch_other_paths():
    clock, calls = [0.0], []

    def respond(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "75"}) if len(calls) == 1 else httpx.Response(
            302, headers={"Location": "https://invalid.example/"},
        )

    source = sources.PredictionEvidence(transport=httpx.MockTransport(respond), clock=lambda: clock[0],
                                        utc_now=lambda: NOW)

    async def check():
        assert (await source.read("SOXL", date(2026, 11, 23))).reason == "not_current_date"
        assert (await source.read("SOXL", TODAY)).reason == "http_429"
        clock[0] = 60
        assert (await source.read("QQQ", TODAY)).reason == "http_429"
        clock[0] = 75
        assert (await source.read("QQQ", TODAY)).reason == "http_302"

    asyncio.run(check())
    assert len(calls) == 2 and all(request.url.host == "gamma-api.polymarket.com" for request in calls)


def test_only_one_controlled_request_is_in_flight_even_after_cooldown():
    clock, calls = [0.0], []

    async def check():
        entered, release = asyncio.Event(), asyncio.Event()

        async def respond(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(403)
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"events": None})

        source = sources.PredictionEvidence(transport=httpx.MockTransport(respond), clock=lambda: clock[0],
                                            utc_now=lambda: NOW)
        assert (await source.read("SOXL", TODAY)).reason == "http_403"
        clock[0] = 901
        probe = asyncio.create_task(source.read("SOXL", TODAY))
        await entered.wait()
        assert (await source.read("QQQ", TODAY)).reason == "request_in_progress"
        release.set()
        assert (await probe).status == "empty"

    asyncio.run(check())
    assert len(calls) == 2


def test_reports_keep_non_optional_news_and_explicit_exclusions(monkeypatch, caplog):
    from tradingagents.agents import tools

    calls = []

    def news(**kwargs):
        calls.append(kwargs)
        return "확인된 기사 데이터"

    async def disabled_prediction(*_args):
        pytest.fail("unapproved prediction vendor must never call the origin")

    monkeypatch.setattr(tools, "get_news", SimpleNamespace(invoke=lambda args: news(**args)))
    monkeypatch.setattr(tools, "get_global_news", SimpleNamespace(invoke=lambda args: news(**args)))
    monkeypatch.setattr(sources._prediction, "read", disabled_prediction)
    monkeypatch.setattr(sources, "_logged_exclusions", set())
    caplog.set_level("INFO", logger=sources.__name__)
    config = {"data_vendors": {"prediction_markets": "polymarket"},
              "tool_vendors": {"get_prediction_markets": "unknown"}}
    assert sources.prepare_reports([], "SOXL", TODAY, config, korean=True) == {}
    for _ in range(2):
        reports = sources.prepare_reports(["social", "news"], "SOXL", TODAY, config, korean=True)
        assert "StockTwits" in reports["social"] and "Reddit" in reports["social"]
        assert "이 원천의 근거 없음(조회 불가)" in reports["social"]
        assert "확인된 기사 데이터" in reports["news"]
        assert "status=disabled; reason=vendor_disabled" in reports["news"]
    assert len(calls) == 4
    assert all(call["trade_date"] == "2026-11-24" for call in calls)
    assert caplog.messages == [
        "analysis source=stocktwits reason=approval_required circuit=disabled",
        "analysis source=reddit reason=oauth_required circuit=disabled",
        "analysis source=polymarket reason=vendor_disabled circuit=disabled",
    ]


@pytest.mark.parametrize(("analysts", "native"), [
    (["fundamentals", "news", "social"], ["fundamentals"]),
    (["news", "social"], ["market"]),
])
def test_live_runner_uses_only_supported_graph_inputs_and_emits_each_evidence_once(monkeypatch, analysts, native):
    from tradingagents.agents import tools
    from tradingagents.graph import trading_graph

    from desk.runner import RunRequest, _stream_graph

    news_calls, origin_calls, events = [], [], []

    def ordinary_news(method, *args):
        news_calls.append((method, args))
        return "Known instrument or global headline"

    def respond(request):
        origin_calls.append(request)
        return httpx.Response(403)

    class Graph:
        def __init__(self, *, selected_analysts, config):
            assert selected_analysts == native
            self.config = config
            self.propagator = SimpleNamespace(get_graph_args=dict)

        def create_run_state(self, *_):
            return {}

        def stream_run(self, state):
            assert "approval_required" in state["sentiment_report"]
            assert "oauth_required" in state["sentiment_report"]
            assert "Known instrument or global headline" in state["news_report"]
            assert "reason=http_403" in state["news_report"]
            yield [], state | {"final_trade_decision": "synthetic"}

    monkeypatch.setattr(trading_graph, "TradingAgentsGraph", Graph)
    monkeypatch.setattr(tools, "route_to_vendor", ordinary_news)
    monkeypatch.setattr(sources, "_prediction", sources.PredictionEvidence(
        transport=httpx.MockTransport(respond), utc_now=lambda: NOW,
    ))
    monkeypatch.setattr(sources, "_logged_exclusions", set())
    request = RunRequest("SOXL", TODAY.isoformat(), analysts=analysts)
    result = _stream_graph(request, events.append, None, {"output_language": "Korean"}, isolated=True)
    assert result.final_state["final_trade_decision"] == "synthetic"
    assert len(origin_calls) == 1
    assert news_calls == [
        ("get_news", ("SOXL", "2026-11-17", "2026-11-24")),
        ("get_global_news", ("2026-11-24", 7, 10)),
    ]
    prepared = [e for e in events if e["agent"] in {"sentiment_analyst", "news_analyst"}]
    assert [e["agent"] for e in prepared] == ["sentiment_analyst", "news_analyst"]
    assert all(e["data"] == {"source_evidence": True} for e in prepared)


def test_unexpected_outcome_object_does_not_fail_the_analysis():
    market = {"outcomes": '{"Yes": 0, "No": 1}', "outcomePrices": '["0", "1"]'}
    assert sources._yes_price(market) is None


def test_checkpoint_resume_does_not_fetch_or_emit_replacement_evidence(monkeypatch):
    from tradingagents.graph import trading_graph

    from desk.runner import RunRequest, _stream_graph

    events, closed = [], []

    class Graph:
        def __init__(self, *, selected_analysts, config):
            assert selected_analysts == ["market"]
            assert config["desk_analysis_source_reports"] == ["news"]
            self.config = config
            self.propagator = SimpleNamespace(get_graph_args=dict)

        def create_run_state(self, *_):
            return {}

        def begin_checkpoint(self, *_):
            return "prior-managed-news-run"

        def checkpoint_input(self, _):
            return None

        def stream_run(self, state, *, config):
            assert state is None
            assert config["configurable"]["thread_id"] == "prior-managed-news-run"
            yield [], {"news_report": "Saved evidence", "final_trade_decision": "synthetic"}

        def end_checkpoint(self):
            closed.append(True)

        record_decision = clear_checkpoint_on_success = save_reports = lambda *_: "synthetic"

    def forbidden(*_, **__):
        pytest.fail("resuming must not read fresh sources")

    monkeypatch.setattr(trading_graph, "TradingAgentsGraph", Graph)
    monkeypatch.setattr(sources, "prepare_reports", forbidden)
    request = RunRequest("SOXL", TODAY.isoformat(), analysts=["news"])
    result = _stream_graph(request, events.append, None, {}, isolated=False)
    assert result.final_state["news_report"] == "Saved evidence"
    assert closed == [True]
    assert [e for e in events if e["agent"] == "news_analyst"] == [
        {"type": "message", "agent": "news_analyst", "content": "Saved evidence", "data": {"source_evidence": True}},
    ]
