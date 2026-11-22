"""#712: log lines carry no login e-mail and no full client address."""

import ast
import logging
import re
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from uvicorn.logging import AccessFormatter

from desk.api import create_app
from desk.auth import Auth
from desk.db import make_engine
from desk.log_setup import AccessMask, configure, mask_ip
from desk.runner import make_fake_runner
from desk.settings import Settings
from tests.test_api import PASSWORD

CLIENT_IP = "203.0.113.57"
OWNER = "owner@desk.test"
ACCESS_FORMAT = '%s - "%s %s HTTP/%s" %d'
NGINX_TEMPLATE = Path(__file__).resolve().parents[2] / "frontend" / "nginx.conf.template"


def test_mask_ip_hides_the_host_part():
    assert mask_ip("192.168.0.57") == "192.168.0.x"
    assert mask_ip(" 203.0.113.9 ") == "203.0.113.x"
    assert mask_ip("::ffff:203.0.113.9") == "203.0.113.x"
    assert mask_ip("2001:0db8:00a1:1234::5") == "2001:db8:a1::x"
    assert mask_ip("2002:cb00:7139::1") == "203.0.113.x"          # 6to4 carries the whole IPv4
    assert mask_ip("fe80::1%eth0") == "fe80:0:0::x"                # a zone, no exception
    for junk in ("testclient", "unknown", "", None, "1.2.3", 12345, "%"):
        assert mask_ip(junk) == "?"


def test_uvicorn_access_line_is_masked():
    configure("INFO")
    assert any(isinstance(f, AccessMask) for f in logging.getLogger("uvicorn.access").filters)
    formatter = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False)
    for addr, shown in ((f"{CLIENT_IP}:50000", "203.0.113.x"), ("2001:db8:1:2::9:443", "2001:db8:1::x"),
                        ("", "?")):
        target = f"/api/{OWNER}/{CLIENT_IP}?next={OWNER}&peer={CLIENT_IP}"
        record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                                   ACCESS_FORMAT, (addr, "POST", target, "1.1", 401), None)
        assert AccessMask().filter(record)
        assert formatter.format(record) == f'{shown} - "POST - HTTP/1.1" 401 Unauthorized'
        assert OWNER not in record.getMessage() and CLIENT_IP not in record.getMessage()


def test_login_lines_carry_neither_the_email_nor_the_address(db_url, caplog):
    app = create_app(Settings(database_url=db_url, fake_runs=True, fake_delay=0), runner=make_fake_runner(0))
    engine = make_engine(db_url)
    Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    with caplog.at_level(logging.INFO, logger="desk"), \
            TestClient(app, base_url="https://testserver", client=(CLIENT_IP, 50000)) as client:
        bad = client.post("/api/auth/login", json={"username": OWNER, "password": "nope-nope-nope"})
        ok = client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
    assert (bad.status_code, ok.status_code) == (401, 204)
    lines = [r.getMessage() for r in caplog.records if r.name.startswith("desk")]
    assert "login failed ip=203.0.113.x" in lines
    assert any(re.fullmatch(r"login ok user=#\d+ ip=203\.0\.113\.x", line) for line in lines), lines
    for line in lines:
        assert OWNER not in line and CLIENT_IP not in line, line


def test_access_mask_drops_unsupported_records_without_formatting_them():
    for msg, args in (
        (ACCESS_FORMAT, (None, "GET")),
        (ACCESS_FORMAT, (OWNER,)),
        (ACCESS_FORMAT, ()),
        (ACCESS_FORMAT, ({"raw": OWNER},)),
        (ACCESS_FORMAT, None),
        (ACCESS_FORMAT, (f"{CLIENT_IP}:50000", "POST", OWNER, "1.1", OWNER)),
        (OWNER, (f"{CLIENT_IP}:50000", "POST", OWNER, "1.1", 401)),
    ):
        record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, msg, args, None)
        assert not AccessMask().filter(record)


def test_web_container_request_logs_keep_only_safe_fields():
    template = NGINX_TEMPLATE.read_text(encoding="utf-8")
    http, server = template.split("\nserver {", maxsplit=1)
    masked_format = re.search(r"(?m)^log_format desk_masked [^;]+;", http)
    assert masked_format is not None
    assert set(re.findall(r"\$\w+", masked_format.group())) == {
        "$desk_masked_addr", "$time_local", "$request_method", "$server_protocol", "$status",
        "$body_bytes_sent", "$request_time", "$upstream_status", "$upstream_response_time",
    }
    assert re.search(r"(?m)^    access_log /var/log/nginx/access\.log desk_masked;$", server)
    assert re.search(r"(?m)^    error_log /dev/null;$", server)
    assert not re.search(r"(?m)^\s*error_log\s", http)


API = Path(__file__).resolve().parents[1] / "desk" / "api.py"
LOG_METHODS = {"debug", "info", "warning", "error", "exception"}


def _log_args(tree: ast.AST):
    """Each ``log.<level>(...)`` call's arguments after the format string."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LOG_METHODS
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "log"):
            yield node.args[1:]


def _bare(arg: ast.AST) -> bool:
    """A login name or a client address not passed through mask_ip."""
    if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute) and arg.func.attr == "mask_ip":
        return False
    for node in ast.walk(arg):
        if isinstance(node, ast.Attribute) and node.attr == "username":
            return True
        if isinstance(node, ast.Name) and node.id == "ip":
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_client_ip":
            return True
    return False


def test_no_log_call_in_api_takes_a_login_name_or_a_raw_address():
    """The OTP lines are not driven by a test client here: read every log call instead (#712)."""
    calls = list(_log_args(ast.parse(API.read_text(encoding="utf-8"))))
    assert len(calls) > 10
    bare = [ast.unparse(a) for args in calls for a in args if _bare(a)]
    assert bare == []
