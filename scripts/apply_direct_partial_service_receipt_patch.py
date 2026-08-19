from pathlib import Path


def replace_once(source: str, old: str, new: str, label: str) -> str:
    if source.count(old) != 1:
        raise SystemExit(f"{label} was not found exactly once")
    return source.replace(old, new, 1)


dispatch_path = Path("backend/preparation_supplier_dispatch.py")
source = dispatch_path.read_text(encoding="utf-8")
old_blocker = '''def supplier_receiving_dispatch_blocker(
    piece: dict[str, Any],
    supplier_id: Any,
) -> dict[str, Any] | None:
    """Fail closed only for pieces governed by the new dispatch workflow."""
    dispatch_status = _text(piece.get("supplier_dispatch_status"))
    if not dispatch_status:
        return None  # Backward compatibility for historical files.
    if dispatch_status == DISPATCH_STATUS_PARTIAL:
        return {
            "code": "supplier_piece_not_dispatched",
            "message": "أرسل القطعة إلى المورد المطلوب قبل استلامها.",
        }
    expected_supplier_id = _text(piece.get("supplier_id"))
    actual_supplier_id = _text(supplier_id)
    if expected_supplier_id and expected_supplier_id != actual_supplier_id:
        return {
            "code": "supplier_piece_dispatched_to_different_supplier",
            "message": "هذه القطعة مرسلة إلى مورد مختلف عن جلسة الاستلام الحالية.",
            "expected_supplier_id": expected_supplier_id,
            "expected_supplier_name": _text(piece.get("supplier_name")) or None,
        }
    if dispatch_status not in {DISPATCH_STATUS_SENT, DISPATCH_STATUS_READY}:
        return {
            "code": "supplier_piece_not_dispatched",
            "message": "حالة إرسال القطعة لا تسمح باستلامها من المورد.",
            "dispatch_status": dispatch_status,
        }
    return None
'''
new_blocker = '''def piece_allows_direct_service_receipt(piece: dict[str, Any]) -> bool:
    """Allow the next supplier to be bound when a remaining service is received.

    A first supplier still requires the governed dispatch workflow. Once a real
    supplier invoice exists, the physical piece may move to another specialist
    without a second dispatch file. Scanning it inside that supplier's receiving
    session is the new operational assignment; invoice approval records only the
    services completed there and never charges the product again.
    """
    history = [
        row
        for row in piece.get("supplier_receiving_history") or []
        if isinstance(row, dict) and _text(row.get("invoice_id"))
    ]
    if not history:
        return False
    pending_services = [
        row
        for row in piece.get("services") or []
        if isinstance(row, dict)
        and _text(row.get("service_id"))
        and _text(row.get("status")).casefold() != "completed"
    ]
    try:
        remaining_service_count = int(piece.get("remaining_service_count") or 0)
    except (TypeError, ValueError, OverflowError):
        remaining_service_count = 0
    return bool(
        remaining_service_count > 0
        or pending_services
        or _text(piece.get("execution_status")) == "awaiting_remaining_services"
        or _text(piece.get("supplier_dispatch_status")) == DISPATCH_STATUS_PARTIAL
    )


def supplier_receiving_dispatch_blocker(
    piece: dict[str, Any],
    supplier_id: Any,
) -> dict[str, Any] | None:
    """Require dispatch only for the first supplier of a physical piece.

    For a partially invoiced piece, the current receiving session directly binds
    the supplier at service receipt time. The previous supplier id and the absence
    of a second dispatch file must not block the remaining-service invoice.
    """
    if piece_allows_direct_service_receipt(piece):
        return None
    dispatch_status = _text(piece.get("supplier_dispatch_status"))
    if not dispatch_status:
        return None  # Backward compatibility for historical files.
    if dispatch_status == DISPATCH_STATUS_PARTIAL:
        return {
            "code": "supplier_piece_not_dispatched",
            "message": (
                "لا توجد فاتورة مورد سابقة تثبت الاستلام الجزئي؛ "
                "أرسل القطعة أولًا عبر ملف المورد."
            ),
        }
    expected_supplier_id = _text(piece.get("supplier_id"))
    actual_supplier_id = _text(supplier_id)
    if expected_supplier_id and expected_supplier_id != actual_supplier_id:
        return {
            "code": "supplier_piece_dispatched_to_different_supplier",
            "message": "هذه القطعة مرسلة إلى مورد مختلف عن جلسة الاستلام الحالية.",
            "expected_supplier_id": expected_supplier_id,
            "expected_supplier_name": _text(piece.get("supplier_name")) or None,
        }
    if dispatch_status not in {DISPATCH_STATUS_SENT, DISPATCH_STATUS_READY}:
        return {
            "code": "supplier_piece_not_dispatched",
            "message": "حالة إرسال القطعة لا تسمح باستلامها من المورد.",
            "dispatch_status": dispatch_status,
        }
    return None
'''
if "def piece_allows_direct_service_receipt(" not in source:
    source = replace_once(source, old_blocker, new_blocker, "receiving dispatch blocker")

export_anchor = '    "piece_is_available_for_supplier_dispatch",\n'
if '    "piece_allows_direct_service_receipt",\n' not in source:
    source = replace_once(
        source,
        export_anchor,
        '    "piece_allows_direct_service_receipt",\n' + export_anchor,
        "direct receipt export",
    )
dispatch_path.write_text(source, encoding="utf-8")

Path("backend/tests/test_direct_partial_service_receipt.py").write_text(
    '''from preparation_supplier_dispatch import (
    DISPATCH_STATUS_PARTIAL,
    DISPATCH_STATUS_RECEIVED,
    DISPATCH_STATUS_SENT,
    piece_allows_direct_service_receipt,
    supplier_receiving_dispatch_blocker,
)


def _partial_piece() -> dict:
    return {
        "piece_id": "piece-1",
        "status": "in_progress",
        "execution_status": "awaiting_remaining_services",
        "supplier_id": "supplier-1",
        "supplier_name": "المورد الأول",
        "supplier_dispatch_status": DISPATCH_STATUS_PARTIAL,
        "remaining_service_count": 1,
        "supplier_receiving_history": [{
            "invoice_id": "invoice-1",
            "supplier_id": "supplier-1",
            "service_ids": ["service-1"],
        }],
        "services": [
            {"service_id": "service-1", "status": "completed"},
            {"service_id": "service-2", "status": "pending"},
        ],
    }


def test_partial_piece_is_received_directly_from_second_supplier() -> None:
    piece = _partial_piece()
    assert piece_allows_direct_service_receipt(piece) is True
    assert supplier_receiving_dispatch_blocker(piece, "supplier-2") is None


def test_prior_supplier_assignment_does_not_block_remaining_service_receipt() -> None:
    piece = _partial_piece()
    piece["supplier_dispatch_status"] = DISPATCH_STATUS_SENT
    assert supplier_receiving_dispatch_blocker(piece, "supplier-2") is None


def test_first_supplier_still_requires_original_dispatch_assignment() -> None:
    piece = {
        "piece_id": "piece-new",
        "status": "in_progress",
        "supplier_id": "supplier-1",
        "supplier_name": "المورد الأول",
        "supplier_dispatch_status": DISPATCH_STATUS_SENT,
        "remaining_service_count": 1,
        "supplier_receiving_history": [],
        "services": [{"service_id": "service-1", "status": "pending"}],
    }
    blocker = supplier_receiving_dispatch_blocker(piece, "supplier-2")
    assert piece_allows_direct_service_receipt(piece) is False
    assert blocker is not None
    assert blocker["code"] == "supplier_piece_dispatched_to_different_supplier"


def test_unproven_partial_status_does_not_bypass_dispatch() -> None:
    piece = _partial_piece()
    piece["supplier_receiving_history"] = []
    blocker = supplier_receiving_dispatch_blocker(piece, "supplier-2")
    assert piece_allows_direct_service_receipt(piece) is False
    assert blocker is not None
    assert blocker["code"] == "supplier_piece_not_dispatched"


def test_completed_piece_is_not_a_direct_partial_receipt() -> None:
    piece = _partial_piece()
    piece.update({
        "status": "received",
        "execution_status": "received_from_supplier",
        "supplier_dispatch_status": DISPATCH_STATUS_RECEIVED,
        "remaining_service_count": 0,
        "services": [{"service_id": "service-1", "status": "completed"}],
    })
    assert piece_allows_direct_service_receipt(piece) is False
''',
    encoding="utf-8",
)
