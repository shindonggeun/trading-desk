from contextlib import closing
from datetime import timedelta

from fastapi.testclient import TestClient

from desk.api import create_app
from desk.auth import Auth
from desk.db import instruments, make_engine, now
from desk.favorites import MAX_FAVORITES
from desk.runner import make_fake_runner
from desk.settings import Settings

OWNER, OTHER, PASSWORD = "owner", "other", "correct horse battery"


def _app(db_url):
    app = create_app(Settings(database_url=db_url, fake_runs=True, fake_delay=0), runner=make_fake_runner(0))
    engine = make_engine(db_url)
    auth = Auth(engine, timedelta(hours=1), timedelta(hours=1))
    auth.create_user(OWNER, PASSWORD)
    auth.create_user(OTHER, PASSWORD)
    with engine.begin() as conn:
        conn.execute(instruments.insert().values(
            symbol="005930", yahoo="005930.KS", name="삼성전자", name_key="삼성전자", market="KOSPI",
            security_type="STOCK", updated_at=now()))
    engine.dispose()
    return app


def _login(app, username=OWNER) -> TestClient:
    client = TestClient(app, base_url="https://testserver")
    assert client.post("/api/auth/login", json={"username": username, "password": PASSWORD}).status_code == 204
    return client


def _tickers(client) -> list[str]:
    return [f["ticker"] for f in client.get("/api/favorites").json()]


def test_add_keeps_order_skips_duplicates_and_fills_the_name(db_url):
    app = _app(db_url)
    with _login(app) as client:
        assert client.get("/api/favorites").json() == []
        first = client.post("/api/favorites", json={"ticker": " nvda ", "name": "엔비디아"})
        assert first.status_code == 201
        assert first.json() == [{"ticker": "NVDA", "name": "엔비디아", "position": 0}]
        assert client.post("/api/favorites", json={"ticker": "005930.ks"}).status_code == 201
        again = client.post("/api/favorites", json={"ticker": "NVDA", "name": "다른 이름"})
        assert again.status_code == 200                       # already there: left as is
        assert again.json() == [
            {"ticker": "NVDA", "name": "엔비디아", "position": 0},
            {"ticker": "005930.KS", "name": "삼성전자", "position": 1},   # from the listing
        ]
        assert client.post("/api/favorites", json={"ticker": "TSLA"}).json()[2] == {
            "ticker": "TSLA", "name": None, "position": 2}

        assert client.delete("/api/favorites/005930.ks").status_code == 204
        assert client.delete("/api/favorites/005930.KS").status_code == 204   # gone already: still fine
        assert client.get("/api/favorites").json() == [
            {"ticker": "NVDA", "name": "엔비디아", "position": 0},
            {"ticker": "TSLA", "name": None, "position": 1},
        ]
        assert client.post("/api/favorites", json={"ticker": "AAPL"}).json()[-1]["position"] == 2


def test_replace_reorders_imports_and_keeps_names(db_url):
    app = _app(db_url)
    with _login(app) as client:
        client.post("/api/favorites", json={"ticker": "NVDA", "name": "엔비디아"})
        client.post("/api/favorites", json={"ticker": "TSLA"})
        put = client.put("/api/favorites", json={"items": [
            {"ticker": "tsla"}, {"ticker": "005930.KS"}, {"ticker": "nvda"}, {"ticker": "TSLA", "name": "중복"}]})
        assert put.status_code == 200
        assert put.json() == [
            {"ticker": "TSLA", "name": None, "position": 0},
            {"ticker": "005930.KS", "name": "삼성전자", "position": 1},
            {"ticker": "NVDA", "name": "엔비디아", "position": 2},   # kept from before
        ]
        assert client.put("/api/favorites", json={"items": []}).json() == []
        assert _tickers(client) == []


def test_cap_of_fifty(db_url):
    app = _app(db_url)
    with _login(app) as client:
        many = [{"ticker": f"T{i}"} for i in range(MAX_FAVORITES + 1)]
        assert client.put("/api/favorites", json={"items": many}).status_code == 422
        # repeats do not count: 51 entries, 50 tickers
        assert client.put("/api/favorites", json={"items": many[:MAX_FAVORITES] + many[:1]}).status_code == 200
        assert client.put("/api/favorites", json={"items": many * 4}).status_code == 422   # body bound (200)
        assert client.put("/api/favorites", json={"items": many[:MAX_FAVORITES]}).status_code == 200
        full = client.post("/api/favorites", json={"ticker": "ONEMORE"})
        assert full.status_code == 409
        assert len(_tickers(client)) == MAX_FAVORITES
        # a repeat at the cap is still "already there", not "full"
        assert client.post("/api/favorites", json={"ticker": "T0"}).status_code == 200


def test_accounts_are_separate_and_list_survives_a_restart(db_url):
    app = _app(db_url)
    with _login(app) as owner, closing(_login(app, OTHER)) as other:
        owner.post("/api/favorites", json={"ticker": "NVDA"})
        other.post("/api/favorites", json={"ticker": "TSLA"})
        other.delete("/api/favorites/NVDA")                   # not theirs: nothing happens
        assert _tickers(owner) == ["NVDA"]
        assert _tickers(other) == ["TSLA"]
    again = create_app(Settings(database_url=db_url, fake_runs=True, fake_delay=0), runner=make_fake_runner(0))
    with _login(again) as owner:
        assert _tickers(owner) == ["NVDA"]


def test_names_are_cleaned_and_blank_falls_back_to_the_listing(db_url):
    app = _app(db_url)
    with _login(app) as client:
        got = client.post("/api/favorites", json={"ticker": "NVDA", "name": "엔비\u202e디아\n"}).json()
        assert got[0]["name"] == "엔비디아"
        got = client.post("/api/favorites", json={"ticker": "005930.KS", "name": "  "}).json()
        assert got[1]["name"] == "삼성전자"


def test_login_origin_and_ticker_checks(db_url):
    app = _app(db_url)
    with TestClient(app, base_url="https://testserver") as anon:
        assert anon.get("/api/favorites").status_code == 401
        assert anon.post("/api/favorites", json={"ticker": "NVDA"}).status_code == 401
        assert anon.put("/api/favorites", json={"items": []}).status_code == 401
        assert anon.delete("/api/favorites/NVDA").status_code == 401
    with _login(app) as client:
        evil = {"origin": "https://evil.example"}
        assert client.post("/api/favorites", json={"ticker": "NVDA"}, headers=evil).status_code == 403
        assert client.put("/api/favorites", json={"items": []}, headers=evil).status_code == 403
        assert client.delete("/api/favorites/NVDA", headers=evil).status_code == 403
        assert client.post("/api/favorites", json={"ticker": "NV DA;"}).status_code == 422
        assert client.post("/api/favorites", json={"ticker": ""}).status_code == 422
        assert client.put("/api/favorites", json={"items": [{"ticker": "<x>"}]}).status_code == 422
        assert client.delete("/api/favorites/a%20b").status_code == 422
        assert client.post("/api/favorites", json={"ticker": "NVDA", "name": "x" * 201}).status_code == 422
        assert client.post("/api/favorites", json={"ticker": "삼성"}).status_code == 422      # ASCII only
        assert client.post("/api/favorites", json={"ticker": "１２３"}).status_code == 422
        assert _tickers(client) == []
