"""Read-only product cards scoped to the instruction and its authorized tasks."""
import base64
from io import BytesIO
import qrcode
from order_tracking_notes import instruction_targets, text
from preparation_piece_barcode import parse_preparation_piece_barcode, BARCODE_PREFIX


def product_cards(instruction, visible_tasks, pieces, items, *, creator=False):
    visible = {text(task.get("piece_id")) for task in visible_tasks}
    by_item = {text(item.get("order_item_id") or item.get("id")): item for item in items}
    cards = []
    represented = set()
    seen = set()
    for piece in pieces:
        pid = text(piece.get("piece_id"))
        iid = text(piece.get("order_item_id"))
        if pid in seen or not instruction_targets(instruction, piece_id=pid, order_item_id=iid):
            continue
        if not creator and pid not in visible:
            continue
        seen.add(pid)
        represented.add(iid)
        cards.append(_card(instruction, by_item.get(iid, {}), piece))
    # Before assignment there is no physical barcode. Never invent one.
    if creator:
        for iid, item in by_item.items():
            if iid not in represented and instruction_targets(instruction, order_item_id=iid):
                cards.append(_card(instruction, item, {}))
    return cards


def _card(instruction, item, piece):
    iid = text(piece.get("order_item_id") or item.get("order_item_id") or item.get("id"))
    fields = {}
    for row in piece.get("specifications_snapshot") or []:
        if isinstance(row, dict):
            label = text(row.get("name") or row.get("label") or row.get("title"))
            if label:
                fields[label] = text(row.get("value") or row.get("answer") or row.get("text"))
    fields.update(piece.get("product_options_snapshot") or item.get("options_normalized") or {})
    changes = [change for change in instruction.get("proposed_changes") or []
               if not change.get("order_item_id") or text(change["order_item_id"]) == iid]
    # Explicit old/new values are requester-supplied, not proof of execution.
    before, requested = dict(fields), dict(fields)
    for change in changes:
        before[change["field"]] = change.get("previous", "")
        requested[change["field"]] = change.get("requested", "")
    before_item = (instruction.get("edit_baseline") or {}).get(iid)
    requested_item = (instruction.get("edit_requested") or {}).get(iid)
    if instruction.get("edit_flow") == 1 and before_item:
        before = dict(before_item.get("options_normalized") or {})
        requested = dict((requested_item or before_item).get("options_normalized") or {})
        before["اسم المنتج"] = before_item.get("name") or ""
        requested["اسم المنتج"] = (requested_item or before_item).get("name") or ""
        changes = [{"field": key} for key in dict.fromkeys([*before, *requested]) if before.get(key) != requested.get(key)]
    pid = text(piece.get("piece_id"))
    barcode = BARCODE_PREFIX + pid if parse_preparation_piece_barcode(pid) else ""
    image = ""
    if barcode:
        buffer = BytesIO()
        qr = qrcode.QRCode(box_size=5, border=4)
        qr.add_data(barcode)
        qr.make(fit=True)
        qr.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
        image = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    return {
        "id": pid or iid, "piece_id": pid, "order_item_id": iid,
        "name": text(piece.get("product_name") or item.get("name")),
        "image_url": text(piece.get("selected_image_url") or piece.get("resolved_image_url") or piece.get("image_url") or item.get("image_url")),
        "quantity": 1 if pid else item.get("quantity", 1),
        "before_options": before, "requested_options": requested,
        "changed_fields": [change["field"] for change in changes],
        "changes_source": "salla_snapshot" if instruction.get("edit_flow") == 1 else "requester_supplied",
        "revision_sent": bool(requested_item),
        "before_image_url": text((before_item or {}).get("image_url")),
        "requested_image_url": text((requested_item or {}).get("image_url")),
        "baseline_source": "preparation_snapshot" if piece else "current_order",
        "barcode_value": barcode, "barcode_image": image,
    }
