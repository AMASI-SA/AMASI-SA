"""Iter-177 — Permanent Asia/Riyadh Timezone Guard.

This test file is the FORMAL enforcement of the merchant's
governance rule (adopted Feb 2026):

    "جميع العمليات والتقارير والفلاتر والحسابات يجب أن تعتمد
     توقيت السعودية (Asia/Riyadh) فقط. يُمنع استخدام UTC
     مباشرة في منطق التقارير أو الفلاتر أو احتساب اليوم
     والشهر والسنة، ويقتصر استخدام UTC على التخزين الداخلي
     فقط."

How the guard works
===================
We walk the entire backend and the frontend source tree and look
for forbidden patterns that bypass `tz_utils`. The patterns are
the ones that historically caused "off by one day" bugs:

Backend
-------
* ``datetime.utcnow()``  — silent UTC clock; never appropriate
  for daily aggregations or display.
* ``datetime.now()``     — no tz argument → naive local-clock
  on the server (UTC in deployment). Always wrong for date math.
* ``date.today()``       — UTC date on the deployment server.
* ``datetime.today()``   — same as above.

Allowed exceptions
~~~~~~~~~~~~~~~~~~
* ``datetime.now(timezone.utc)`` — explicit UTC instant, used
  for STORAGE (audit logs, created_at). This is canonical.
* ``datetime.now(tz=...)`` / ``datetime.now(SOME_TZ)`` — any
  explicit timezone argument is acceptable.
* ``tz_utils.py`` itself is exempt (it defines the wrappers).
* The ``tests/`` directory is exempt (tests construct fake
  instants and that's fine).

Frontend
--------
* ``new Date().toISOString().slice(0, 10)`` — UTC YYYY-MM-DD;
  rolls back to "yesterday" between 21:00–24:00 UTC.
* ``new Date().toISOString().slice(0, 7)``  — UTC YYYY-MM.

Allowed exceptions
~~~~~~~~~~~~~~~~~~
* The helper modules ``lib/dates.js`` and ``lib/tzUtils.js``
  define the wrappers and may use the UTC pattern internally.
* Tests are exempt.

Adding new exceptions
=====================
If a NEW file genuinely needs UTC storage (e.g. audit logs),
add it to ``ALLOWED_BACKEND_FILES`` / ``ALLOWED_FRONTEND_FILES``
with a comment explaining why. Do NOT add a generic skip — every
exemption must be justified.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


BACKEND_ROOT = Path("/app/backend")
FRONTEND_ROOT = Path("/app/frontend/src")

# ── Patterns that bypass tz_utils ──────────────────────────────
BACKEND_FORBIDDEN: list[tuple[str, re.Pattern[str]]] = [
    ("datetime.utcnow()",       re.compile(r"\bdatetime\.utcnow\s*\(")),
    # datetime.now WITHOUT arg (negative lookahead matches anything
    # other than ``)``) — only flag when called with no arguments.
    ("datetime.now() (no tz)",  re.compile(r"\bdatetime\.now\s*\(\s*\)")),
    ("date.today()",            re.compile(r"\bdate\.today\s*\(")),
    ("datetime.today()",        re.compile(r"\bdatetime\.today\s*\(")),
]

FRONTEND_FORBIDDEN: list[tuple[str, re.Pattern[str]]] = [
    ("new Date().toISOString().slice(0, 10)",
     re.compile(r"new\s+Date\s*\(\s*\)\s*\.\s*toISOString\s*\(\s*\)\s*\.\s*slice\s*\(\s*0\s*,\s*10")),
    ("new Date().toISOString().slice(0, 7)",
     re.compile(r"new\s+Date\s*\(\s*\)\s*\.\s*toISOString\s*\(\s*\)\s*\.\s*slice\s*\(\s*0\s*,\s*7")),
]

# ── Files exempt because they implement the wrappers themselves
# or because the directory is a test / fixture / generated area.
ALLOWED_BACKEND_PATHS = {
    BACKEND_ROOT / "tz_utils.py",  # defines the helpers
}
ALLOWED_BACKEND_DIRS = {
    BACKEND_ROOT / "tests",
    BACKEND_ROOT / "__pycache__",
    BACKEND_ROOT / ".venv",
}

# Files we know are pre-existing and have been audited as
# CORRECT (e.g. they already pass an explicit tz to datetime.now).
# Anything new MUST migrate to tz_utils.
ALLOWED_FRONTEND_PATHS = {
    FRONTEND_ROOT / "lib" / "dates.js",
    FRONTEND_ROOT / "lib" / "tzUtils.js",
    FRONTEND_ROOT / "lib" / "format.js",  # has its own internal +3h shift
}
ALLOWED_FRONTEND_DIRS: set[Path] = set()


# ── Helpers ────────────────────────────────────────────────────
def _iter_python_files(root: Path):
    for p in root.rglob("*.py"):
        if any(part in {"__pycache__", ".venv", "node_modules"}
               for part in p.parts):
            continue
        if p in ALLOWED_BACKEND_PATHS:
            continue
        if any(p.is_relative_to(d) for d in ALLOWED_BACKEND_DIRS):
            continue
        yield p


def _iter_frontend_files(root: Path):
    if not root.exists():
        return
    for ext in (".js", ".jsx", ".ts", ".tsx"):
        for p in root.rglob(f"*{ext}"):
            if any(part in {"node_modules", "build", "dist"}
                   for part in p.parts):
                continue
            if p in ALLOWED_FRONTEND_PATHS:
                continue
            if any(p.is_relative_to(d) for d in ALLOWED_FRONTEND_DIRS):
                continue
            yield p


def _scan(path: Path, patterns) -> list[tuple[str, int, str]]:
    """Return list of (pattern_label, line_number, line_text)
    matches found in ``path``."""
    hits: list[tuple[str, int, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return hits
    for i, line in enumerate(text.splitlines(), start=1):
        # Skip comments and docstrings cheaply: lines whose first
        # non-whitespace is `#` (Python) or `*` / `//` (JS) usually
        # describe the bug rather than reintroduce it.
        stripped = line.lstrip()
        if stripped.startswith(("#", "//", "*")):
            continue
        for label, regex in patterns:
            if regex.search(line):
                hits.append((label, i, line.strip()))
    return hits


# ── Backend guard ──────────────────────────────────────────────
def test_backend_uses_tz_utils_everywhere():
    offenders: list[str] = []
    for py in _iter_python_files(BACKEND_ROOT):
        hits = _scan(py, BACKEND_FORBIDDEN)
        for label, lineno, line in hits:
            offenders.append(f"{py.relative_to(BACKEND_ROOT)}:{lineno}: {label}\n    {line}")
    if offenders:
        msg = (
            "\n\nThe following BACKEND files bypass tz_utils. Replace "
            "with tz_utils.riyadh_now_aware() / riyadh_today() / "
            "riyadh_today_iso() / riyadh_date_from_utc(...) — or, for "
            "storage timestamps, use datetime.now(timezone.utc).\n\n"
            + "\n".join(offenders)
        )
        pytest.fail(msg)


# ── Frontend guard ─────────────────────────────────────────────
def test_frontend_uses_tz_utils_everywhere():
    offenders: list[str] = []
    for f in _iter_frontend_files(FRONTEND_ROOT):
        hits = _scan(f, FRONTEND_FORBIDDEN)
        for label, lineno, line in hits:
            offenders.append(f"{f.relative_to(FRONTEND_ROOT)}:{lineno}: {label}\n    {line}")
    if offenders:
        msg = (
            "\n\nThe following FRONTEND files use UTC date strings "
            "instead of the Riyadh helpers. Replace with todaySA() / "
            "monthStartSA() / monthISO_SA() / yearStartSA() from "
            "lib/dates.js — these always reflect the Saudi calendar "
            "regardless of browser timezone.\n\n"
            + "\n".join(offenders)
        )
        pytest.fail(msg)


# ── Sanity test: the guard itself works ────────────────────────
def test_guard_detects_known_bad_patterns():
    """If someone refactors the patterns above and accidentally
    breaks them, this test ensures we catch a synthetic bad line."""
    sample = "x = datetime.utcnow()\n"
    for _, regex in BACKEND_FORBIDDEN[:1]:
        assert regex.search(sample), \
            "Forbidden-pattern regex no longer matches `datetime.utcnow()`"
    sample_js = "const d = new Date().toISOString().slice(0, 10);"
    for _, regex in FRONTEND_FORBIDDEN[:1]:
        assert regex.search(sample_js), \
            "Forbidden-pattern regex no longer matches the UTC date slice."


def test_tz_utils_helpers_are_importable():
    """If the helper module breaks, the entire codebase loses its
    Riyadh authority. Catch import-time errors here."""
    from tz_utils import (  # noqa: F401
        DEFAULT_TIMEZONE,
        RIYADH_TZ,
        riyadh_date_from_utc,
        riyadh_end_of_day_utc,
        riyadh_end_of_month_utc,
        riyadh_end_of_year_utc,
        riyadh_last_n_days_range_utc,
        riyadh_now_aware,
        riyadh_start_of_day_utc,
        riyadh_start_of_month_utc,
        riyadh_start_of_year_utc,
        riyadh_this_month_range_utc,
        riyadh_this_year_range_utc,
        riyadh_today,
        riyadh_today_iso,
        riyadh_today_range_utc,
        riyadh_yesterday_range_utc,
        utc_to_riyadh,
        utc_to_riyadh_iso,
    )
    assert str(RIYADH_TZ) in ("Asia/Riyadh", "UTC+03:00")
    assert DEFAULT_TIMEZONE == "Asia/Riyadh"
