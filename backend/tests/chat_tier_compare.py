"""Offline quick/deep comparison. Run from backend: python -m tests.chat_tier_compare.

This exercises real local chat requests with a fake provider. The answers and
model names are fixtures for inspecting the report format. The elapsed time
only measures the local message request, including collection of its SSE body.
Use a separately authorized environment for a real model quality comparison.
"""

import json
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

QUESTIONS = {
    "macro": "합성 자료: 물가 2.4%, 정책금리 3.0%, 실업률 4.1%. 거시 상황과 판단의 한계를 요약해 주세요.",
    "long_short": "합성 종목 A의 가격은 104, 지지는 100, 저항은 110입니다. 롱·숏 조건을 비교해 주세요.",
    "entry": "합성 종목 B의 현재가는 52, 지지는 50, 손절 기준 후보는 48입니다. 진입 조건과 무효화 조건을 제시해 주세요.",
}
ANSWERS = {
    "macro": "물가·금리·고용을 함께 봅니다. 한 시점의 수치만 있어 추세는 알 수 없습니다.",
    "long_short": "지지 100 유지가 롱 검토 조건입니다. 지지 이탈 시 숏을 검토하며 저항은 110입니다.",
    "entry": "50 부근 지지 확인 후 진입을 검토합니다. 48 이탈 시 가정을 무효화합니다.",
}
MODELS = {"quick": "fixture-quick", "deep": "fixture-deep"}
BASE_CONFIG = {f"{tier}_think_llm": model for tier, model in MODELS.items()}
USER = "chat-tier-fixture"
PASSWORD = "local-chat-tier-fixture-only"


class _FakeLlm:
    def __init__(self, tier, failure):
        self.tier = tier
        self.failure = failure

    def bind_tools(self, *_args, **_kwargs):
        return self

    async def astream(self, messages):
        from langchain_core.messages import AIMessageChunk

        if self.failure is not None:
            raise self.failure
        # There should be a desk prompt and exactly one user turn.
        assert [message.type for message in messages] == ["system", "human"]
        question = next(key for key, text in QUESTIONS.items() if text == messages[-1].content)
        yield AIMessageChunk(content=f"합성 {self.tier} 응답. ")
        yield AIMessageChunk(content=ANSWERS[question])


@contextmanager
def _client(path: Path, tier: str, failure: Exception | None = None):
    # Settings loads .env at import time. Keep product imports and lazy imports scoped
    # to the offline fixture, without reading or changing any environment files.
    with patch("dotenv.load_dotenv", return_value=False):
        from tradingagents import llm_clients

        from desk import api, chat
        from desk.auth import Auth
        from desk.db import make_engine
        from desk.settings import Settings

        class FixtureChatter(chat.LangChainChatter):
            def config(self, model: str | None = None) -> dict:
                return chat.chat_config(BASE_CONFIG, self.tier, model, self.effort)

        settings = Settings(
            database_url=f"sqlite:///{path.as_posix()}",
            role="api", fake_runs=True, fake_delay=0, broker="mock",
            toss_client_id="", toss_client_secret="", toss_account_seq="",
            kis_app_key="", kis_app_secret="", kis_ws_url="", kis_rest_url="",
            krx_api_key="", semibot_db_path="", kosdaq_symbols=[],
            redis_url="", redis_sentinels=(), redis_master="master-redis", redis_password="", redis_cache_key="",
            market_stream=False, macro_interval_sec=0, report_every_days=0, report_model="",
            report_reasoning="", report_topics="", morning_brief_at="off",
            score_interval_sec=0, futures_snapshot_min=0, retention_days=0, portfolio_stream_sec=5,
            session_max_hours=1, session_idle_hours=1, cookie_secure=True, enable_docs=False,
            otp_key="", public_url="", cors_origins=[], log_level="WARNING",
            vapid_public_key="", vapid_private_key="", vapid_subject="",
            telegram_bot_token="", telegram_chat_id="",
            chat_model_tier=tier, chat_reasoning_effort="", chat_models=list(MODELS.values()), chat_model="",
            rate_limit_cooldown_min=1, max_concurrent_runs=1, reuse_reports_min=0,
        )

        def fake_client(config, selected_tier, **_kwargs):
            assert selected_tier == settings.chat_model_tier
            assert config[f"{selected_tier}_think_llm"] == MODELS[selected_tier]
            return SimpleNamespace(get_llm=lambda: _FakeLlm(selected_tier, failure))

        warning = api.log.warning

        def fixture_warning(message, *args, **kwargs):
            if message == "chat answer in %s failed: %s":
                warning("synthetic chat answer failed")
            else:
                warning(message, *args, **kwargs)

        with (
            patch.object(llm_clients, "create_tier_client", fake_client),
            patch.object(api.log, "warning", fixture_warning),
        ):
            app = api.create_app(settings, chatter=FixtureChatter(tier=settings.chat_model_tier))
            engine = make_engine(settings.database_url)
            try:
                Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(USER, PASSWORD)
            finally:
                engine.dispose()
            with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as client:
                login = client.post("/api/auth/login", json={"username": USER, "password": PASSWORD})
                if login.status_code != 204:
                    raise RuntimeError("fixture login failed")
                yield client


def _read_reply(status_code: int, body: str) -> tuple[str, str | None, str | None]:
    if status_code == 429:
        return "http_429", None, None
    if status_code != 200:
        return "http_error", None, None
    tokens = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        event = json.loads(line[6:])
        if event["type"] == "token":
            tokens.append(event["text"])
        elif event["type"] == "error":
            status = "sse_429" if event["message"].startswith("모델 속도 제한") else "sse_error"
            return status, None, None
        elif event["type"] == "done":
            return "ok", event.get("model"), "".join(tokens)
    return "missing_terminal", None, None


def _sample(client: TestClient, question: str) -> dict:
    new = client.post("/api/chat/threads", json={})
    if new.status_code != 201:
        raise RuntimeError("fixture thread setup failed")
    thread = new.json()["id"]
    start = perf_counter()
    reply = client.post(f"/api/chat/threads/{thread}/messages", json={"text": QUESTIONS[question]})
    elapsed_ms = round((perf_counter() - start) * 1000, 3)
    status, model, answer = _read_reply(reply.status_code, reply.text)
    retry = reply.headers.get("Retry-After", "")
    return {
        "question": question,
        "elapsed_ms": elapsed_ms,
        "http_status": reply.status_code,
        "status": status,
        "retry_after_s": int(retry) if status == "http_429" and retry.isdecimal() else None,
        "model": model,
        "answer": answer,
    }


def compare() -> dict:
    tiers = {}
    with TemporaryDirectory(prefix="desk-chat-tier-") as folder:
        for tier in MODELS:
            with _client(Path(folder) / f"{tier}.db", tier) as client:
                tiers[tier] = [_sample(client, question) for question in QUESTIONS]
    return {
        "provider": "synthetic fixture",
        "quality_evaluation": "fixture answers only; real model quality unmeasured",
        "questions": QUESTIONS,
        "tiers": tiers,
    }


if __name__ == "__main__":
    print(json.dumps(compare(), indent=2))
