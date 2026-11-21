"""#688 DESK_ROLE: which process runs which background job (desk.roles)."""

import re
from pathlib import Path

import pytest

from desk import api, roles
from desk.settings import Settings


def test_role_defaults_to_all_and_a_typo_stops_the_start(monkeypatch):
    assert roles.parse(None) == roles.parse("") == "all"
    assert roles.parse(" Collector ") == "collector"
    with pytest.raises(ValueError, match="DESK_ROLE"):
        roles.parse("colector")
    monkeypatch.setenv("DESK_ROLE", "analyst")
    assert Settings().role == "analyst"
    monkeypatch.delenv("DESK_ROLE")
    assert Settings().role == "all"
    assert Settings(role="API").role == "api"
    with pytest.raises(ValueError, match="DESK_ROLE"):
        Settings(role="worker")


def test_every_job_has_one_real_role_and_a_part_runs_with_its_parent():
    names = [name for name, *_ in roles.JOBS]
    assert len(names) == len(set(names))
    role_of = {name: role for name, _label, _source, _parent, role in roles.JOBS}
    for name, _label, _source, parent, role in roles.JOBS:
        assert role in ("api", "collector", "analyst"), name
        if parent is not None:
            assert role_of[parent] == role, name


def test_all_runs_every_job_and_each_split_role_only_its_own():
    names = [name for name, *_ in roles.JOBS]
    assert all(roles.runs("all", n) for n in names)
    for role in ("api", "collector", "analyst"):
        mine = {n for n in names if roles.runs(role, n)}
        assert mine == {n for n, *_, r in roles.JOBS if r == role}
    assert roles.runs("collector", "listing") and not roles.runs("api", "listing")   # the broker listing: once
    with pytest.raises(KeyError):
        roles.runs("all", "not_listed")             # a new job must get its line in roles.JOBS


def test_every_job_name_the_app_uses_has_its_line():
    # read from the source: a job behind a switch that is off in tests (or a part that only runs inside a live
    # pass) would otherwise reach production without its line and fail there
    src = (Path(api.__file__)).read_text(encoding="utf-8")
    used = set(re.findall(r'\b(?:start|here|jobs\.(?:spawn|part|get|mark_error))\(\s*"([a-z0-9_]+)"', src))
    assert {"listing", "prewarm", "kis_stream", "macro_news"} <= used        # the pattern still finds them
    assert used <= {name for name, *_ in roles.JOBS}


def test_a_job_switched_off_without_its_line_stops_the_start(db_url, monkeypatch):
    from tests.test_api import _client

    monkeypatch.delitem(roles._ROLE_OF, "scoring")     # scoring is off with scripted runs
    with pytest.raises(KeyError, match="desk.roles.JOBS"), _client(db_url):
        pass


@pytest.mark.parametrize(("role", "listing"), [("all", True), ("api", False), ("collector", True), ("analyst", False)])
def test_status_names_the_role_and_a_job_of_another_role_stays_off(db_url, role, listing):
    from desk.db import migrate
    from tests.test_api import _client

    if role in ("collector", "analyst"):
        migrate(db_url)  # the API has already prepared the shared database
    with _client(db_url, role=role) as client:
        body = client.get("/api/status").json()
    assert body["role"] == role
    state = {j["name"]: j["state"] for j in body["jobs"]}["listing"]
    assert (state != "off") is listing
