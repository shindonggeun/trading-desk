"""#171 step 5: the desk's own stages overlap - slow notes wait only up to the horizon's budget,
and the closing summary thinks beside the product's playbooks and trade map."""

import time
from datetime import timedelta as td

from fastapi.testclient import TestClient

from desk import api, runner
from desk.auth import Auth
from desk.consensus import fake_summariser
from desk.db import make_engine
from desk.runner import make_fake_runner
from desk.settings import Settings
from tests.test_api import OWNER, PASSWORD, _wait_done

BRIEF = {"ticker": "NVDA", "instruments": [{"ticker": "SMH", "role": "sector", "price": 301.5, "as_of": "2026-11-04",
                                            "chg_1d": 1.0, "chg_5d": 2.0, "chg_20d": 3.0, "vs_sma20": 1.0,
                                            "vs_sma50": 2.0, "corr_60d": 0.9}]}


def _run(db_url, monkeypatch, related, summariser=None):
    seen = []
    fake = make_fake_runner(0)

    def graph(request, emit, portfolio):
        seen.append(portfolio.render(request.ticker) if portfolio else "")
        return fake(request, emit, portfolio)

    monkeypatch.setattr(api, "collect_related", related)
    if summariser is not None:
        monkeypatch.setattr(api, "llm_summariser", summariser)
    client = TestClient(api.create_app(Settings(database_url=db_url, fake_delay=0), runner=graph),
                        base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, td(hours=1), td(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    with client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        run = client.post("/api/runs", json={"ticker": "NVDA", "trade_date": "2026-11-04"}).json()
        done = _wait_done(client, run["id"])
    return done, seen


def test_a_related_set_past_the_budget_skips_the_prompt_but_reaches_the_plan(db_url, monkeypatch):
    monkeypatch.setattr(runner, "NOTES_WAIT_SEC", {})
    monkeypatch.setattr(runner, "NOTES_WAIT_DEFAULT", 0.05)

    def slow(ticker, day):
        time.sleep(0.5)
        return BRIEF

    done, seen = _run(db_url, monkeypatch, slow)
    assert done["status"] == "done"
    assert "SMH" not in seen[0]                       # the agents started without it
    assert done["plan"]["related"] == BRIEF            # filled in after the graph
    assert done["plan"]["timing"]["late_notes"] == ["related"]


def test_a_related_set_within_the_budget_reaches_the_prompt(db_url, monkeypatch):
    done, seen = _run(db_url, monkeypatch, lambda ticker, day: BRIEF)
    assert "SMH" in seen[0] and done["plan"]["related"] == BRIEF
    assert "late_notes" not in done["plan"]["timing"]


def test_the_closing_summary_reads_its_own_copy_of_the_plan(db_url, monkeypatch):
    got = []

    def summariser(ctx):
        got.append(set(ctx.plan))
        return fake_summariser(ctx)

    done, _ = _run(db_url, monkeypatch, lambda ticker, day: BRIEF, summariser)
    # the levels and zones it needs, none of what is built beside it
    assert {"rating", "entry_price", "support_resistance"} & got[0] and not {"trade_map", "related"} & got[0]
    assert done["plan"]["consensus"] and "consensus_error" not in done["plan"]
    assert done["plan"]["related"] == BRIEF
