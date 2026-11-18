"""#628 the DESK_* keys the code reads, .env.example and the server compose agree (names only - no values
are read or written here; a secret-looking key's example value must stay empty)."""

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent

# read by the CLI as a one-off input (create-user without a prompt): never kept in a file
CLI_ONLY = {"DESK_NEW_PASSWORD"}
# compose interpolation only (ports, limits, the network) - the app itself never reads them
COMPOSE_ONLY = {"DESK_WEB_BIND", "DESK_WEB_PORT", "DESK_BACKEND_MEM_LIMIT", "DESK_WEB_MEM_LIMIT", "DESK_DOCKER_NETWORK"}
SECRET = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|CHAT_ID")


def _code_keys() -> set[str]:
    keys = set()
    for path in (BACKEND / "desk").rglob("*.py"):
        keys |= set(re.findall(r"[\"'](DESK_[A-Z0-9_]+)[\"']", path.read_text(encoding="utf-8")))
    return keys


def _example() -> dict[str, str]:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    return dict(re.findall(r"^(DESK_[A-Z0-9_]+)=(.*)$", text, re.MULTILINE))


def _compose_env() -> set[str]:
    text = (BACKEND / "docker-compose-backend.yml").read_text(encoding="utf-8")
    return set(re.findall(r"^\s+(DESK_[A-Z0-9_]+):", text, re.MULTILINE))


def _interpolated() -> set[str]:
    keys = set()
    for path in [*ROOT.glob("docker-compose*.yml"), *ROOT.glob("*/docker-compose*.yml")]:
        keys |= set(re.findall(r"\$\{(DESK_[A-Z0-9_]+)", path.read_text(encoding="utf-8")))
    return keys


def test_every_key_the_code_reads_is_in_the_example_and_the_server_compose():
    code = _code_keys() - CLI_ONLY
    assert code, "no DESK_* key found - the scan is broken"
    assert sorted(code - set(_example())) == [], ".env.example lacks these (add the name, no value)"
    assert sorted(code - _compose_env()) == [], "docker-compose-backend.yml environment lacks these"


def test_nothing_in_the_example_or_the_compose_is_left_over():
    code = _code_keys()
    assert sorted(set(_example()) - code - COMPOSE_ONLY) == [], "in .env.example but read by nothing"
    assert sorted(_compose_env() - code) == [], "in the server compose but read by nothing"
    assert COMPOSE_ONLY <= _interpolated() and COMPOSE_ONLY <= set(_example())


def test_a_cli_only_key_is_never_kept_in_a_file_and_secrets_have_no_example_value():
    assert not CLI_ONLY & (set(_example()) | _compose_env())
    filled = {k: v for k, v in _example().items() if SECRET.search(k) and v.strip()}
    assert filled == {}, "a secret-looking key carries a value in .env.example"
