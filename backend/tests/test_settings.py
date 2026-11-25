"""#628 settings: the env parsing helpers, defaults, and no secret in the dataclass's repr."""

import dataclasses
import re

import pytest

from desk import settings as st
from desk.settings import Settings

SECRET_FIELD = re.compile(r"key|secret|token|password|chat_id|database_url|account")


@pytest.mark.parametrize(("raw", "default", "want"), [
    (None, False, False), ("", True, True), ("1", False, True), (" TRUE ", False, True), ("on", False, True),
    ("yes", False, True), ("0", True, False), ("off", True, False), ("nope", True, False),
])
def test_bool(monkeypatch, raw, default, want):
    if raw is None:
        monkeypatch.delenv("DESK_X_FLAG", raising=False)
    else:
        monkeypatch.setenv("DESK_X_FLAG", raw)
    assert st._bool("DESK_X_FLAG", default) is want


def test_list_tier_and_effort(monkeypatch):
    monkeypatch.setenv("DESK_X_LIST", " a, ,b ,c,")
    assert st._list("DESK_X_LIST") == ["a", "b", "c"]
    monkeypatch.delenv("DESK_X_LIST")
    assert st._list("DESK_X_LIST") == []
    assert [st._tier(x) for x in ("QUICK", " deep ", "fast", None)] == ["quick", "deep", "deep", "deep"]
    assert [st._effort(x) for x in ("High", "xhigh", "max", None)] == ["high", "xhigh", "", ""]


def test_values_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("DESK_CHAT_MODELS", "")
    monkeypatch.setenv("DESK_KOSDAQ_SYMBOLS", "035720, 247540")
    monkeypatch.setenv("DESK_PUBLIC_URL", "https://desk.example/")
    monkeypatch.setenv("DESK_BROKER", "TOSS")
    monkeypatch.setenv("DESK_CORS_ORIGINS", "")
    s = Settings()
    assert s.chat_models == ["gpt-6-luna", "gpt-6-sol"]          # empty: the default pair
    assert s.kosdaq_symbols == ["035720", "247540"] and s.public_url == "https://desk.example"
    assert s.broker == "toss" and s.cors_origins == []


def test_no_secret_reaches_the_repr(monkeypatch):
    secret_fields = [f for f in dataclasses.fields(Settings)
                     if SECRET_FIELD.search(f.name) and "public" not in f.name]   # a VAPID public key is public
    assert {f.name for f in secret_fields} >= {"database_url", "otp_key", "toss_client_secret", "kis_app_key",
                                               "telegram_bot_token"}
    assert [f.name for f in secret_fields if f.repr] == []
    monkeypatch.setenv("DESK_DATABASE_URL", "postgresql+psycopg://desk:marker-db-pw@db:5432/desk")
    monkeypatch.setenv("DESK_OTP_KEY", "marker-otp")
    monkeypatch.setenv("DESK_TELEGRAM_BOT_TOKEN", "marker-token")
    text = repr(Settings())
    assert "marker" not in text
