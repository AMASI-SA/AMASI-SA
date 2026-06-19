"""Iter-246z — BNPL Timezone SSOT.

Single source of truth for every BNPL-related date/time computation:
  • Period from/to derivation ("last_week", "this_week", ...)
  • Invoice-issuance weekday guard (Tamara=Sat, Tabby=Mon)
  • Duplicate-period check
  • Default `settlement_date` displayed in the modal
  • Health/diagnostic endpoints

Saudi Arabia uses Asia/Riyadh (AST, UTC+3) year-round with NO DST,
so the offset is constant.  Using `ZoneInfo` rather than a manual
+3-hour shift makes the intent explicit and survives any future
host-timezone misconfiguration.

NEVER use `datetime.utcnow()` for BNPL logic — it is timezone-naive
and silently slips by a calendar day between 21:00 and 24:00 UTC
(00:00-03:00 Asia/Riyadh).
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

BNPL_TZ: ZoneInfo = ZoneInfo("Asia/Riyadh")
BNPL_TZ_NAME: str = "Asia/Riyadh"

# Per-provider official invoice-issuance weekday (Python: 0=Mon … 6=Sun).
INVOICE_WEEKDAY: dict[str, int] = {
    "tamara": 5,   # Saturday
    "tabby":  0,   # Monday
}
WEEKDAY_AR: dict[int, str] = {
    0: "الإثنين", 1: "الثلاثاء", 2: "الأربعاء",
    3: "الخميس", 4: "الجمعة", 5: "السبت", 6: "الأحد",
}


def riyadh_now() -> datetime:
    """Current wall-clock time in Asia/Riyadh, fully tz-aware."""
    return datetime.now(BNPL_TZ)


def today_riyadh() -> date:
    """Today's calendar date in Asia/Riyadh."""
    return riyadh_now().date()


def today_riyadh_iso() -> str:
    return today_riyadh().isoformat()


def earliest_save_date_for_period(provider: str, period_to: str) -> str:
    """First Asia/Riyadh date on which a settlement covering
    `period_to` may be saved.  Equals the next occurrence of the
    provider's invoice-weekday on or after `period_to + 1 day`."""
    from datetime import timedelta
    wd = INVOICE_WEEKDAY.get(provider, 5)
    y, m, d = map(int, period_to.split("-"))
    first_eligible = date(y, m, d) + timedelta(days=1)
    delta = (wd - first_eligible.weekday()) % 7
    return (first_eligible + timedelta(days=delta)).isoformat()
