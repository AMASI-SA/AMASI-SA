"""Count only order reversals whose product and preparation stage are evidenced.

Historical resync adjustments have no status snapshot. Their removed items are
retained as unclassified until an explicit stage can be proved; a later current
status must never be used to infer the stage at the time of removal.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import unicodedata
from zoneinfo import ZoneInfo


def _norm(value: Any) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(value.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا"})).replace("_", " ").split())


def _is_completed(value: Any) -> bool:
    return _norm(value) in {"تم التنفيذ", "completed"}


def _is_reversed(value: Any) -> bool:
    text = _norm(value)
    return any(term in text for term in ("ملغ", "محذوف", "الغاء", "cancel", "deleted", "refunded", "refund", "returned", "restored", "restoring", "مسترج", "استرجاع", "مرتجع"))


def _instant(value: Any, *, salla_history: bool = False) -> datetime | None:
    if isinstance(value, dict):
        value = value.get("date")
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Riyadh") if salla_history else timezone.utc)
    return parsed.astimezone(timezone.utc)


def _stage_at(when: Any, history: list[dict], completed_at: Any = None) -> str:
    event_at = _instant(when, salla_history=True)
    if not event_at:
        return "unknown"
    completion = [_instant(row.get("occurred_at"), salla_history=True) for row in history if _is_completed(row.get("title"))]
    stamp = _instant(completed_at)
    if stamp:
        completion.append(stamp)
    completion = [date for date in completion if date]
    if not completion:
        # A stored history starting with order creation and reaching the
        # reversal is affirmative evidence for the pre-execution path.
        created = [_instant(row.get("occurred_at"), salla_history=True) for row in history
                   if row.get("event_type") == "order_created" or _norm(row.get("title")) in {"تم انشاء الطلب", "order created"}]
        if any(date and date <= event_at for date in created):
            return "before"
        return "unknown"
    return "after" if min(completion) <= event_at else "before"


def _positive(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _line_key(item: dict) -> str:
    return _norm(item.get("sku") or item.get("product_id") or item.get("key") or item.get("name"))


def archived_order_lines(capture: dict) -> tuple[str, list[dict], Any, str] | None:
    """Read a complete item list from a verified Salla webhook audit row."""
    payload = capture.get("payload") or {}
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    order = data.get("order") if isinstance(data.get("order"), dict) else data
    items = order.get("items")
    if not isinstance(items, list):
        return None  # Sparse status webhooks do not prove that items vanished.
    reference = str(order.get("reference_id") or order.get("order_number") or
                    data.get("reference_id") or data.get("order_number") or "")
    if not reference:
        return None
    lines: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        product = item.get("product") if isinstance(item.get("product"), dict) else {}
        line = {
            "product_id": product.get("id") or item.get("product_id"),
            "variant_id": item.get("product_sku_id") or item.get("variant_id"),
            "sku": product.get("sku") or item.get("sku"),
            "name": product.get("name") or item.get("name"),
            "image_url": product.get("image") or item.get("image"),
            "quantity": _positive(item.get("quantity")),
        }
        if _line_key(line) and line["quantity"]:
            lines.append(line)
    return reference, lines, capture.get("first_received_at"), str(capture.get("event") or payload.get("event") or "")


def collect_archived_removals(
    captures: list[dict], histories: list[dict], orders: list[dict],
) -> list[dict]:
    """Count drops proven by two full snapshots; ignore sparse status events."""
    history_by_order: dict[str, list[dict]] = {}
    for row in histories:
        history_by_order.setdefault(str(row.get("order_number") or ""), []).append(row)
    orders_by_number = {str(row.get("order_number") or ""): row for row in orders}
    snapshots: dict[str, list[tuple[dict[str, dict], Any, str]]] = {}
    for capture in captures:
        parsed = archived_order_lines(capture)
        if not parsed or parsed[0] not in orders_by_number:
            continue
        number, lines, when, event = parsed
        grouped: dict[str, dict] = {}
        for line in lines:
            key = _line_key(line)
            if key in grouped:
                grouped[key]["quantity"] += line["quantity"]
            else:
                grouped[key] = dict(line)
        if event in {"order.created", "order.products.updated"} or (event == "order.updated" and grouped):
            snapshots.setdefault(number, []).append((grouped, when, event))
    result: list[dict] = []
    for number, sequence in snapshots.items():
        sequence.sort(key=lambda row: _instant(row[1]) or datetime.min.replace(tzinfo=timezone.utc))
        for (previous, _, _), (current, when, event) in zip(sequence, sequence[1:]):
            if event != "order.products.updated":
                # A sparse status/update payload with items=[] is not proof
                # that any customer product was actually removed.
                continue
            for key, line in previous.items():
                delta = line["quantity"] - current.get(key, {}).get("quantity", 0)
                if delta > 0:
                    stage = _stage_at(when, history_by_order.get(number, []),
                                      orders_by_number[number].get("sold_products_completed_at"))
                    result.append({"order_number": number, "item": line,
                                   "quantity": delta, "stage": stage})
    return result


def collect_review_snapshot_removals(
    orders: list[dict], workflows: list[dict],
) -> list[dict]:
    """Recover reviewed product identities missing from the present order."""
    by_order = {str(row.get("order_number") or ""): row for row in orders}
    result: list[dict] = []
    for workflow in workflows:
        number = str(workflow.get("order_number") or "")
        order = by_order.get(number)
        if not order or not workflow.get("reviewed_at") or not (
            _is_reversed(order.get("order_status")) or _is_reversed(order.get("order_status_slug"))
        ):
            continue
        live: dict[str, float] = {}
        for line in order.get("products") or []:
            if isinstance(line, dict):
                key = _line_key(line)
                live[key] = live.get(key, 0) + _positive(line.get("quantity"))
        # The review snapshot proves which pieces existed, but does not date
        # their removal. A later order cancellation cannot date an earlier edit.
        stage = "unknown"
        for snapshot in workflow.get("items") or []:
            if not isinstance(snapshot, dict):
                continue
            item = {**snapshot, "name": snapshot.get("product_name") or snapshot.get("name"),
                    "image_url": snapshot.get("selected_image_url") or snapshot.get("image_url")}
            key = _line_key(item)
            missing = _positive(snapshot.get("quantity")) - live.get(key, 0)
            if key and missing > 0:
                result.append({"order_number": number, "item": item, "quantity": missing, "stage": stage})
                live[key] = 0
            elif key:
                live[key] -= _positive(snapshot.get("quantity"))
    return result


def merge_reversal_evidence(
    current: list[dict], captured: list[dict], reviewed: list[dict],
) -> list[dict]:
    """Use the strongest recorded quantity once per order/product."""
    def group(rows: list[dict]) -> dict[tuple[str, str], list[dict]]:
        grouped: dict[tuple[str, str], list[dict]] = {}
        for row in rows:
            key = (str(row.get("order_number") or ""), _line_key(row.get("item") or {}))
            if key[0] and key[1]:
                grouped.setdefault(key, []).append(row)
        return grouped

    result: list[dict] = []
    current_group = group(current)
    captured_group = group(captured)
    reviewed_group = group(reviewed)
    for key in current_group.keys() | captured_group.keys() | reviewed_group.keys():
        base = current_group.get(key, [])
        archive = captured_group.get(key, [])
        review = reviewed_group.get(key, [])
        # A dated pair of complete webhook snapshots supersedes an undated
        # resync diff when it proves at least as many removed pieces.
        if archive and sum(row["quantity"] for row in archive) >= sum(row["quantity"] for row in base):
            base = archive
        result.extend(base)
        remaining = sum(row["quantity"] for row in review) - sum(row["quantity"] for row in base)
        if remaining > 0:
            result.append({**review[0], "quantity": remaining, "stage": "unknown"})
    return result


def collect_reversals(
    orders: list[dict], adjustments: list[dict], histories: list[dict],
) -> list[dict]:
    """Emit distinct reversed quantities without counting surviving order lines twice."""
    by_order: dict[str, list[dict]] = {}
    for row in histories:
        by_order.setdefault(str(row.get("order_number") or ""), []).append(row)
    adjustments_by_order: dict[str, list[dict]] = {}
    for row in adjustments:
        adjustments_by_order.setdefault(str(row.get("order_number") or ""), []).append(row)

    result: list[dict] = []
    for order in orders:
        number = str(order.get("order_number") or "")
        history = by_order.get(number, [])
        completed_at = order.get("sold_products_completed_at")
        removed_snapshots: dict[str, tuple[float, float]] = {}
        for adjustment in adjustments_by_order.get(number, []):
            # Historic adjustments cannot be assigned a stage from the *current*
            # order status or from a completion stamp captured after resync.
            stage = adjustment.get("removal_stage")
            if stage not in {"before", "after"}:
                stage = "unknown"
            diff = adjustment.get("items_diff") or {}
            lines = list(diff.get("removed") or [])
            for removed in diff.get("removed") or []:
                removed_snapshots[_line_key(removed)] = (_positive(removed.get("quantity")), 0.0)
            for modified in diff.get("modified") or []:
                before_qty = _positive((modified.get("before") or {}).get("quantity"))
                after_qty = _positive((modified.get("after") or {}).get("quantity"))
                difference = before_qty - after_qty
                if difference > 0:
                    removed_snapshots[_line_key(modified)] = (before_qty, after_qty)
                    lines.append({**modified, "quantity": difference, "sku": modified.get("sku") or modified.get("key")})
            for line in lines:
                quantity = _positive(line.get("quantity"))
                if quantity:
                    result.append({"order_number": number, "item": line, "quantity": quantity, "stage": stage})

        status = order.get("order_status_slug") or order.get("order_status")
        if not (_is_reversed(status) or _is_reversed(order.get("order_status"))):
            continue
        # An order status history records the actual reversal instant. Without
        # it, only an already recorded completed transition proves "after".
        reversed_events = [row for row in history if _is_reversed(row.get("title"))]
        reversed_events.sort(key=lambda row: str(row.get("occurred_at") or ""))
        stage = _stage_at(reversed_events[0].get("occurred_at"), history, completed_at) if reversed_events else ("after" if completed_at else "unknown")
        for line in order.get("products") or []:
            if isinstance(line, dict) and _positive(line.get("quantity")):
                snapshot = removed_snapshots.get(_line_key(line))
                if snapshot and snapshot[0] != snapshot[1] and _positive(line["quantity"]) == snapshot[0]:
                    # Canonical product arrays can be stale after a removal;
                    # the recorded deleted quantity already accounts for it.
                    continue
                result.append({"order_number": number, "item": line, "quantity": _positive(line["quantity"]), "stage": stage})
    return result
