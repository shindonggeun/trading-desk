"""#766 two processes (api's chat and analyst's runs, #688 split) meet an expired ChatGPT sign-in at once.

The library renews under a lock on ``chatgpt-auth.json.lock`` beside the auth file and reads the file again
inside it, so the second process takes the token the first one wrote instead of replaying the refresh token
the first one rotated away (OpenAI ends the sign-in on a replay). These tests pin that down with two real
processes and a fake OAuth server that rotates refresh tokens and refuses a reused one - and show, with the
library's lock taken away, that the fake does catch the race. A TradingAgents SHA bump that loses the lock
fails here before the split is turned on (docs/deploy.md section 12).
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

WORKER = r'''
import contextlib, json, sys, time
from pathlib import Path

from tradingagents.dataflows.files import locked
from tradingagents.llm_clients import chatgpt_auth as ca

server, here, go, unlocked = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4] == "1"


def fake_token_request(form, task, refused):
    """OpenAI's token endpoint: rotates the refresh token, refuses a reused one. Answers one request at a time."""
    time.sleep(1.5)                      # both processes have read the file by now
    with locked(server):
        state = json.loads(server.read_text(encoding="utf-8"))
        if form.get("refresh_token") != state["valid"]:
            state["replayed"] += 1
            server.write_text(json.dumps(state), encoding="utf-8")
            raise ca.ChatGPTAuthError("OpenAI did not renew your ChatGPT sign-in (invalid_grant); sign in again.")
        n = state["refreshes"] + 1
        state.update(refreshes=n, valid=f"rt-{n}")
        server.write_text(json.dumps(state), encoding="utf-8")
        return {"access_token": f"at-{n}", "refresh_token": f"rt-{n}", "expires_in": 3600}


ca._token_request = fake_token_request
ca._signing_keys = lambda: []            # no id_token in the fake's answer: nothing to check against
if unlocked:
    ca.locked = lambda path, wait=True: contextlib.nullcontext(True)
assert ca.AUTH_FILE.parent == Path(sys.argv[5]), ca.AUTH_FILE      # the test's home, never the developer's

here.touch()
while not go.exists():
    time.sleep(0.01)
try:
    print(json.dumps({"token": ca.access_token(), "error": None}))
except ca.ChatGPTAuthError as exc:
    print(json.dumps({"token": None, "error": str(exc)}))
'''


def _race(tmp_path: Path, unlocked: bool) -> tuple[list[dict], dict, dict]:
    home = tmp_path / "home"
    auth_dir = home / ".tradingagents"
    auth_dir.mkdir(parents=True)
    auth = auth_dir / "chatgpt-auth.json"
    auth.write_text(json.dumps({
        "tokens": {"access_token": "at-0", "refresh_token": "rt-0", "expires_at": time.time() - 10,
                   "earliest_refresh_at": None, "scopes": []},
        "account": {"client_id": "client", "subject": "someone"},
    }), encoding="utf-8")
    server = tmp_path / "server.json"
    server.write_text(json.dumps({"valid": "rt-0", "refreshes": 0, "replayed": 0}), encoding="utf-8")
    script = tmp_path / "worker.py"
    script.write_text(WORKER, encoding="utf-8")
    go = tmp_path / "go"
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}       # ~ is the test's home on Linux and Windows
    procs, ready = [], []
    for i in range(2):
        ready.append(tmp_path / f"ready-{i}")
        procs.append(subprocess.Popen(
            [sys.executable, str(script), str(server), str(ready[i]), str(go), "1" if unlocked else "0", str(auth_dir)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
    deadline = time.monotonic() + 120
    while not all(r.exists() for r in ready):
        assert time.monotonic() < deadline and all(p.poll() is None for p in procs), \
            [p.communicate(timeout=5) for p in procs if p.poll() is not None]
        time.sleep(0.05)
    go.touch()
    answers = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err
        answers.append(json.loads(out.strip().splitlines()[-1]))
    return answers, json.loads(server.read_text(encoding="utf-8")), json.loads(auth.read_text(encoding="utf-8"))


def test_two_processes_renew_once_and_both_go_on_with_the_new_token(tmp_path):
    answers, server, auth = _race(tmp_path, unlocked=False)
    assert answers == [{"token": "at-1", "error": None}] * 2
    assert (server["refreshes"], server["replayed"]) == (1, 0)
    assert auth["tokens"]["refresh_token"] == "rt-1" and auth["tokens"]["access_token"] == "at-1"


def test_without_the_librarys_lock_the_second_refresh_replays_and_is_refused(tmp_path):
    """The control: the fake does see the race the lock prevents (so the test above means something)."""
    answers, server, _auth = _race(tmp_path, unlocked=True)
    assert server["replayed"] >= 1
    assert any(a["error"] and "invalid_grant" in a["error"] for a in answers)
