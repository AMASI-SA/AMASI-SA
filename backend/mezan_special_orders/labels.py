"""Existing assembly/label contract backed by a private new local label."""
from __future__ import annotations

from decimal import Decimal

from .contracts import EXPONENTS
from .domain import DomainError, balances, dispatch_blockers
from .evidence import EvidenceStore
from .source_hooks import current_document


async def local_shipping_label(db, tenant_id, order_number, *, force_store_courier=False):
    from order_engine.shipping_label_service import ShippingLabelError, _qr_data_uri
    try:
        document, workflow = await current_document(db, tenant_id, order_number, write=True)
        if not workflow or workflow.get("stage") != "completed" or workflow.get("assembly_status") != "completed":
            raise DomainError("assembly_completion_required")
        blockers = dispatch_blockers(document)
        if blockers:
            raise DomainError(blockers[0])
        from .domain import effective_delivery
        delivery = effective_delivery(document)
        if force_store_courier and delivery["method"] != "courier":
            raise DomainError("local_order_delivery_override_not_allowed")
        await EvidenceStore(db).verify_delivery(document)
        result = {"ok": True, "source": "mezan", "source_provider": "mezan", "ready": True,
            "order_number": order_number, "order_status_completed": False,
            "order_status_changed": False, "salla_status_sync": "not_applicable",
            "courier_name": "مندوب المتجر" if delivery["method"] == "courier" else delivery["carrier_key"],
            "tracking_number": delivery.get("tracking_number"), "shipping_number": delivery.get("tracking_number"),
            "shipment_id": None, "message": "بوليصة طلب ميزان؛ لم تُجرَ أي عملية في سلة.",
            "label_url": None, "print_data": None}
        if delivery["method"] == "carrier":
            result.update(label_type="carrier", status="verified_private_label",
                label_requires_authorization=True,
                label_url=f"/api/special-orders-v1/{document['order_id']}/label")
            return result
        currency = document["fx"]["currency"]
        scale = Decimal(10) ** EXPONENTS[currency]
        def money(value):
            return {"amount": float(Decimal(value) / scale), "currency": currency}
        settings = await db.settings.find_one({"user_id": str(tenant_id)}, {"_id": 0, "store_name": 1, "store_phone": 1}) or {}
        recipient = document["recipient"]
        address = dict(recipient["address"] or {})
        result.update(label_type="store_courier", status="store_courier", print_data={
            "order_number": order_number, "barcode_value": order_number, "qr_code": _qr_data_uri(order_number),
            "order_date": document["created_at"][:10], "courier_name": "مندوب المتجر",
            "store_name": settings.get("store_name") or "المتجر", "store_logo": None,
            "store_phone": settings.get("store_phone"),
            "customer_name": recipient["name"], "customer_phone": recipient["mobile"],
            "address": {**address, "block": address.get("district"), "address_line": address.get("formatted")},
            "total": money(document["customer_agreed_minor"]),
            "remaining_amount": money(balances(document)["remaining_minor"]),
            "purpose_badge": document["badge"], "original_order_number": (document["original"] or {}).get("order_number"),
            "items": [{"name": i["product"]["name"], "quantity": i["quantity"], "sku": i["product"]["sku"]} for i in document["items"]],
        })
        return result
    except DomainError as exc:
        raise ShippingLabelError(exc.code, "تعذّر التحقق من بوليصة طلب ميزان أو مبلغ التحصيل.", status_code=exc.http_status) from None
