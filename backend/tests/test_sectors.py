from contextlib import closing
from datetime import timedelta

from fastapi.testclient import TestClient

from desk.api import create_app
from desk.auth import Auth
from desk.db import make_engine
from desk.runner import make_fake_runner
from desk.sectors import DEFAULTS, MAX_SECTORS, MAX_TICKERS
from desk.settings import Settings

OWNER, OTHER, PASSWORD = "owner", "other", "correct horse battery"


def _app(db_url):
    app = create_app(Settings(database_url=db_url, fake_runs=True, fake_delay=0), runner=make_fake_runner(0))
    engine = make_engine(db_url)
    auth = Auth(engine, timedelta(hours=1), timedelta(hours=1))
    auth.create_user(OWNER, PASSWORD)
    auth.create_user(OTHER, PASSWORD)
    engine.dispose()
    return app


def _login(app, username=OWNER) -> TestClient:
    client = TestClient(app, base_url="https://testserver")
    assert client.post("/api/auth/login", json={"username": username, "password": PASSWORD}).status_code == 204
    return client


def _names(client) -> list[str]:
    return [s["name"] for s in client.get("/api/sectors").json()]


def test_a_new_account_gets_the_default_sectors_once(db_url):
    app = _app(db_url)
    with _login(app) as client:
        first = client.get("/api/sectors").json()
        assert [s["name"] for s in first] == [name for name, _ in DEFAULTS]
        assert [s["position"] for s in first] == list(range(len(DEFAULTS)))
        assert first[0]["tickers"][0] == {"ticker": "005930.KS", "name": "삼성전자"}
        assert client.get("/api/sectors").json() == first          # not seeded twice
        for s in first:
            assert client.delete(f"/api/sectors/{s['id']}").status_code == 204
        assert len(client.get("/api/sectors").json()) == len(DEFAULTS)   # all deleted: defaults come back


def test_create_update_reorder_and_delete(db_url):
    app = _app(db_url)
    with _login(app) as client:
        base = client.get("/api/sectors").json()
        made = client.post("/api/sectors", json={"name": "  AI 인프라 ", "tickers": [
            {"ticker": "nvda", "name": "엔비디아"}, {"ticker": "VRT"}, {"ticker": "NVDA", "name": "중복"}]})
        assert made.status_code == 201
        ai = made.json()
        assert ai["name"] == "AI 인프라" and ai["position"] == len(base)
        assert ai["tickers"] == [{"ticker": "NVDA", "name": "엔비디아"}, {"ticker": "VRT", "name": None}]

        renamed = client.put(f"/api/sectors/{ai['id']}", json={"name": "AI 전력"}).json()
        assert renamed["name"] == "AI 전력" and renamed["tickers"] == ai["tickers"]   # tickers untouched
        moved = client.put(f"/api/sectors/{ai['id']}", json={"tickers": [{"ticker": "VST"}]}).json()
        assert moved["tickers"] == [{"ticker": "VST", "name": None}] and moved["name"] == "AI 전력"

        ids = [s["id"] for s in client.get("/api/sectors").json()]
        order = client.put("/api/sectors/order", json={"ids": [ids[-1], *ids[:-1]]})
        assert order.status_code == 200 and order.json()[0]["name"] == "AI 전력"
        assert [s["position"] for s in order.json()] == list(range(len(ids)))
        assert client.put("/api/sectors/order", json={"ids": ids[:-1]}).status_code == 422          # one missing
        assert client.put("/api/sectors/order", json={"ids": [ids[0], *ids]}).status_code == 422    # repeated

        assert client.delete(f"/api/sectors/{ai['id']}").status_code == 204
        assert client.delete(f"/api/sectors/{ai['id']}").status_code == 204   # gone already: still fine
        assert "AI 전력" not in _names(client)


def test_names_limits_and_errors(db_url):
    app = _app(db_url)
    with _login(app) as client:
        base = client.get("/api/sectors").json()
        dup = client.post("/api/sectors", json={"name": base[0]["name"]})
        assert dup.status_code == 409
        assert client.put(f"/api/sectors/{base[1]['id']}", json={"name": base[0]["name"]}).status_code == 409
        assert client.put(f"/api/sectors/{base[0]['id']}", json={"name": base[0]["name"]}).status_code == 200
        assert client.post("/api/sectors", json={"name": "   "}).status_code == 422
        assert client.post("/api/sectors", json={"name": chr(0x202E)}).status_code == 422   # only a format char
        assert client.post("/api/sectors", json={"name": "x" * 41}).status_code == 422
        too_many = [{"ticker": f"T{i}"} for i in range(MAX_TICKERS + 1)]
        assert client.post("/api/sectors", json={"name": "big", "tickers": too_many}).status_code == 422
        assert client.put(f"/api/sectors/{base[0]['id']}", json={"tickers": too_many}).status_code == 422
        assert client.post("/api/sectors", json={"name": "ok", "tickers": too_many[:MAX_TICKERS] * 2}).status_code == 201
        assert client.post("/api/sectors", json={"name": "bad", "tickers": [{"ticker": "삼성"}]}).status_code == 422
        assert client.put("/api/sectors/999999", json={"name": "nope"}).status_code == 404
        assert client.put("/api/sectors/abc", json={"name": "nope"}).status_code == 422
        assert client.put(f"/api/sectors/{10**20}", json={"name": "nope"}).status_code == 422   # not a driver overflow
        assert client.delete(f"/api/sectors/{10**20}").status_code == 422
        assert client.put("/api/sectors/order", json={"ids": [10**20]}).status_code == 422
        for i in range(MAX_SECTORS - len(base) - 1):
            assert client.post("/api/sectors", json={"name": f"s{i}"}).status_code == 201
        assert client.post("/api/sectors", json={"name": "one more"}).status_code == 409


def test_accounts_are_separate_login_and_origin(db_url):
    app = _app(db_url)
    with TestClient(app, base_url="https://testserver") as anon:
        assert anon.get("/api/sectors").status_code == 401
        assert anon.post("/api/sectors", json={"name": "x"}).status_code == 401
        assert anon.put("/api/sectors/order", json={"ids": []}).status_code == 401
        assert anon.delete("/api/sectors/1").status_code == 401
    with _login(app) as owner, closing(_login(app, OTHER)) as other:
        mine = owner.post("/api/sectors", json={"name": "내 섹터"}).json()
        assert "내 섹터" not in _names(other)
        assert other.put(f"/api/sectors/{mine['id']}", json={"name": "뺏기"}).status_code == 404
        theirs = other.get("/api/sectors").json()[0]["name"]
        assert other.put(f"/api/sectors/{mine['id']}", json={"name": theirs}).status_code == 404   # not 409
        assert other.delete(f"/api/sectors/{mine['id']}").status_code == 204       # not theirs: nothing happens
        assert "내 섹터" in _names(owner)
        other_ids = [s["id"] for s in other.get("/api/sectors").json()]
        assert other.put("/api/sectors/order", json={"ids": [*other_ids[1:], mine["id"]]}).status_code == 422
        evil = {"origin": "https://evil.example"}
        assert owner.post("/api/sectors", json={"name": "x"}, headers=evil).status_code == 403
        assert owner.delete(f"/api/sectors/{mine['id']}", headers=evil).status_code == 403
