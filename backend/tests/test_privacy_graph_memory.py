"""Inspect the graph's persistence contract with an in-process stub."""

from pathlib import Path

import pytest
from tradingagents import default_config
from tradingagents.dataflows import config as dataflows
from tradingagents.graph import trading_graph

from desk.runner import RunRequest, run_tradingagents


@pytest.mark.parametrize("fails", [False, True])
def test_protected_graph_has_fresh_memory_and_no_checkpoint_or_report(tmp_path, monkeypatch, fails):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    memory = legacy / "trading_memory.md"
    memory.write_text("legacy synthetic lesson", encoding="utf-8")
    cfg = {"memory_log_path": str(memory), "results_dir": str(legacy),
           "data_cache_dir": str(legacy), "checkpoint_enabled": True}
    previous = dataflows.get_config()
    initial_states = []
    temporary = []

    class Graph:
        def __init__(self, *, selected_analysts, config):
            self.config = config
            self.propagator = self
            assert selected_analysts
            dataflows.set_config(config)  # the pinned constructor sets a process-wide fallback
            scratch_memory = Path(config["memory_log_path"])
            assert scratch_memory.parent != legacy
            assert not scratch_memory.exists()
            assert Path(config["data_cache_dir"]).parent == scratch_memory.parent
            assert Path(config["results_dir"]).parent == scratch_memory.parent
            assert config["checkpoint_enabled"] is False
            temporary.append(scratch_memory.parent)
            scratch_memory.write_text("temporary synthetic content", encoding="utf-8")

        def create_run_state(self, ticker, date, asset_type, portfolio):
            return {"ticker": ticker}

        def get_graph_args(self):
            return {}

        def stream_run(self, state):
            initial_states.append(state)
            assert dataflows.get_config()["memory_log_path"] == self.config["memory_log_path"]
            if fails:
                raise RuntimeError("synthetic graph failure")
            yield [], {"final_trade_decision": "synthetic"}

    monkeypatch.setattr(default_config, "build_default_config", lambda: dict(cfg))
    monkeypatch.setattr(trading_graph, "TradingAgentsGraph", Graph)

    request = RunRequest("SYNTHETIC", "2026-11-20")
    if fails:
        with pytest.raises(RuntimeError, match="synthetic graph failure"):
            run_tradingagents(request, lambda _event: None, None, isolate_artifacts=True)
    else:
        result = run_tradingagents(request, lambda _event: None, None, isolate_artifacts=True)
        assert result.final_state["final_trade_decision"] == "synthetic"
        assert result.report_path is None

    assert initial_states == [{"ticker": "SYNTHETIC"}]
    assert temporary and not temporary[0].exists()
    assert memory.read_text(encoding="utf-8") == "legacy synthetic lesson"
    assert dataflows.get_config() == previous


@pytest.mark.parametrize("public", [False, True], ids=["account", "public"])
def test_a_public_run_reads_and_writes_a_memory_apart_from_the_account_runs(tmp_path, monkeypatch, public):
    """#775 the default memory file holds the owner's account runs' decisions: a run anyone may read keeps its own."""
    memory = tmp_path / "trading_memory.md"
    memory.write_text("account synthetic lesson", encoding="utf-8")
    cfg = {"memory_log_path": str(memory), "results_dir": str(tmp_path), "data_cache_dir": str(tmp_path),
           "checkpoint_enabled": False}

    class Graph:
        def __init__(self, *, selected_analysts, config):
            self.config = config
            self.propagator = self

        def create_run_state(self, ticker, date, asset_type, portfolio):
            return {"ticker": ticker}

        def get_graph_args(self):
            return {}

        def begin_checkpoint(self, *args):
            return None

        def checkpoint_input(self, state):
            return state

        def end_checkpoint(self):
            pass

        def stream_run(self, state):
            # the library reads its past decisions from the configured file
            yield [], {"final_trade_decision": Path(self.config["memory_log_path"]).read_text(encoding="utf-8")
                       if Path(self.config["memory_log_path"]).exists() else "no memory"}

        def record_decision(self, ticker, date, final_state):
            with open(self.config["memory_log_path"], "a", encoding="utf-8") as f:
                f.write(f"\n{ticker} decision")

        def clear_checkpoint_on_success(self, *args):
            pass

        def save_reports(self, final_state, ticker):
            return tmp_path / "report.md"

    monkeypatch.setattr(default_config, "build_default_config", lambda: dict(cfg))
    monkeypatch.setattr(trading_graph, "TradingAgentsGraph", Graph)

    result = run_tradingagents(RunRequest("SYNTHETIC", "2026-11-20"), lambda _event: None, None,
                               public_memory=public)
    public_file = tmp_path / "trading_memory.public.md"
    if public:
        assert result.final_state["final_trade_decision"] == "no memory"
        assert memory.read_text(encoding="utf-8") == "account synthetic lesson"
        assert public_file.read_text(encoding="utf-8") == "\nSYNTHETIC decision"
    else:
        assert result.final_state["final_trade_decision"] == "account synthetic lesson"
        assert memory.read_text(encoding="utf-8").endswith("SYNTHETIC decision")
        assert not public_file.exists()
