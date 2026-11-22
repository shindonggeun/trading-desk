import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from tests.chat_tier_compare import ANSWERS, MODELS, QUESTIONS, _client, _read_reply, _sample


def test_standalone_compares_both_tiers_without_loading_dotenv():
    # A fresh interpreter catches import-time reads that can be masked by modules
    # already imported by pytest fixtures.
    probe = """
from runpy import run_module
from unittest.mock import patch

poisoned_env = dict.fromkeys(
    ("DESK_MAX_CONCURRENT_RUNS", "DESK_REUSE_REPORTS_MIN", "DESK_SESSION_MAX_HOURS",
     "DESK_SESSION_IDLE_HOURS", "DESK_PORTFOLIO_STREAM_SEC"), "invalid")
with (
    patch.dict("os.environ", poisoned_env),
    patch("dotenv.load_dotenv", side_effect=AssertionError("unexpected .env load")),
):
    run_module("tests.chat_tier_compare", run_name="__main__")
"""
    process = subprocess.run([sys.executable, "-B", "-c", probe], cwd=Path(__file__).resolve().parents[1],
                             capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["provider"] == "synthetic fixture"
    assert result["questions"] == QUESTIONS
    for tier, model in MODELS.items():
        rows = result["tiers"][tier]
        assert [row["question"] for row in rows] == list(QUESTIONS)
        for row in rows:
            assert row["status"] == "ok" and row["http_status"] == 200
            assert row["model"] == model
            assert row["elapsed_ms"] >= 0
            assert row["answer"] == f"합성 {tier} 응답. " + ANSWERS[row["question"]]


def test_provider_rate_limit_and_cooldown_are_distinguished(tmp_path):
    class RateLimitError(Exception):
        status_code = 429

    with _client(tmp_path / "rate-limit.db", "quick", RateLimitError("synthetic")) as client:
        provider = _sample(client, "macro")
        cooldown = _sample(client, "entry")
    assert (provider["http_status"], provider["status"]) == (200, "sse_429")
    assert (cooldown["http_status"], cooldown["status"]) == (429, "http_429")
    assert provider["retry_after_s"] is None and cooldown["retry_after_s"] > 0
    assert all(row["answer"] is None and row["model"] is None for row in (provider, cooldown))


def test_report_and_logs_exclude_raw_errors_and_partial_answers(tmp_path, caplog, capsys):
    with _client(tmp_path / "failure.db", "deep", RuntimeError("fixture-raw-error")) as client:
        row = _sample(client, "long_short")
        with patch("desk.chat.build_messages", side_effect=RuntimeError("fixture-raw-http-error")):
            http_error = _sample(client, "entry")
    assert (row["http_status"], row["status"], row["answer"]) == (200, "sse_error", None)
    assert (http_error["http_status"], http_error["status"], http_error["answer"]) == (500, "http_error", None)
    assert "fixture-raw-error" not in json.dumps(row)
    assert "fixture-raw-http-error" not in json.dumps(http_error)
    captured = caplog.text + capsys.readouterr().err
    assert "synthetic chat answer failed" in captured
    assert "fixture-raw-error" not in captured
    assert "fixture-raw-http-error" not in captured
    assert _read_reply(200, 'data: {"type":"token","text":"partial"}\n\n') == ("missing_terminal", None, None)
    assert _read_reply(502, "fixture-raw-http-error") == ("http_error", None, None)
