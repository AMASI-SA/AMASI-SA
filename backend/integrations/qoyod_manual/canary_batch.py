"""One-time, closed Qoyod auto-send canary.

The canary intentionally accepts no caller-provided order numbers.  Keeping
the allow-list in code makes the first automatic run auditable and prevents a
UI mistake from expanding it to other pending orders.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from integrations.qoyod_manual.send import ManualSendRefused


CANARY_ORDER_NUMBERS: tuple[str, ...] = (
    "273274882",  # COD
    "271235259",  # credit card
    "272982420",  # mada
    "272809621",  # mada
)

CANARY_CONFIRMATION = "تشغيل تجربة الأربعة"

# These responses mean that duplicate protection did its job.  They are safe
# to count as completed when the canary is retried after a browser/network
# interruption.
SAFE_ALREADY_SENT_CODES = frozenset({
    "already_sent",
    "already_sent_legacy",
    "duplicate_invoice_in_qoyod",
})


async def execute_canary_batch(
    send_order: Callable[[str], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Send the fixed canary sequentially and stop on the first real error."""
    results: list[dict[str, Any]] = []
    stopped_on: str | None = None

    for order_number in CANARY_ORDER_NUMBERS:
        try:
            payload = await send_order(order_number)
            results.append({
                "order_number": order_number,
                "outcome": "sent",
                "ok": True,
                "invoice_only": bool(payload.get("invoice_only")),
                "invoice_id": payload.get("invoice_id"),
                "invoice_number": payload.get("invoice_number"),
                "payment_id": payload.get("payment_id"),
                "difference": payload.get("difference"),
            })
        except ManualSendRefused as exc:
            if exc.code in SAFE_ALREADY_SENT_CODES:
                results.append({
                    "order_number": order_number,
                    "outcome": "already_sent",
                    "ok": True,
                    "code": exc.code,
                    "message": exc.message,
                    "invoice_id": (
                        exc.extra.get("qoyod_invoice_id")
                        or exc.extra.get("manual_qoyod_invoice_id")
                    ),
                    "payment_id": exc.extra.get(
                        "manual_qoyod_payment_id"),
                    "invoice_only": bool(exc.extra.get("invoice_only")),
                    "detail": exc.extra,
                })
                continue

            results.append({
                "order_number": order_number,
                "outcome": "failed",
                "ok": False,
                "code": exc.code,
                "message": exc.message,
                "detail": exc.extra,
            })
            stopped_on = order_number
            break

    sent_count = sum(r["outcome"] == "sent" for r in results)
    already_sent_count = sum(
        r["outcome"] == "already_sent" for r in results)
    invoice_only_count = sum(
        r.get("outcome") == "sent" and r.get("invoice_only")
        for r in results)
    failed_count = sum(r["outcome"] == "failed" for r in results)

    return {
        "ok": failed_count == 0 and len(results) == len(CANARY_ORDER_NUMBERS),
        "status": "succeeded" if failed_count == 0 and len(results) == len(
            CANARY_ORDER_NUMBERS) else "stopped_on_error",
        "order_numbers": list(CANARY_ORDER_NUMBERS),
        "results": results,
        "sent_count": sent_count,
        "already_sent_count": already_sent_count,
        "invoice_only_count": invoice_only_count,
        "failed_count": failed_count,
        "stopped_on": stopped_on,
        "remaining_count": len(CANARY_ORDER_NUMBERS) - len(results),
    }
