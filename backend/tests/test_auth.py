from datetime import timedelta

from sqlalchemy import select, update

from desk.auth import COOKIE, Auth, LoginThrottle, hash_token
from desk.db import make_engine, now, sessions
from tests.test_api import OWNER, PASSWORD, _client

CREDS = {"username": OWNER, "password": PASSWORD}


def test_everything_but_health_and_login_needs_a_session(db_url):
    with _client(db_url, login=False) as client:
        assert client.get("/api/health").json() == {"ok": True}
        for path in ("/api/runs", "/api/portfolio", "/api/config", "/api/auth/me", "/api/regime",
                     "/api/runs/x/stream", "/docs", "/openapi.json", "/api/health/", "/API/runs"):
            assert client.get(path).status_code == 401, path
        assert client.post("/api/runs", json={"ticker": "NVDA"}).status_code == 401
        assert client.delete("/api/runs/whatever").status_code == 401


def test_login_sets_a_strict_httponly_cookie_and_logout_kills_it(db_url):
    with _client(db_url, login=False) as client:
        response = client.post("/api/auth/login", json=CREDS)
        assert response.status_code == 204
        cookie = response.headers["set-cookie"]
        for flag in ("HttpOnly", "Secure", "SameSite=strict", "Path=/"):
            assert flag.lower() in cookie.lower(), flag
        token = client.cookies.get(COOKIE)
        assert client.get("/api/auth/me").json()["username"] == OWNER

        engine = make_engine(db_url)
        with engine.connect() as conn:  # only the hash is stored
            stored = conn.execute(select(sessions.c.token_hash)).scalars().all()
        engine.dispose()
        assert stored == [hash_token(token)]

        assert client.post("/api/auth/logout").status_code == 204
        client.cookies.clear()
        client.cookies.set(COOKIE, token)  # a copied cookie is dead after logout
        assert client.get("/api/auth/me").status_code == 401


def test_wrong_password_and_unknown_user_look_the_same(db_url):
    with _client(db_url, login=False) as client:
        bad = client.post("/api/auth/login", json={**CREDS, "password": "nope-nope-nope"})
        ghost = client.post("/api/auth/login", json={"username": "ghost", "password": PASSWORD})
        assert bad.status_code == ghost.status_code == 401
        assert bad.json() == ghost.json()
        assert COOKIE not in client.cookies


def test_login_replaces_the_previous_session(db_url):
    with _client(db_url) as client:
        old = client.cookies.get(COOKIE)
        assert client.post("/api/auth/login", json=CREDS).status_code == 204
        assert client.cookies.get(COOKIE) != old
        client.cookies.clear()
        client.cookies.set(COOKIE, old)
        assert client.get("/api/auth/me").status_code == 401


def test_expired_and_idle_sessions_are_refused(db_url):
    with _client(db_url) as client:
        engine = make_engine(db_url)
        key = hash_token(client.cookies.get(COOKIE))
        stale = sessions.c.token_hash == key
        with engine.begin() as conn:
            conn.execute(update(sessions).where(stale).values(last_seen_at=now() - timedelta(hours=13)))
        assert client.get("/api/runs").status_code == 401   # idle 12h by default
        with engine.connect() as conn:
            assert conn.execute(select(sessions).where(stale)).first() is None
        engine.dispose()


def test_failed_logins_are_throttled_even_with_the_right_password_after(db_url):
    with _client(db_url, login=False) as client:
        for _ in range(5):
            assert client.post("/api/auth/login", json={**CREDS, "password": "x" * 12}).status_code == 401
        blocked = client.post("/api/auth/login", json=CREDS)
        assert blocked.status_code == 429
        assert int(blocked.headers["retry-after"]) > 0


def test_cross_origin_writes_are_refused(db_url):
    with _client(db_url) as client:
        evil = {"Origin": "https://evil.example"}
        assert client.post("/api/runs", json={"ticker": "NVDA"}, headers=evil).status_code == 403
        assert client.delete("/api/runs/whatever", headers=evil).status_code == 403
        assert client.post("/api/auth/login", json=CREDS, headers=evil).status_code == 403
        same = {"Origin": "https://testserver"}
        assert client.post("/api/runs", json={"ticker": "NVDA"}, headers=same).status_code == 201


def test_set_password_signs_everyone_out_and_rejects_short_ones(db_url):
    with _client(db_url) as client:
        engine = make_engine(db_url)
        auth = Auth(engine, timedelta(hours=1), timedelta(hours=1))
        try:
            auth.set_password(OWNER, "short")
            raise AssertionError("short password accepted")
        except ValueError:
            pass
        assert auth.set_password(OWNER, "another long passphrase")
        engine.dispose()
        assert client.get("/api/runs").status_code == 401


def test_throttle_counts_before_checking_and_forgets_old_attempts():
    throttle = LoginThrottle(per_ip=2, window_sec=0.05)
    assert throttle.attempt("1.2.3.4") == 0
    assert throttle.attempt("1.2.3.4") == 0
    assert throttle.attempt("1.2.3.4") > 0      # third in the window: refused before any password check
    assert throttle.attempt("5.6.7.8") == 0     # other clients are not affected
    import time
    # well past the window: Windows' monotonic clock moves in ~15.6 ms steps, 0.06 s could read as 0.047 (#213)
    time.sleep(0.2)
    assert throttle.attempt("1.2.3.4") == 0
    throttle.succeeded("1.2.3.4")
    assert throttle.attempt("1.2.3.4") == 0


def test_cookie_uses_host_prefix():
    assert COOKIE.startswith("__Host-")


def test_open_stream_ends_when_the_session_is_revoked(db_url):
    with _client(db_url) as client:
        run_id = client.post("/api/runs", json={"ticker": "NVDA"}).json()["id"]
        engine = make_engine(db_url)
        Auth(engine, timedelta(hours=1), timedelta(hours=1)).revoke_all(OWNER)
        engine.dispose()
        # Auth passed at the door only via the middleware; revoked now, so the stream is refused
        assert client.get(f"/api/runs/{run_id}/stream").status_code == 401


def test_a_run_streams_session_re_check_is_not_activity(db_url, monkeypatch):
    """#753: every round's re-check passes touch=False, so an open tab does not hold off the idle expiry."""
    from tests.test_api import _wait_done

    seen: list[bool] = []
    real = Auth.resolve

    def spy(self, token, *, touch):
        seen.append(touch)
        return real(self, token, touch=touch)

    with _client(db_url) as client:
        run_id = client.post("/api/runs", json={"ticker": "NVDA"}).json()["id"]
        _wait_done(client, run_id)
        monkeypatch.setattr(Auth, "resolve", spy)
        with client.stream("GET", f"/api/runs/{run_id}/stream") as response:
            assert response.status_code == 200
            list(response.iter_lines())
    # #762 opening the stream is not activity either (is_activity): the screen's own requests are
    assert len(seen) >= 2 and not any(seen)


def test_every_stream_re_check_in_the_api_passes_touch_false():
    """#753: the middleware is the one place a session check can be activity; any other auth.resolve in
    desk/api.py is a background re-check (an open stream) and must say touch=False. #762 read from the
    syntax tree, so a comment or a line break cannot pass for it."""
    import ast
    from pathlib import Path

    import desk.api

    tree = ast.parse(Path(desk.api.__file__).read_text(encoding="utf-8"))
    touches = []
    for node in ast.walk(tree):
        # to_thread(auth.resolve, ..., touch=...) and a direct auth.resolve(..., touch=...) alike
        if isinstance(node, ast.Call) and ("auth.resolve" == ast.unparse(node.func)
                                           or (node.args and ast.unparse(node.args[0]) == "auth.resolve")):
            touches.append(next((ast.unparse(k.value) for k in node.keywords if k.arg == "touch"), None))
    door = [t for t in touches if t != "False"]
    assert len(touches) >= 6 and door == ["is_activity(request)"], touches
    assert sum("auth.resolve" in ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Name | ast.Attribute)
               and ast.unparse(n) == "auth.resolve") == len(touches)       # no alias passed around


def test_what_counts_as_activity():
    """#762: a request is activity unless the screen marked it background or it opens an SSE stream (an
    EventSource cannot carry the header, and reconnects by itself after a restart or a network change)."""
    from starlette.requests import Request

    from desk.api import is_activity

    def req(method, path, headers=()):
        return Request({"type": "http", "method": method, "path": path, "query_string": b"",
                        "headers": [(k.lower().encode(), v.encode()) for k, v in headers]})

    assert is_activity(req("GET", "/api/runs"))
    assert is_activity(req("POST", "/api/chat/threads/t/messages"))      # sending a chat message is the person
    assert is_activity(req("GET", "/api/runs", [("X-Desk-Background", "0")]))
    assert not is_activity(req("GET", "/api/runs", [("X-Desk-Background", "1")]))
    for path in ("/api/runs/r1/stream", "/api/portfolio/stream", "/api/live/stream", "/api/stream/market"):
        assert not is_activity(req("GET", path)), path


def test_the_screen_and_the_server_name_the_background_header_alike():
    """#762: frontend/src/activity.ts and desk.api must agree, or the screen's marks are ignored (fail open)."""
    import re
    from pathlib import Path

    import desk.api

    src = (Path(desk.api.__file__).resolve().parents[2] / "frontend" / "src" / "activity.ts").read_text(encoding="utf-8")
    name = re.search(r'BACKGROUND_HEADER = "([^"]+)"', src).group(1)
    assert name.lower() == desk.api.BACKGROUND_HEADER
    assert '[BACKGROUND_HEADER]: "1"' in src


def test_opening_a_stream_is_not_activity(db_url, monkeypatch):
    """#762: a reconnecting EventSource (after a deploy, say) must not keep an unattended tab's session alive."""
    from desk import auth as auth_module
    from desk.db import now as real_now

    clock = [real_now()]
    monkeypatch.setattr(auth_module, "now", lambda: clock[0])
    with _client(db_url) as client:
        run_id = client.post("/api/runs", json={"ticker": "NVDA"}).json()["id"]
        from tests.test_api import _wait_done
        _wait_done(client, run_id)
        engine = make_engine(db_url)
        with engine.connect() as conn:
            start = conn.execute(select(sessions.c.last_seen_at)).scalar()
        clock[0] += timedelta(minutes=5)
        with client.stream("GET", f"/api/runs/{run_id}/stream") as response:
            assert response.status_code == 200
            list(response.iter_lines())
        with engine.connect() as conn:
            assert conn.execute(select(sessions.c.last_seen_at)).scalar() == start
        engine.dispose()


def test_a_request_marked_background_is_checked_but_is_not_activity(db_url, monkeypatch):
    """#762: the screen's automatic reads (frontend/src/activity.ts) carry X-Desk-Background: 1 - the
    session is checked as always, but last_seen_at does not move; a request without it moves it."""
    from desk import auth as auth_module
    from desk.db import now as real_now

    clock = [real_now()]
    monkeypatch.setattr(auth_module, "now", lambda: clock[0])

    def seen():
        engine = make_engine(db_url)
        with engine.connect() as conn:
            got = conn.execute(select(sessions.c.last_seen_at)).scalar()
        engine.dispose()
        return got

    with _client(db_url) as client:
        start = seen()
        clock[0] += timedelta(minutes=5)
        assert client.get("/api/auth/me", headers={"X-Desk-Background": "1"}).status_code == 200
        assert seen() == start
        clock[0] += timedelta(hours=11, minutes=56)      # 12 h 1 min after the last real request
        assert client.get("/api/auth/me", headers={"X-Desk-Background": "1"}).status_code == 401
        assert client.post("/api/auth/login", json=CREDS).status_code == 204
        start = seen()
        clock[0] += timedelta(minutes=5)
        assert client.get("/api/auth/me", headers={"X-Desk-Background": "0"}).status_code == 200
        assert seen() != start                           # anything but "1" is a request like any other
