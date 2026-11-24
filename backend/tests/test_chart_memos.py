from contextlib import closing
from datetime import datetime, timedelta
from urllib.parse import quote

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from desk.api import create_app
from desk.auth import Auth
from desk.db import alembic_config, make_engine
from desk.settings import Settings
from tests.test_favorites import OTHER, OWNER, PASSWORD, _login

URL = "/api/chart-memo"
PRIVATE = "private, no-store"
ACCOUNT_HEADER = "X-Desk-Memo-Account"


@pytest.fixture
def memo_app(db_url):
    app = create_app(Settings(database_url=db_url, fake_runs=True, fake_delay=0, role="api"))
    engine = make_engine(db_url)
    try:
        auth = Auth(engine, timedelta(hours=1), timedelta(hours=1))
        for username in (OWNER, OTHER, "샘플+사용자%20"):
            auth.create_user(username, PASSWORD)
    finally:
        engine.dispose()
    # Start the API role once, without a broker issuer or a lifespan per account.
    with TestClient(app, base_url="https://testserver"):
        yield app


def _memo_login(app, username=OWNER):
    client = _login(app, username)
    client.headers[ACCOUNT_HEADER] = quote(username, safe="~!*'()")
    return client


def test_chart_memos_use_session_and_origin(memo_app):
    params = {"ticker": "SOXX"}
    with closing(TestClient(memo_app, base_url="https://testserver")) as anonymous:
        assert anonymous.get(URL, params=params).status_code == 401
        assert anonymous.put(URL, params=params, json={"text": "memo"}).status_code == 401
    with closing(_memo_login(memo_app)) as client:
        assert client.put(URL, params=params, json={"text": "memo"},
                          headers={"Origin": "https://foreign.test"}).status_code == 403
        response = client.get(URL, params=params)
        assert response.status_code == 200
        assert response.headers["cache-control"] == PRIVATE
        assert response.json() == {"ticker": "SOXX", "memo": None}


def test_chart_memos_keep_exact_product_account_and_utc_times(memo_app):
    with closing(_memo_login(memo_app)) as owner, closing(_memo_login(memo_app, OTHER)) as other:
        params = {"ticker": "SOXL"}
        response = owner.put(URL, params={"ticker": " soxl "}, json={"text": "  <b>my note</b>\n"})
        assert response.status_code == 200
        assert response.headers["cache-control"] == PRIVATE
        assert response.json()["ticker"] == "SOXL"
        first = response.json()["memo"]
        assert first["source"] == "user"
        assert first["text"] == "<b>my note</b>"
        assert datetime.fromisoformat(first["created_at"]).utcoffset() == timedelta()
        assert first["created_at"] == first["updated_at"]

        second = owner.put(URL, params=params, json={"text": "edited"}).json()["memo"]
        assert second["text"] == "edited"
        assert second["created_at"] == first["created_at"]
        assert datetime.fromisoformat(second["updated_at"]) >= datetime.fromisoformat(second["created_at"])
        assert owner.get(URL, params=params).json()["memo"] == second
        for ticker in ("SOXX", "SOXLUSDT", "NQ=F", "NAS100", "005930.KS"):
            assert owner.get(URL, params={"ticker": ticker}).json() == {"ticker": ticker, "memo": None}

        assert other.get(URL, params=params).json() == {"ticker": "SOXL", "memo": None}
        assert other.put(URL, params=params, json={"text": "a different account"}).status_code == 200
        cleared = owner.put(URL, params=params, json={"text": " \n\t"})
        assert cleared.status_code == 200
        assert cleared.headers["cache-control"] == PRIVATE
        assert cleared.json() == {"ticker": "SOXL", "memo": None}
        assert other.get(URL, params=params).json()["memo"]["text"] == "a different account"


def test_chart_memos_reject_account_hint_from_a_tab_with_a_new_session(memo_app):
    params = {"ticker": "SOXX"}
    with closing(_memo_login(memo_app)) as owner, closing(_memo_login(memo_app, OTHER)) as other:
        owner_memo = owner.put(URL, params=params, json={"text": "first tab memo"}).json()
        other_memo = other.put(URL, params=params, json={"text": "new session memo"}).json()
        stale_headers = {ACCOUNT_HEADER: OWNER}
        for response in (
            other.get(URL, params=params, headers=stale_headers),
            other.put(URL, params=params, headers=stale_headers, json={"text": "stale tab edit"}),
            other.put(URL, params=params, headers=stale_headers, json={"text": " "}),
        ):
            assert response.status_code == 409
            assert response.headers["cache-control"] == PRIVATE
            assert response.json() == {"detail": "chart memo account changed"}
        assert owner.get(URL, params=params).json() == owner_memo
        assert other.get(URL, params=params).json() == other_memo


def test_chart_memos_require_a_valid_utf8_account_hint(memo_app):
    params = {"ticker": "SOXX"}
    username = "샘플+사용자%20"
    with closing(_login(memo_app, username)) as client:
        for headers in ({}, {ACCOUNT_HEADER: "%FF"}):
            for response in (
                client.get(URL, params=params, headers=headers),
                client.put(URL, params=params, headers=headers, json={"text": "memo"}),
            ):
                assert response.status_code == 422
                assert response.headers["cache-control"] == PRIVATE
                assert response.json() == {"detail": "invalid chart memo request"}
        client.headers[ACCOUNT_HEADER] = quote(username, safe="~!*'()")
        assert client.put(URL, params=params, json={"text": "memo"}).status_code == 200
        assert client.get(URL, params=params).json()["memo"]["text"] == "memo"


@pytest.mark.parametrize("ticker", [" ", "x" * 21, "ＳＯＸＬ", "SOXL/USDT", "SOXL!"])
def test_chart_memos_reject_invalid_product(memo_app, ticker):
    with closing(_memo_login(memo_app)) as client:
        params = {"ticker": ticker}
        assert client.get(URL, params=params).status_code == 422
        assert client.put(URL, params=params, json={"text": "memo"}).status_code == 422


def test_chart_memos_validate_input_without_taking_owner_from_request(memo_app):
    with closing(_memo_login(memo_app)) as client:
        params = {"ticker": "SOXL"}
        for body in ({}, {"text": 123}, {"text": "x" * 2001}, {"text": "x\0y"}, {"text": "x", "user_id": 2}):
            assert client.put(URL, params=params, json=body).status_code == 422
        assert client.get(URL, params=params).json() == {"ticker": "SOXL", "memo": None}


def test_chart_memos_validation_does_not_echo_private_input(memo_app):
    with closing(_memo_login(memo_app)) as client:
        params = {"ticker": "SOXX"}
        secret = "synthetic private note"
        responses = (
            client.put(URL, params=params, json={"text": secret * 100}),
            client.put(URL, params=params, json={"text": {"value": secret}}),
            client.put(URL, params=params, content=f'{{"text":"{secret}"',
                       headers={"Content-Type": "application/json"}),
            client.put(URL, params=params, content=r'{"text":"\ud800"}',
                       headers={"Content-Type": "application/json"}),
            client.get(URL),
        )
        for response in responses:
            assert response.status_code == 422
            assert response.headers["cache-control"] == PRIVATE
            assert response.json() == {"detail": "invalid chart memo request"}


def test_0034_adds_only_the_account_product_memo_table(db_url):
    config = alembic_config(db_url)
    engine = make_engine(db_url)
    try:
        command.upgrade(config, "0033")
        previous_tables = set(inspect(engine).get_table_names())
        command.upgrade(config, "0034")
        schema = inspect(engine)
        assert set(schema.get_table_names()) - previous_tables == {"chart_memos"}
        assert schema.get_pk_constraint("chart_memos")["constrained_columns"] == ["user_id", "ticker"]
        [owner_fk] = schema.get_foreign_keys("chart_memos")
        assert owner_fk["constrained_columns"] == ["user_id"]
        assert owner_fk["referred_table"] == "users"
        assert owner_fk["options"]["ondelete"] == "CASCADE"
        command.downgrade(config, "0033")
        assert set(inspect(engine).get_table_names()) == previous_tables
    finally:
        engine.dispose()
