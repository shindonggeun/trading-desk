"""#497 (optional): one or two explainer pictures for a beginner report (#495), drawn by the OpenAI image API.

A ChatGPT plan (the desk's sign-in) cannot call the image API, so this needs an API key of its own:
``OPENAI_API_KEY``. Without it nothing here runs and the reports stay as they are (no pictures).

- What is drawn comes from the report the desk already wrote: the stages (a staircase of the price ranges)
  and one term box (the idea behind it, no numbers). Every number or label in a picture is one the report
  holds; the model is told to write no other text or number.
- Cost caps: at most ``PER_REPORT`` pictures a report, ``per_day`` a day and ``per_month`` a month
  (``DESK_REPORT_IMAGES_PER_DAY`` / ``_PER_MONTH``), counted in a small ledger on the backend's volume.
  A cap reached, a failure or a timeout: the report simply has fewer pictures.
- The key is only ever sent in the request's Authorization header. It is never logged, never part of an
  error, a response or this object's repr; a failed call logs the HTTP status (never the body - an
  OpenAI error body can quote part of the key).

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
from datetime import UTC, datetime
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

URL = "https://api.openai.com/v1/images/generations"
CACHE_DIR = Path.home() / ".tradingagents" / "desk-cache" / "report-images"   # the backend's volume
PER_REPORT = 2
TIMEOUT_SEC = 120
MAX_BYTES = 8 * 1024 * 1024          # a picture larger than this is not kept
REPORT_ID = re.compile(r"^rp_[0-9a-f]{20}$")
PNG = b"\x89PNG\r\n\x1a\n"
DEFAULT_MODEL = "gpt-image-1"
DEFAULT_SIZE = "1536x1024"
DEFAULT_QUALITY = "medium"

# the desk's own look (never another blog's character, style or wording)
STYLE = ("Flat vector infographic for a beginner investor, clean and friendly. Palette: deep navy background, "
         "teal and amber accents, white text. A small round teal robot mascot with a desk-lamp head may point at "
         "the key part. Large shapes, generous spacing, no photo realism, no logos, no brand names.")
NO_OTHER_TEXT = ("Write ONLY the labels and numbers listed here, exactly as given (Korean text as given); "
                 "write no other words, numbers, dates or prices. If a label does not fit, leave it out.")


def image_path(report_id: str, n: int, root: Path | None = None) -> Path | None:
    """The file of picture ``n`` of a report, or ``None`` for an id or number that is not one of ours (the
    id is checked against the report id shape, so a path never leaves ``root``)."""
    if not REPORT_ID.match(report_id or "") or n not in range(1, PER_REPORT + 1):
        return None
    return Path(root or CACHE_DIR) / report_id / f"{n}.png"


class Ledger:
    """Pictures made per day and per month (UTC), in a small JSON file - survives restarts."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()

    def _read(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (OSError, ValueError):
            return {}

    def counts(self, now: datetime) -> tuple[int, int]:
        raw = self._read()
        return (int(raw.get("days", {}).get(now.strftime("%Y-%m-%d"), 0)),
                int(raw.get("months", {}).get(now.strftime("%Y-%m"), 0)))

    def add(self, now: datetime, n: int = 1) -> None:
        with self.lock:
            raw = self._read()
            day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
            days = {k: v for k, v in raw.get("days", {}).items() if k[:7] == month}   # keep this month only
            months = dict(sorted(raw.get("months", {}).items())[-12:])
            days[day] = int(days.get(day, 0)) + n
            months[month] = int(months.get(month, 0)) + n
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"days": days, "months": months}), encoding="utf-8")
            tmp.replace(self.path)


def _p(x: float | None) -> str:
    if x is None:
        return ""
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:,.2f}"


def plan(report: dict) -> list[dict]:
    """Up to ``PER_REPORT`` pictures for a report: ``{"section": i, "prompt": ..., "alt": ...}``. The stages
    staircase (on the section whose chart shows the stage labels, else the last section with a chart) and the
    first term box (on its own section). Every label in a prompt is taken from the report itself."""
    sections = report.get("sections") or []
    out: list[dict] = []
    stages = [s for s in report.get("stages") or [] if s.get("label")][:3]
    if stages and sections:
        names = {s["label"] for s in stages}
        at = next((i for i, s in enumerate(sections) if names & set((s.get("chart") or {}).get("labels") or [])),
                  None)
        if at is None:
            at = max((i for i, s in enumerate(sections) if (s.get("chart") or {}).get("kind") not in (None, "none")),
                     default=len(sections) - 1)
        steps = [f'"{s["label"]}": {_p(s.get("low"))} ~ {_p(s.get("high"))}' if s.get("high") is not None
                 and s.get("low") is not None else f'"{s["label"]}": {_p(s.get("low") or s.get("high"))}'
                 for s in stages]
        name = (report.get("subject") or {}).get("name") or ""
        out.append({
            "section": at,
            "prompt": (f"{STYLE}\nA descending staircase of {len(steps)} steps, one step per buying stage, "
                       f"each step a price range; the mascot stands on the first step. "
                       f"Title: \"{name} 단계별 대응\". Step labels: " + "; ".join(steps) + f".\n{NO_OTHER_TEXT}"),
            "alt": f"{name} 단계별 대응 범위: " + ", ".join(s.replace('"', "") for s in steps),
        })
    for i, s in enumerate(sections):
        term = s.get("term") or {}
        if len(out) >= PER_REPORT:
            break
        if term.get("name") and term.get("explain") and all(o["section"] != i for o in out):
            out.append({
                "section": i,
                "prompt": (f"{STYLE}\nA simple concept picture that explains the market term "
                           f"\"{term['name']}\" to a beginner: {term['explain']} "
                           f"Show the idea with shapes and arrows on a plain price line, no axis numbers. "
                           f"Title: \"{term['name']}\".\n{NO_OTHER_TEXT}"),
                "alt": f"{term['name']}: {term['explain']}",
            })
    return out[:PER_REPORT]


class ReportImages:
    """Draws a report's pictures within the caps. Never raises: a failure means fewer pictures."""

    def __init__(self, key: str | None, root: Path | None = None, per_day: int = 4, per_month: int = 20,
                 model: str = DEFAULT_MODEL, size: str = DEFAULT_SIZE, quality: str = DEFAULT_QUALITY,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.__key = (key or "").strip()
        self.root, self.per_day, self.per_month = Path(root or CACHE_DIR), per_day, per_month
        self.model, self.size, self.quality = model, size, quality
        self.transport = transport
        self.ledger = Ledger(self.root / "usage.json")

    def __repr__(self) -> str:                 # never the key
        return f"ReportImages(enabled={self.enabled}, per_day={self.per_day}, per_month={self.per_month})"

    @property
    def enabled(self) -> bool:
        return bool(self.__key) and self.per_day > 0 and self.per_month > 0

    def room(self, now: datetime | None = None) -> int:
        """How many pictures the caps still allow right now (0 when off)."""
        if not self.enabled:
            return 0
        day, month = self.ledger.counts(now or datetime.now(UTC))
        return max(0, min(PER_REPORT, self.per_day - day, self.per_month - month))

    def _draw(self, prompt: str) -> bytes | None:
        headers = {"Authorization": f"Bearer {self.__key}", "Content-Type": "application/json"}
        body = {"model": self.model, "prompt": prompt, "n": 1, "size": self.size, "quality": self.quality}
        try:
            with httpx.Client(transport=self.transport, timeout=TIMEOUT_SEC, follow_redirects=False) as client:
                r = client.post(URL, headers=headers, json=body)
        except httpx.HTTPError as exc:
            log.warning("report image: request failed (%s)", type(exc).__name__)
            return None
        if r.status_code != 200:
            log.warning("report image: HTTP %d", r.status_code)      # never the body: it can quote the key
            return None
        try:
            data = base64.b64decode(r.json()["data"][0]["b64_json"], validate=True)
        except (ValueError, KeyError, IndexError, TypeError):
            log.warning("report image: unexpected response shape")
            return None
        if not data.startswith(PNG) or len(data) > MAX_BYTES:
            log.warning("report image: not a PNG or too large (%d bytes)", len(data))
            return None
        return data

    def draw(self, report: dict) -> dict[int, dict]:
        """The pictures of a stored report (it carries its ``id``): ``{section index: {"url", "alt"}}``."""
        rid = report.get("id") or ""
        if not self.enabled or not REPORT_ID.match(rid):
            return {}
        out: dict[int, dict] = {}
        for pic in plan(report):
            if self.room() <= 0:
                break
            data = self._draw(pic["prompt"])
            if data is None:
                continue
            n = len(out) + 1
            path = image_path(rid, n, self.root)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            except OSError as exc:
                log.warning("report image: not saved (%s)", type(exc).__name__)
                continue
            self.ledger.add(datetime.now(UTC))
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

    return ReportImages(os.getenv("OPENAI_API_KEY"), root,
                        per_day=num("DESK_REPORT_IMAGES_PER_DAY", 4), per_month=num("DESK_REPORT_IMAGES_PER_MONTH", 20),
                        model=(os.getenv("DESK_REPORT_IMAGE_MODEL") or DEFAULT_MODEL).strip())
