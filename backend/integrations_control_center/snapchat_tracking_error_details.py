"""Persist bounded, secret-safe Snapchat tracking diagnostic reasons."""
from __future__ import annotations

import logging
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from .snapchat_native_data_common import SNAPCHAT_PROVIDER_ID, _collection
from . import snapchat_native_tracking_diagnostics as tracking_module

logger = logging.getLogger(__name__)

MAX_PERSISTED_DETAIL_ERRORS = 100
_PIXEL_SCOPED_KINDS = {"domains", "pixel_stats", "signal_readiness"}
_KIND_LABELS = {
    "pixels": "اكتشاف Pixels",
    "domains": "إحصاءات نطاقات Pixel",
    "pixel_stats": "إحصاءات أحداث Pixel",
    "signal_readiness": "Signal Readiness",
    "account": "فحص حساب Snapchat",
}
_SAFE_CODE_RE = re.compile(r"^[a-z0-9_.:-]{1,160}$", re.IGNORECASE)


def _safe_identifier(value: Any, *, limit: int = 160) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:limit]


def _safe_code(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not _SAFE_CODE_RE.fullmatch(text):
        return "snapchat_tracking_unknown_error"
    return text


def _is_retryable(code: str) -> bool:
    if code in {
        "snapchat_tracking_provider_network_error",
        "snapchat_tracking_invalid_json",
        "snapchat_tracking_invalid_payload",
        "snapchat_tracking_provider_request_failed",
    }:
        return True
    return code.startswith("snapchat_tracking_http_5")


def _detail_rows(engine: dict[str, Any]) -> list[dict[str, Any]]:
    """Return deduplicated error reasons with account/pixel context."""
    counter: Counter[tuple[str | None, str | None, str, str]] = Counter()

    for item in engine.get("items") or []:
        if not isinstance(item, dict):
            continue
        account_id = _safe_identifier(item.get("ad_account_id"))

        for pixel in item.get("pixels") or []:
            if not isinstance(pixel, dict):
                continue
            pixel_id = _safe_identifier(pixel.get("pixel_id"))
            for error in pixel.get("errors") or []:
                if not isinstance(error, dict):
                    continue
                kind = _safe_identifier(error.get("kind"), limit=80) or "unknown"
                code = _safe_code(error.get("error"))
                counter[(account_id, pixel_id, kind, code)] += 1

        # Pixel-scoped errors also appear in the account aggregate. Keep only
        # account/list errors here to avoid losing pixel identity or duplicating rows.
        for error in item.get("errors") or []:
            if not isinstance(error, dict):
                continue
            kind = _safe_identifier(error.get("kind"), limit=80) or "unknown"
            if kind in _PIXEL_SCOPED_KINDS:
                continue
            code = _safe_code(error.get("error"))
            counter[(account_id, None, kind, code)] += 1

    if not counter:
        for error in engine.get("errors") or []:
            if not isinstance(error, dict):
                continue
            kind = _safe_identifier(error.get("kind"), limit=80) or "unknown"
            code = _safe_code(error.get("error"))
            counter[(None, None, kind, code)] += 1

    rows = [
        {
            "ad_account_id": account_id,
            "pixel_id": pixel_id,
            "kind": kind,
            "code": code,
            "occurrences": occurrences,
            "retryable": _is_retryable(code),
        }
        for (account_id, pixel_id, kind, code), occurrences in counter.items()
    ]
    rows.sort(
        key=lambda row: (
            row.get("ad_account_id") or "",
            row.get("pixel_id") or "",
            row.get("kind") or "",
            row.get("code") or "",
        )
    )
    return rows[:MAX_PERSISTED_DETAIL_ERRORS]


def _detail_message(row: dict[str, Any]) -> str:
    label = _KIND_LABELS.get(str(row.get("kind") or ""), "تشخيص تتبع Snapchat")
    targets: list[str] = []
    if row.get("ad_account_id"):
        targets.append(f"الحساب {row['ad_account_id']}")
    if row.get("pixel_id"):
        targets.append(f"Pixel {row['pixel_id']}")
    target = "، ".join(targets) if targets else "أصل تتبع غير محدد"
    repeated = (
        f" تكرر السبب {int(row['occurrences'])} مرات."
        if int(row.get("occurrences") or 0) > 1
        else ""
    )
    return f"{label} غير متاح لـ {target}. الرمز: {row['code']}.{repeated}"


async def persist_snapchat_tracking_error_details(
    db: Any,
    *,
    user_id: str,
    run_id: str,
    engine: dict[str, Any],
) -> int:
    """Upsert deterministic detail records into the existing V2 error log."""
    details = _detail_rows(engine)
    if not details:
        return 0

    occurred_at = _safe_identifier(engine.get("fetched_at"), limit=80)
    if not occurred_at:
        occurred_at = datetime.now(timezone.utc).isoformat()

    collection = _collection(db, "mezan_integration_errors_v2")
    for row in details:
        identity = "|".join(
            [
                run_id,
                row.get("ad_account_id") or "",
                row.get("pixel_id") or "",
                row.get("kind") or "",
                row.get("code") or "",
            ]
        )
        error_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"mezan:snapchat:tracking-detail:{identity}",
            )
        )
        await collection.update_one(
            {"user_id": user_id, "error_id": error_id},
            {
                "$set": {
                    "error_id": error_id,
                    "user_id": user_id,
                    "provider": SNAPCHAT_PROVIDER_ID,
                    "code": row["code"],
                    "message": _detail_message(row),
                    "occurred_at": occurred_at,
                    "retryable": bool(row["retryable"]),
                    "source_mode": tracking_module.TRACKING_SOURCE_MODE,
                    "run_id": run_id,
                    "diagnostic_kind": row["kind"],
                    "ad_account_id": row.get("ad_account_id"),
                    "pixel_id": row.get("pixel_id"),
                    "occurrences": int(row["occurrences"]),
                },
                "$setOnInsert": {"created_at": occurred_at},
            },
            upsert=True,
        )
    return len(details)


async def _running_tracking_run_id(db: Any, user_id: str) -> str | None:
    run = await _collection(db, "mezan_integration_sync_runs_v2").find_one(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "run_type": "tracking_diagnostics",
            "status": "running",
        },
        {"_id": 0, "run_id": 1},
    )
    return _safe_identifier((run or {}).get("run_id"), limit=100)


def install_snapchat_tracking_error_detail_persistence() -> None:
    """Install a concurrency-safe, one-time wrapper around the diagnostics engine."""
    original_run = tracking_module.SnapchatTrackingDiagnostics.run
    if getattr(original_run, "_mezan_tracking_error_details", False):
        return

    async def wrapped_run(self: Any, user_id: str, payload: Any) -> dict[str, Any]:
        engine = await original_run(self, user_id, payload)
        if engine.get("status") != "partial" or not engine.get("errors_count"):
            return engine
        try:
            run_id = await _running_tracking_run_id(self.db, user_id)
            if run_id:
                engine["detail_errors_persisted"] = (
                    await persist_snapchat_tracking_error_details(
                        self.db,
                        user_id=user_id,
                        run_id=run_id,
                        engine=engine,
                    )
                )
        except Exception:  # noqa: BLE001
            # Observability must never turn a valid bounded diagnostic into a failure.
            logger.exception("Failed to persist Snapchat tracking error details")
        return engine

    wrapped_run._mezan_tracking_error_details = True  # type: ignore[attr-defined]
    tracking_module.SnapchatTrackingDiagnostics.run = wrapped_run


__all__ = [
    "MAX_PERSISTED_DETAIL_ERRORS",
    "install_snapchat_tracking_error_detail_persistence",
    "persist_snapchat_tracking_error_details",
]
