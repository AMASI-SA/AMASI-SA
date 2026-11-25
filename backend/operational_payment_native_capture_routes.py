"""Owner-only native Salla original capture preview; NEVER posts or syncs.

Read at most one unique canonical order and one bounded group of saved native
transaction proofs. No customer, payment instrument, full source or financial
state is returned. Merchant/owner permission is rechecked before responding.
"""
from datetime import datetime, timezone

from fastapi import Depends, Request
from operational_balance_store import fail
from operational_visible_report_read import run_visible_report_read


def attach_native_capture_preview(router, db, current_user, scope):
    @router.get("/payment-events/native-capture-preview")
    async def native_capture_preview(
        request: Request, order_number: str, user=Depends(current_user)
    ):
        async def read_page():
            actor, owner, _ = await scope(db, user, "reports")
            if actor.get("role") != "owner" or actor.get("id") != owner:
                fail("operational_payment_native_preview_owner_required",
                     "فحص إثبات التحصيل الأصلي متاح لمالك المؤسسة فقط", 403)
            from operational_payment_native_capture_read import (
                preview_one_native_capture,
            )
            try:
                result = await preview_one_native_capture(
                    db, owner, order_number,
                    as_of=datetime.now(timezone.utc).isoformat(),
                )
            except ValueError as exc:
                if str(exc) == "payment_order_number_invalid":
                    fail("payment_order_number_invalid", "رقم الطلب غير صالح", 422)
                fail("operational_native_capture_preview_unavailable",
                     "تعذر التحقق من أدلة سلة الأصلية", 503)
            except Exception:
                fail("operational_native_capture_preview_unavailable",
                     "تعذر قراءة إثبات الدفع الأصلي", 503)
            latest, current_owner, _ = await scope(db, user, "reports")
            if (current_owner != owner or latest.get("id") != actor.get("id")
                    or latest.get("role") != "owner"):
                fail("operational_actor_scope_changed", "تغير ارتباط الحساب", 403)
            # The proof model always marks posting_permitted=False.
            return result
        return await run_visible_report_read(request, read_page)

    return router
