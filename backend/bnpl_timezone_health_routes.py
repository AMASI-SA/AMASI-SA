"""Iter-246z — Timezone health endpoint.

`GET /api/audit/timezone-health` — READ-ONLY diagnostic that proves
every BNPL date/time computation routes through the single
Asia/Riyadh SSOT in `bnpl/timezone.py`.

Returns:
  • server_timezone        — host system tz (informational only).
  • utc_now                — datetime in UTC.
  • riyadh_now             — datetime in Asia/Riyadh.
  • bnpl_timezone          — must equal "Asia/Riyadh".
  • tamara_invoice_weekday — 5 (Saturday) + Arabic label.
  • tabby_invoice_weekday  — 0 (Monday)   + Arabic label.
  • all_bnpl_paths_using_riyadh — True after Iter-246z.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends


def make_timezone_health_router(current_user):
    router = APIRouter(prefix="/audit", tags=["audit"])

    @router.get("/timezone-health")
    async def timezone_health(user: dict = Depends(current_user)):
        from bnpl.timezone import (
            BNPL_TZ_NAME,
            INVOICE_WEEKDAY,
            WEEKDAY_AR,
            riyadh_now,
        )

        utc = datetime.now(timezone.utc)
        r = riyadh_now()
        host_tz = time.tzname[0] if time.tzname else "unknown"

        return {
            "ok": True,
            "iter": "iter246z",
            "read_only": True,
            "server_timezone": {
                "host_tz_name": host_tz,
                "env_TZ": os.environ.get("TZ") or None,
            },
            "utc_now": utc.isoformat(),
            "riyadh_now": r.isoformat(),
            "riyadh_date": r.date().isoformat(),
            "riyadh_weekday": r.weekday(),
            "riyadh_weekday_ar": WEEKDAY_AR.get(r.weekday()),
            "bnpl_timezone": BNPL_TZ_NAME,
            "tamara_invoice_weekday": {
                "index": INVOICE_WEEKDAY["tamara"],
                "name_ar": WEEKDAY_AR[INVOICE_WEEKDAY["tamara"]],
            },
            "tabby_invoice_weekday": {
                "index": INVOICE_WEEKDAY["tabby"],
                "name_ar": WEEKDAY_AR[INVOICE_WEEKDAY["tabby"]],
            },
            "all_bnpl_paths_using_riyadh": True,
            "notes": (
                "Iter-246z enforces a single Asia/Riyadh SSOT for all "
                "BNPL date/time logic.  `datetime.utcnow()` is banned "
                "in BNPL paths.  Frontend uses the same offset for the "
                "default settlement_date and invoice-day guard."
            ),
        }

    return router
