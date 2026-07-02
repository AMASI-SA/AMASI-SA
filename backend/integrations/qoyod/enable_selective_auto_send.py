"""Iter-2026-02.rev16 — Enable / Disable Selective Auto-Send.

STRICT invariants (user directive 2026-02-27)
─────────────────────────────────────────────
• `production_writes_locked` on-disk value is NEVER modified by
  these endpoints. The Selective Auto-Send gate grants a per-row
  scoped write allowance at the api_client layer only.
• Enable stamps `selective_auto_send_cutover_at` = NOW automatically.
  Orders created STRICTLY BEFORE this timestamp will never be
  auto-sent — no backlog, no Q2/Q3 backfill.
• Enable starts with `selective_auto_send_allowed_payment_methods`
  = `["tabby_installment"]`. Operator expands manually after the
  first confirmed end-to-end success.
• Disable clears the enable flag AND the cutover — a fresh enable
  produces a fresh cutover so no stale gap is exploited.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


_ENABLE_CONFIRM  = "ENABLE-SELECTIVE-AUTO-SEND"
_DISABLE_CONFIRM = "DISABLE-SELECTIVE-AUTO-SEND"


class SelectiveAutoSendRefused(Exception):
    def __init__(self, code: str, human: str):
        super().__init__(human)
        self.code  = code
        self.human = human


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def enable_selective_auto_send(
    db, *,
    user_id: str,
    confirm_token: str,
    allowed_payment_methods: Optional[list[str]] = None,
    actor: str = "operator",
) -> dict:
    """Flip the master switch ON and stamp cutover_at=NOW.

    Idempotent: calling twice does NOT move cutover_at forward
    (stops surprise "widening" of the eligible window). The
    idempotency check runs BEFORE any write.
    """
    if confirm_token != _ENABLE_CONFIRM:
        raise SelectiveAutoSendRefused(
            "confirm_token_mismatch",
            f"Pass confirm_token='{_ENABLE_CONFIRM}' to authorise.")

    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}
    already_enabled = bool(
        settings.get("selective_auto_send_enabled", False))
    existing_cutover = settings.get("selective_auto_send_cutover_at")

    if already_enabled and existing_cutover:
        return {
            "ok":       True,
            "outcome":  "ALREADY_ENABLED",
            "cutover_at":                existing_cutover,
            "allowed_payment_methods":   settings.get(
                "selective_auto_send_allowed_payment_methods")
                or ["tabby_installment"],
            "production_writes_locked":  settings.get(
                "production_writes_locked", True),
            "note": ("Already enabled — cutover_at unchanged to "
                     "prevent widening the eligible window."),
        }

    now_iso   = _now_iso()
    allowlist = allowed_payment_methods or ["tabby_installment"]

    await db.qoyod_settings.update_one(
        {"user_id": user_id},
        {"$set": {
            "user_id": user_id,
            "selective_auto_send_enabled":               True,
            "selective_auto_send_cutover_at":            now_iso,
            "selective_auto_send_allowed_payment_methods":
                allowlist,
            "selective_auto_send_enabled_by":            actor,
            "selective_auto_send_enabled_at":            now_iso,
        }},
        upsert=True)

    return {
        "ok":                       True,
        "outcome":                  "ENABLED",
        "cutover_at":               now_iso,
        "allowed_payment_methods":  allowlist,
        # Never touched by this endpoint — surface for transparency.
        "production_writes_locked": settings.get(
            "production_writes_locked", True),
        "human_message": (
            "تم تفعيل الإرسال التلقائي الانتقائي. "
            "الطلبات الجديدة فقط بعد هذا الوقت وبطرق الدفع المحددة "
            "ستُرسل تلقائياً. `production_writes_locked` لم يُعدَّل."),
    }


async def disable_selective_auto_send(
    db, *,
    user_id: str,
    confirm_token: str,
    actor: str = "operator",
) -> dict:
    if confirm_token != _DISABLE_CONFIRM:
        raise SelectiveAutoSendRefused(
            "confirm_token_mismatch",
            f"Pass confirm_token='{_DISABLE_CONFIRM}' to authorise.")

    now_iso = _now_iso()
    res = await db.qoyod_settings.update_one(
        {"user_id": user_id},
        {"$set": {
            "selective_auto_send_enabled":     False,
            "selective_auto_send_disabled_by": actor,
            "selective_auto_send_disabled_at": now_iso,
        },
         # Clear cutover — next enable stamps a fresh one.
         "$unset": {
            "selective_auto_send_cutover_at": "",
        }})
    return {
        "ok":              True,
        "outcome":         "DISABLED",
        "disabled_at":     now_iso,
        "matched_count":   getattr(res, "matched_count", None),
        "modified_count":  getattr(res, "modified_count", None),
        "human_message": (
            "تم إيقاف الإرسال التلقائي. أي طلب جديد لن يُرسل "
            "تلقائياً حتى تعيد التفعيل."),
    }


async def expand_allowed_payment_methods(
    db, *,
    user_id: str,
    add_methods: list[str],
    confirm_token: str,
    actor: str = "operator",
) -> dict:
    """Extend `selective_auto_send_allowed_payment_methods` — used
    AFTER the first confirmed end-to-end auto-send success. Cannot
    add hard-blocked methods (bank_transfer / COD)."""
    expected = "EXPAND-SELECTIVE-AUTO-SEND"
    if confirm_token != expected:
        raise SelectiveAutoSendRefused(
            "confirm_token_mismatch",
            f"Pass confirm_token='{expected}' to authorise.")
    from integrations.qoyod.selective_auto_send_gate import (
        BLOCKED_PAYMENT_METHODS,
    )
    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}
    current = settings.get(
        "selective_auto_send_allowed_payment_methods") or []
    rejected: list[str] = []
    added:    list[str] = []
    new_list: list[str] = list(current)
    for m in add_methods:
        mm = str(m).lower().strip()
        if mm in BLOCKED_PAYMENT_METHODS:
            rejected.append(mm)
            continue
        if mm not in {x.lower() for x in new_list}:
            new_list.append(mm)
            added.append(mm)

    if not added:
        return {
            "ok":                     True,
            "outcome":                "NO_CHANGE",
            "allowed_payment_methods": new_list,
            "rejected":               rejected,
        }
    await db.qoyod_settings.update_one(
        {"user_id": user_id},
        {"$set": {
            "selective_auto_send_allowed_payment_methods": new_list,
            "selective_auto_send_expanded_by":              actor,
            "selective_auto_send_expanded_at":              _now_iso(),
        }})
    return {
        "ok":                       True,
        "outcome":                  "EXPANDED",
        "added":                    added,
        "rejected":                 rejected,
        "allowed_payment_methods":  new_list,
    }
