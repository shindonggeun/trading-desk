"""#497 (optional): one or two explainer pictures for a beginner report (#495), drawn by the OpenAI image API.

A ChatGPT plan (the desk's sign-in) cannot call the image API, so this needs an API key of its own:
``OPENAI_API_KEY``. Without it nothing here runs and the reports stay as they are (no pictures).

- What is drawn comes from the report the desk already wrote, and only on sections without a small price
  chart (the page shows a picture in the chart's place): the term box of such a section (the idea behind
  it, no numbers), else the stages (a staircase of the price ranges). Every number or label in a picture
  is one the report holds; the model is told to write no other text or number.
- Cost caps: at most ``PER_REPORT`` pictures a report, ``per_day`` a day and ``per_month`` a month
  (``DESK_REPORT_IMAGES_PER_DAY`` / ``_PER_MONTH``), counted in a small ledger on the backend's volume
  before each call (a timeout may still be billed); a ledger that cannot be read or written stops the
  pictures. A cap reached, a failure or a timeout: the report simply has fewer pictures.
- The key is only ever sent in the request's Authorization header. It is never logged, never part of an
  error, a response or this object's repr; a failed call logs the HTTP status and the error body's
  ``code`` / ``type`` / ``param`` only, each cut to a short identifier (never the message - an OpenAI error
  message can quote part of the key - and never the prompt).
- A 4xx answer (other than ``moderation_blocked``, which is about that one prompt) ends the report's
  drawing: the next picture would be refused the same way and still use the caps.

Pictures are PNG files under ``CACHE_DIR/<report id>/<n>.png`` (n = 1, 2), served by
``GET /api/reports/{id}/images/{n}`` (desk.api).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from desk import explain

log = logging.getLogger(__name__)

URL = "https://api.openai.com/v1/images/generations"
CACHE_DIR = Path.home() / ".tradingagents" / "desk-cache" / "report-images"   # the backend's volume
PER_REPORT = 2
TIMEOUT_SEC = 120          # one picture
DEADLINE_SEC = 180         # all of a report's pictures (shutdown and the page's wait stay bounded)
MIN_CALL_SEC = 20          # less time left than this: no new picture
MAX_BYTES = 8 * 1024 * 1024          # a picture larger than this is not kept
REPORT_ID = re.compile(r"rp_[0-9a-f]{20}")          # always used with fullmatch
PNG = b"\x89PNG\r\n\x1a\n"
# gpt-image-1 was shut down on 2026-10-23 (OpenAI API reference, images/create: "Use gpt-image-2.5-sunburst or
# gpt-image-2.5-flare instead") and answered every picture with HTTP 400 (#497). The size and quality below
# are ones every GPT image model takes.
DEFAULT_MODEL = "gpt-image-2.5-flare"
MODEL_NAME = re.compile(r"[A-Za-z0-9_.\-]{1,64}")     # always used with fullmatch
RETIRED_MODELS = frozenset({"gpt-image-1", "dall-e-2", "dall-e-3"})
DEFAULT_SIZE = "1536x1024"
DEFAULT_QUALITY = "medium"

# the desk's own look (never another blog's character, style or wording)
STYLE = ("Flat vector infographic for a beginner investor, clean and friendly. Palette: deep navy background, "
         "teal and amber accents, white text. A small round teal robot mascot with a desk-lamp head may point at "
         "the key part. Large shapes, generous spacing, no photo realism, no logos, no brand names.")
ERROR_FIELD = re.compile(r"[A-Za-z0-9_.\-\[\]]{1,64}")     # always used with fullmatch


def _error_fields(r: httpx.Response) -> dict[str, str]:
    """``code`` / ``type`` / ``param`` of an OpenAI error body, each kept only when it is a short identifier
    ("-" when missing, "?" when it is anything else) - never the message, which can quote the key."""
    try:
        err = r.json().get("error")
    except (ValueError, AttributeError):
        err = None
    err = err if isinstance(err, dict) else {}
    out = {}
    for name in ("code", "type", "param"):
        v = err.get(name)
        ok = isinstance(v, str) and ERROR_FIELD.fullmatch(v) and not v.lower().startswith("sk-")
        out[name] = "-" if v is None else v if ok else "?"
    return out


NO_OTHER_TEXT = ("Write ONLY the labels and numbers listed here, exactly as given (Korean text as given); "
                 "write no other words, numbers, dates or prices. If a label does not fit, leave it out.")


def image_path(report_id: str, n: int, root: Path | None = None) -> Path | None:
    """The file of picture ``n`` of a report, or ``None`` for an id or number that is not one of ours (the
    whole id is checked against the report id shape, so a path never leaves ``root``)."""
    if not REPORT_ID.fullmatch(report_id or "") or n not in range(1, PER_REPORT + 1):
        return None
    return Path(root or CACHE_DIR) / report_id / f"{n}.png"


class Ledger:
    """Pictures asked for per day and per month (UTC: a day starts 09:00 KST), in a small JSON file -
    survives restarts. A file that is there but cannot be read counts as full (the caps fail closed);
    only a missing file starts from zero."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()

    def _read(self) -> dict | None:
        if not self.path.exists():
            return {"days": {}, "months": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            days, months = raw.get("days", {}), raw.get("months", {})
            if not isinstance(days, dict) or not isinstance(months, dict):
                raise TypeError("bad shape")
            return {"days": {str(k): int(v) for k, v in days.items()},
                    "months": {str(k): int(v) for k, v in months.items()}}
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def counts(self, now: datetime) -> tuple[int, int] | None:
        """(this day, this month), or ``None`` when the ledger cannot be read."""
        raw = self._read()
        if raw is None:
            return None
        return raw["days"].get(now.strftime("%Y-%m-%d"), 0), raw["months"].get(now.strftime("%Y-%m"), 0)

    def add(self, now: datetime, n: int = 1) -> None:
        """Count ``n`` more; raises (OSError / ValueError) when it cannot - the caller then stops drawing."""
        with self.lock:
            raw = self._read()
            if raw is None:
                raise ValueError("the image ledger cannot be read")
            day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
            days = {k: v for k, v in raw["days"].items() if k[:7] == month}     # keep this month only
            months = dict(sorted(raw["months"].items())[-12:])
            days[day] = days.get(day, 0) + n
            months[month] = months.get(month, 0) + n
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"days": days, "months": months}), encoding="utf-8")
            tmp.replace(self.path)


def _p(x: float | None) -> str:
    return "" if x is None else explain._p(x)     # the report text's own number form


def _free(section: dict) -> bool:
    """A section without a small price chart: the page shows a picture in the chart's place (#496
    ExplainArticle), so a picture only goes where it hides no chart."""
    return (section.get("chart") or {}).get("kind", "none") == "none"


def plan(report: dict) -> list[dict]:
    """Up to ``PER_REPORT`` pictures for a report: ``{"section": i, "prompt": ..., "alt": ...}``, only on
    sections without a chart. Each such section's term box first (the idea behind it, no numbers), then the
    stages staircase (two stages or more) on a free section left. Every label in a prompt is the report's."""
    sections = report.get("sections") or []
    free = [i for i, s in enumerate(sections) if _free(s)]
    out: list[dict] = []
    for i in free:
        term = sections[i].get("term") or {}
        if term.get("name") and term.get("explain"):
            out.append({
                "section": i,
                "prompt": (f"{STYLE}\nA simple concept picture that explains the market term "
                           f"\"{term['name']}\" to a beginner: {term['explain']} "
                           f"Show the idea with shapes and arrows on a plain price line, no axis numbers. "
                           f"Title: \"{term['name']}\".\n{NO_OTHER_TEXT}"),
                "alt": f"{term['name']}: {term['explain']}",
            })
    stages = [s for s in report.get("stages") or [] if s.get("label")][:3]
    spare = next((i for i in free if all(o["section"] != i for o in out)), None)
    if len(stages) >= 2 and spare is not None:
        steps = [f'"{s["label"]}": {_p(s.get("low"))} ~ {_p(s.get("high"))}'
                 if s.get("high") is not None and s.get("low") is not None
                 else f'"{s["label"]}": {_p(s.get("low") if s.get("low") is not None else s.get("high"))}'
                 for s in stages]
        name = (report.get("subject") or {}).get("name") or ""
        out.append({
            "section": spare,
            "prompt": (f"{STYLE}\nA descending staircase of {len(steps)} steps, one step per buying stage, "
                       f"each step a price range; the mascot stands on the first step. "
                       f"Title: \"{name} 단계별 대응\". Step labels: " + "; ".join(steps) + f".\n{NO_OTHER_TEXT}"),
            "alt": f"{name} 단계별 대응 범위: " + ", ".join(s.replace('"', "") for s in steps),
        })
    return out[:PER_REPORT]


class ReportImages:
    """Draws a report's pictures within the caps and a time limit. Never raises: a failure means fewer
    pictures. ``stop`` (set on shutdown) ends a report's drawing before its next picture."""

    def __init__(self, key: str | None, root: Path | None = None, per_day: int = 4, per_month: int = 20,
                 model: str = DEFAULT_MODEL, size: str = DEFAULT_SIZE, quality: str = DEFAULT_QUALITY,
                 transport: httpx.BaseTransport | None = None, deadline_sec: float = DEADLINE_SEC) -> None:
        key = (key or "").strip()
        if key and not key.isascii():           # a pasted key with a stray character: off, said once
            log.warning("report images: OPENAI_API_KEY has non-ASCII characters - pictures are off")
            key = ""
        self.__key = key
        self.root, self.per_day, self.per_month = Path(root or CACHE_DIR), per_day, per_month
        model = (model or "").strip() or DEFAULT_MODEL
        if not MODEL_NAME.fullmatch(model):      # not a model name (a pasted key?): never logged as it is
            log.warning("report images: DESK_REPORT_IMAGE_MODEL is not a model name - using %s", DEFAULT_MODEL)
            model = DEFAULT_MODEL
        if model in RETIRED_MODELS:             # shut down: every call would be a 400 that still uses the caps
            log.warning("report images: model %r is shut down - using %s", model, DEFAULT_MODEL)
            model = DEFAULT_MODEL
        self.model, self.size, self.quality = model, size, quality
        self.transport, self.deadline_sec = transport, deadline_sec
        self.ledger = Ledger(self.root / "usage.json")
        self.stop = threading.Event()

    def __repr__(self) -> str:                 # never the key
        return f"ReportImages(enabled={self.enabled}, per_day={self.per_day}, per_month={self.per_month})"

    @property
    def enabled(self) -> bool:
        return bool(self.__key) and self.per_day > 0 and self.per_month > 0

    def room(self, now: datetime | None = None) -> int:
        """How many pictures the caps still allow right now (0 when off, or when the ledger is unreadable)."""
        if not self.enabled:
            return 0
        got = self.ledger.counts(now or datetime.now(UTC))
        if got is None:
            log.warning("report images: the usage ledger cannot be read - no pictures until it is fixed")
            return 0
        day, month = got
        return max(0, min(PER_REPORT, self.per_day - day, self.per_month - month))

    def _draw(self, prompt: str, timeout: float) -> tuple[bytes | None, bool]:
        """(the PNG or ``None``, whether another picture is worth asking for)."""
        body = {"model": self.model, "prompt": prompt, "n": 1, "size": self.size, "quality": self.quality}
        try:
            headers = {"Authorization": f"Bearer {self.__key}", "Content-Type": "application/json"}
            with httpx.Client(transport=self.transport, timeout=httpx.Timeout(timeout, connect=10.0),
                              follow_redirects=False) as client:
                r = client.post(URL, headers=headers, json=body)
        except (httpx.HTTPError, UnicodeError) as exc:
            log.warning("report image: request failed (%s)", type(exc).__name__)
            return None, True
        if r.status_code != 200:
            err = _error_fields(r)                  # never the message: it can quote the key
            log.warning("report image: HTTP %d (code=%s type=%s param=%s, model %s)",
                        r.status_code, err["code"], err["type"], err["param"], self.model)
            return None, not (400 <= r.status_code < 500 and err["code"] != "moderation_blocked")
        try:
            data = base64.b64decode(r.json()["data"][0]["b64_json"], validate=True)
        except (ValueError, KeyError, IndexError, TypeError):
            log.warning("report image: unexpected response shape")
            return None, True
        if not data.startswith(PNG) or len(data) > MAX_BYTES:
            log.warning("report image: not a PNG or too large (%d bytes)", len(data))
            return None, True
        return data, True

    def draw(self, report: dict) -> dict[int, dict]:
        """The pictures of a stored report (it carries its ``id``): ``{section index: {"url", "alt"}}``.
        Each picture is counted before it is asked for - a timeout or an odd answer may still be billed."""
        rid = report.get("id") or ""
        if not self.enabled or not REPORT_ID.fullmatch(rid):
            return {}
        started = time.monotonic()
        out: dict[int, dict] = {}
        for pic in plan(report):
            left = self.deadline_sec - (time.monotonic() - started)
            if self.stop.is_set() or left < MIN_CALL_SEC or self.room() <= 0:
                break
            try:
                self.ledger.add(datetime.now(UTC))
            except (OSError, ValueError) as exc:
                log.warning("report images: the usage ledger cannot be written (%s) - no pictures",
                            type(exc).__name__)
                break
            data, go_on = self._draw(pic["prompt"], min(TIMEOUT_SEC, left))
            if data is None:
                if go_on:
                    continue
                break
            n = len(out) + 1
            path = image_path(rid, n, self.root)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            except OSError as exc:
                log.warning("report image: not saved (%s)", type(exc).__name__)
                continue
            out[pic["section"]] = {"url": f"/api/reports/{rid}/images/{n}", "alt": pic["alt"][:300]}
        if out:
            log.info("report %s: %d picture(s)", rid, len(out))
        return out


def from_env(root: Path | None = None) -> ReportImages:
    """The image maker as the environment sets it (off without ``OPENAI_API_KEY``)."""
    def num(name: str, default: int) -> int:
        try:
            return int(os.getenv(name) or default)
        except ValueError:
            return default

    key = os.getenv("OPENAI_API_KEY")
    provider = (os.getenv("TRADINGAGENTS_LLM_PROVIDER") or "openai").strip().lower()
    if key and provider != "chatgpt":           # the analyses would bill this key too (the library's default)
        log.warning("OPENAI_API_KEY is set and the LLM provider is %r: the analyses use this key as well", provider)
    return ReportImages(key, root,
                        per_day=num("DESK_REPORT_IMAGES_PER_DAY", 4), per_month=num("DESK_REPORT_IMAGES_PER_MONTH", 20),
                        model=os.getenv("DESK_REPORT_IMAGE_MODEL") or DEFAULT_MODEL)
