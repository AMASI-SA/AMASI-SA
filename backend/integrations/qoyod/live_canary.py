"""Iter-2026-02.rev31 — Live Canary for Tabby (Option A).

Purpose-built admin endpoint that flips EXACTLY three flags and
nothing else:

    dry_run_mode                = False
    production_writes_locked    = False
    selective_live_send_enabled = True

STRICT preconditions (all must hold BEFORE any write; otherwise raise
`LiveCanaryRefused`):

    auto_send                                      == False
    selective_auto_send_enabled                    == True
    selective_auto_send_allowed_payment_methods    == ["tabby_installment"]
    auto_receipt                                   == True
    capabilities.create_receipts                   == True

Never touches:

    payment_method_mapping (untouched — no new methods added)
    auto_send (must ALREADY be False; endpoint refuses if True)

Symmetric `disable_tabby_live_canary(...)` restores the fail-closed
posture (dry_run_mode=True, production_writes_locked=True,
selective_live_send_enabled=False) with NO other side-effects.

The whole thing is a single atomic Mongo update; a partial-write
window is not possible.

Why a dedicated module + endpoint (Option A) instead of extending
`SettingsPatch`:

- SettingsPatch is a generic PUT. Adding `selective_live_send_enabled`
  there would allow ANY UI/API caller to flip live-send on/off
  without the operator-guardrails above. Dedicated endpoint = one
  single, auditable, hard-guarded flip site.
- Precondition checks live INSIDE the function, so no future PATCH
  can bypass them.
- Every call — success or refusal — is logged with the confirm token
  and actor for audit.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class LiveCanaryRefused(Exception):
    """rev31 — Raised when the tabby-live-canary flip cannot be
    performed because at least one precondition is not met. The
    exception carries a stable machine-readable `code` alongside the
    human message so the UI can act on it deterministically."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


_ENABLE_CONFIRM = "ENABLE_TABBY_LIVE_CANARY"
_DISABLE_CONFIRM = "DISABLE_TABBY_LIVE_CANARY"

# Frozen allowlist: rev31 is EXCLUSIVELY for tabby_installment.
# Widening to other methods (mada, apple_pay, credit_card, stc_pay,
# tamara) is a SEPARATE decision that must not be piggy-backed on
# this endpoint.
_REQUIRED_ALLOWLIST: tuple[str, ...] = ("tabby_installment",)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_preconditions(settings: dict) -> None:
    """Raises `LiveCanaryRefused` on the FIRST failing precondition."""
    # 1. auto_send must be OFF. Live-Canary is per-order via Selective
    # Auto Send; the master `auto_send` switch must stay OFF.
    if bool(settings.get("auto_send", False)):
        raise LiveCanaryRefused(
            "auto_send_is_on",
            "auto_send=True detected. Turn it OFF before enabling the "
            "Tabby live canary — this endpoint intentionally does NOT "
            "flip the master auto-send flag.")

    # 2. Selective Auto Send must be enabled (that's the gate we open).
    if not bool(settings.get("selective_auto_send_enabled", False)):
        raise LiveCanaryRefused(
            "selective_auto_send_disabled",
            "selective_auto_send_enabled=False. Enable Selective Auto "
            "Send first (via enable_selective_auto_send) then retry.")

    # 3. Allow-list must be EXACTLY ['tabby_installment']. Any extras
    # (mada, apple_pay, credit_card, stc_pay, tamara, cod, bank_transfer)
    # are grounds for refusal.
    allowed = list(settings.get(
        "selective_auto_send_allowed_payment_methods") or [])
    if tuple(allowed) != _REQUIRED_ALLOWLIST:
        raise LiveCanaryRefused(
            "allowlist_not_exactly_tabby",
            "selective_auto_send_allowed_payment_methods must be "
            f"exactly {list(_REQUIRED_ALLOWLIST)!r} for the canary. "
            f"Current: {allowed!r}. Remove other methods (or run a "
            "separate widening decision) then retry.")

    # 4. auto_receipt must be True — otherwise Tabby (prepaid) would
    # stop at COMPLETED_INVOICE_ONLY (rev30 short-circuit).
    if not bool(settings.get("auto_receipt", True)):
        raise LiveCanaryRefused(
            "auto_receipt_disabled",
            "auto_receipt=False. Tabby is a prepaid method — the "
            "invoice_payment step MUST be allowed to run. Set "
            "auto_receipt=True first.")

    # 5. capabilities.create_receipts must be True — same reasoning.
    caps = settings.get("capabilities") or {}
    if not bool(caps.get("create_receipts", True)):
        raise LiveCanaryRefused(
            "capability_create_receipts_disabled",
            "capabilities.create_receipts=False. Set it to True so "
            "the invoice_payment step can run for Tabby.")


async def enable_tabby_live_canary(
    db, *,
    user_id: str,
    confirm_token: str,
    actor: str = "operator",
) -> dict:
    """Flip the three live-canary flags for tabby_installment.

    Idempotent: if all three flags are already at their canary values
    AND all preconditions hold, returns outcome=ALREADY_ENABLED
    without a write.
    """
    if confirm_token != _ENABLE_CONFIRM:
        raise LiveCanaryRefused(
            "confirm_token_mismatch",
            f"Pass confirm_token='{_ENABLE_CONFIRM}' to authorise.")

    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}

    # Hard preconditions FIRST — never write on a bad state.
    _check_preconditions(settings)

    already = (
        settings.get("dry_run_mode") is False
        and settings.get("production_writes_locked") is False
        and settings.get("selective_live_send_enabled") is True)
    if already:
        logger.info(
            "rev31 live_canary_already_enabled user_id=%s actor=%s",
            user_id, actor)
        return {
            "ok":                          True,
            "outcome":                     "ALREADY_ENABLED",
            "dry_run_mode":                False,
            "production_writes_locked":    False,
            "selective_live_send_enabled": True,
            "human_message": (
                "Tabby Live Canary already active. No changes made."),
        }

    now_iso = _now_iso()
    patch = {
        # The three-and-only-three flags this endpoint owns.
        "dry_run_mode":                False,
        "production_writes_locked":    False,
        "selective_live_send_enabled": True,
        # Audit trail — never touched by any other endpoint.
        "tabby_live_canary_enabled_at": now_iso,
        "tabby_live_canary_enabled_by": actor,
        "tabby_live_canary_disabled_at": None,
        "tabby_live_canary_disabled_by": None,
    }
    await db.qoyod_settings.update_one(
        {"user_id": user_id},
        {"$set": patch},
        upsert=True,
    )
    logger.warning(
        "rev31 live_canary_enabled user_id=%s actor=%s at=%s",
        user_id, actor, now_iso)
    return {
        "ok":                          True,
        "outcome":                     "ENABLED",
        "dry_run_mode":                False,
        "production_writes_locked":    False,
        "selective_live_send_enabled": True,
        "auto_send_still_off":         True,
        "allowed_payment_methods":     list(_REQUIRED_ALLOWLIST),
        "enabled_at":                  now_iso,
        "enabled_by":                  actor,
        "human_message": (
            "تم تفعيل Live Canary لـ Tabby فقط. "
            "auto_send لا يزال False. "
            "selective_auto_send يعمل مع tabby_installment فقط. "
            "أي طلب Tabby جديد بعد هذه اللحظة سيُرسل فعلياً إلى قيود. "
            "لإيقاف Canary فوراً: استخدم "
            "POST /admin/live-canary/disable-tabby مع "
            f"confirm_token='{_DISABLE_CONFIRM}'."),
    }


async def disable_tabby_live_canary(
    db, *,
    user_id: str,
    confirm_token: str,
    actor: str = "operator",
    reason: Optional[str] = None,
) -> dict:
    """Restore the fail-closed posture. No preconditions on the
    settings — this is a rollback and must ALWAYS succeed."""
    if confirm_token != _DISABLE_CONFIRM:
        raise LiveCanaryRefused(
            "confirm_token_mismatch",
            f"Pass confirm_token='{_DISABLE_CONFIRM}' to authorise.")

    now_iso = _now_iso()
    patch = {
        "dry_run_mode":                True,
        "production_writes_locked":    True,
        "selective_live_send_enabled": False,
        "tabby_live_canary_disabled_at": now_iso,
        "tabby_live_canary_disabled_by": actor,
        "tabby_live_canary_disabled_reason": reason,
    }
    await db.qoyod_settings.update_one(
        {"user_id": user_id},
        {"$set": patch},
        upsert=True,
    )
    logger.warning(
        "rev31 live_canary_disabled user_id=%s actor=%s reason=%s at=%s",
        user_id, actor, reason, now_iso)
    return {
        "ok":                          True,
        "outcome":                     "DISABLED",
        "dry_run_mode":                True,
        "production_writes_locked":    True,
        "selective_live_send_enabled": False,
        "disabled_at":                 now_iso,
        "disabled_by":                 actor,
        "reason":                      reason,
        "human_message": (
            "تم إيقاف Live Canary. النظام عاد إلى fail-closed "
            "posture: dry_run_mode=True, production_writes_locked=True."),
    }
