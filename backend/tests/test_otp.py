import time
from datetime import timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient

from desk import cli, otp
from desk.api import create_app
from desk.auth import COOKIE, Auth
from desk.db import make_engine
from desk.runner import make_fake_runner
from desk.settings import Settings
from tests.test_api import OWNER, PASSWORD, _client

KEY = otp.new_key()
CREDS = {"username": OWNER, "password": PASSWORD}


def _code(secret: str, shift: int = 0) -> str:
    return pyotp.TOTP(secret).at(time.time() + shift)


def _enroll(client) -> tuple[str, list[str]]:
    setup = client.post("/api/auth/otp/setup", json={"password": PASSWORD})
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    assert setup.json()["uri"].startswith("otpauth://totp/") and "Trading%20Desk" in setup.json()["uri"]
    enabled = client.post("/api/auth/otp/enable", json={"code": _code(secret)})
    assert enabled.status_code == 200, enabled.text
    return secret, enabled.json()["recovery_codes"]


def test_matching_step_allows_one_step_of_drift_and_never_the_same_step_twice():
    secret = otp.new_secret()
    totp, at = pyotp.TOTP(secret), 1_800_000_000
    now_step = at // 30
    assert otp.matching_step(secret, totp.at(at), None, at) == now_step
    assert otp.matching_step(secret, totp.at(at - 30), None, at) == now_step - 1      # clock behind
    assert otp.matching_step(secret, totp.at(at + 30), None, at) == now_step + 1      # clock ahead
    assert otp.matching_step(secret, totp.at(at - 60), None, at) is None             # too old
    assert otp.matching_step(secret, totp.at(at), now_step, at) is None              # already used
    assert otp.matching_step(secret, "12345", None, at) is None
    box = otp.SecretBox(KEY)
    assert box.open(box.seal(secret)) == secret
    with pytest.raises(otp.OtpUnavailable):
        otp.SecretBox(otp.new_key()).open(box.seal(secret))                         # wrong key


def test_enrolment_needs_the_password_and_a_matching_code(db_url):
    with _client(db_url, otp_key=KEY) as client:
        assert client.post("/api/auth/otp/setup", json={"password": "wrong-password-x"}).status_code == 403
        assert client.post("/api/auth/otp/enable", json={"code": "000000"}).status_code == 403   # nothing pending
        secret = client.post("/api/auth/otp/setup", json={"password": PASSWORD}).json()["secret"]
        wrong = "000000" if _code(secret) != "000000" else "111111"
        assert client.post("/api/auth/otp/enable", json={"code": wrong}).status_code == 403
        assert client.get("/api/auth/me").json()["otp_enabled"] is False
        assert client.post("/api/auth/otp/enable", json={"code": _code(secret)}).status_code == 200
        me = client.get("/api/auth/me").json()
        assert me["otp_enabled"] is True and me["recovery_codes_left"] == 10
        assert client.post("/api/auth/otp/setup", json={"password": PASSWORD}).status_code == 409


def test_with_otp_on_a_password_alone_never_opens_a_session(db_url):
    with _client(db_url, otp_key=KEY) as client:
        secret, _ = _enroll(client)
        client.cookies.clear()
        first = client.post("/api/auth/login", json=CREDS)
        assert first.status_code == 200 and first.json()["otp_required"] is True
        assert "set-cookie" not in first.headers and client.get("/api/runs").status_code == 401
        challenge = first.json()["challenge"]

        wrong = "000000" if _code(secret, 30) != "000000" else "111111"
        assert client.post("/api/auth/login", json={"challenge": challenge, "code": wrong}).status_code == 401
        code = _code(secret, 30)                    # the enrolment used this step; the next one is fresh
        ok = client.post("/api/auth/login", json={"challenge": challenge, "code": code})
        assert ok.status_code == 204 and client.cookies.get(COOKIE)
        assert client.get("/api/runs").status_code == 200

        # The same code cannot open a second session, and a used challenge is gone.
        client.cookies.clear()
        again = client.post("/api/auth/login", json=CREDS).json()["challenge"]
        assert client.post("/api/auth/login", json={"challenge": again, "code": code}).status_code == 401
        assert client.post("/api/auth/login", json={"challenge": challenge, "code": code}).status_code == 401


def test_recovery_codes_work_once_and_a_challenge_burns_after_five_tries(db_url):
    with _client(db_url, otp_key=KEY) as client:
        _, recovery = _enroll(client)
        client.cookies.clear()
        challenge = client.post("/api/auth/login", json=CREDS).json()["challenge"]
        spaced = recovery[0].upper().replace("-", " ")                    # case and separators do not matter
        assert client.post("/api/auth/login", json={"challenge": challenge, "code": spaced}).status_code == 204
        assert client.get("/api/auth/me").json()["recovery_codes_left"] == 9
        client.cookies.clear()
        challenge = client.post("/api/auth/login", json=CREDS).json()["challenge"]
        assert client.post("/api/auth/login", json={"challenge": challenge, "code": recovery[0]}).status_code == 401

    otp_limit = otp.Challenges(tries=5)
    token = otp_limit.issue(7)
    assert [otp_limit.user_for(token) for _ in range(6)] == [7, 7, 7, 7, 7, None]


def test_login_body_must_be_one_step():
    for body in ({"username": "a"}, {"challenge": "x"}, {"username": "a", "password": "b", "code": "1"}, {}):
        with pytest.raises(ValueError):
            from desk.api import LoginIn
            LoginIn(**body)


def test_without_the_key_otp_cannot_start_and_an_otp_account_is_refused_not_waved_through(db_url):
    with _client(db_url, otp_key=KEY) as client:
        _enroll(client)
    keyless = Settings(database_url=db_url, fake_runs=True, fake_delay=0, otp_key="")
    with TestClient(create_app(keyless, runner=make_fake_runner(0)), base_url="https://testserver") as client:
        login = client.post("/api/auth/login", json=CREDS)
        assert login.status_code == 503 and COOKIE not in client.cookies


def test_disable_needs_password_and_code_and_the_cli_can_force_it(db_url, monkeypatch):
    with _client(db_url, otp_key=KEY) as client:
        _, recovery = _enroll(client)
        assert client.post("/api/auth/otp/disable",
                           json={"password": "wrong-password-x", "code": recovery[0]}).status_code == 403
        assert client.post("/api/auth/otp/disable",
                           json={"password": PASSWORD, "code": recovery[1]}).status_code == 204
        assert client.get("/api/auth/me").json()["otp_enabled"] is False
        assert client.post("/api/auth/login", json=CREDS).status_code == 204   # a full login clears the count
        _enroll(client)                                   # on again, for the CLI to force off

    monkeypatch.setenv("DESK_DATABASE_URL", db_url)
    assert cli.main(["disable-otp", OWNER]) == 0
    engine = make_engine(db_url)
    auth = Auth(engine, timedelta(hours=1), timedelta(hours=1))
    assert auth.otp_enabled(1) is False and auth.recovery_left(1) == 0
    engine.dispose()
    with pytest.raises(SystemExit, match="no such user"):
        cli.main(["disable-otp", "ghost"])


def test_enabling_signs_other_browsers_out_but_keeps_this_one(db_url):
    with _client(db_url, otp_key=KEY) as client:
        other = client.cookies.get(COOKIE)
        client.cookies.clear()
        assert client.post("/api/auth/login", json=CREDS).status_code == 204       # a second browser
        _enroll(client)
        assert client.get("/api/runs").status_code == 200
        client.cookies.clear()
        client.cookies.set(COOKIE, other)
        assert client.get("/api/runs").status_code == 401


def test_changing_the_factor_counts_every_call_so_codes_cannot_be_ground_through_disable(db_url):
    """Review HIGH: a right password used to clear the IP count, leaving code guesses unlimited."""
    with _client(db_url, otp_key=KEY) as client:
        secret, _ = _enroll(client)                                     # 2 attempts
        wrong = "000000" if _code(secret) != "000000" else "111111"
        for _ in range(3):                                              # attempts 3-5
            assert client.post("/api/auth/otp/disable",
                               json={"password": PASSWORD, "code": wrong}).status_code == 403
        blocked = client.post("/api/auth/otp/disable", json={"password": PASSWORD, "code": _code(secret, 30)})
        assert blocked.status_code == 429                               # even with the right answer now
        assert client.get("/api/auth/me").json()["otp_enabled"] is True


def test_an_unfinished_enrolment_expires(db_url, monkeypatch):
    with _client(db_url, otp_key=KEY) as client:
        secret = client.post("/api/auth/otp/setup", json={"password": PASSWORD}).json()["secret"]
        monkeypatch.setattr(otp, "PENDING_TTL_SEC", -1)                 # as if ten minutes passed
        assert client.post("/api/auth/otp/enable", json={"code": _code(secret)}).status_code == 403
        assert client.get("/api/auth/me").json()["otp_enabled"] is False


def test_code_failures_across_ips_cap_the_account(db_url, monkeypatch):
    monkeypatch.setattr(otp, "USER_FAILS", 3)
    import desk.api
    # The test client has one address; take the IP from a header so each try comes from a new one.
    monkeypatch.setattr(desk.api, "_client_ip", lambda request: request.headers.get("x-forwarded-for", "testclient"))
    with _client(db_url, otp_key=KEY) as client:
        secret, _ = _enroll(client)
        client.cookies.clear()
        wrong = "000000" if _code(secret, 30) != "000000" else "111111"
        ips = iter(f"203.0.113.{n}" for n in range(1, 50))
        for _ in range(3):                                              # each from a fresh IP
            ip = next(ips)
            challenge = client.post("/api/auth/login", json=CREDS, headers={"X-Forwarded-For": ip}).json()["challenge"]
            client.post("/api/auth/login", json={"challenge": challenge, "code": wrong}, headers={"X-Forwarded-For": ip})
        capped = client.post("/api/auth/login", json=CREDS, headers={"X-Forwarded-For": next(ips)})
        assert capped.status_code == 429 and "challenge" not in capped.json()


def test_web_disable_signs_out_other_browsers(db_url):
    with _client(db_url, otp_key=KEY) as client:
        _, recovery = _enroll(client)                                   # enabling kept only this browser
        mine = client.cookies.get(COOKIE)
        engine = make_engine(db_url)
        auth = Auth(engine, timedelta(hours=1), timedelta(hours=1))
        from desk.auth import User
        stolen = auth.open_session(User(1, OWNER), "198.51.100.9", "thief")
        engine.dispose()
        assert client.post("/api/auth/otp/disable", json={"password": PASSWORD, "code": recovery[0]}).status_code == 204
        assert client.get("/api/runs").status_code == 200 and client.cookies.get(COOKIE) == mine
        client.cookies.clear()
        client.cookies.set(COOKIE, stolen)
        assert client.get("/api/runs").status_code == 401


def test_a_malformed_key_only_disables_otp():
    box = otp.SecretBox("not-a-fernet-key")
    assert box.available is False and "not a valid Fernet key" in box.error

