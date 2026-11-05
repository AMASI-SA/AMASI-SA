"""Read-only assembly queue membership; never grants Ready or shipping authority."""
from collections import Counter


def assembly_queue_group(workflow, physical_pieces):
    items = workflow.get("items") or []
    if not items:
        return None
    expected = {}
    for item in items:
        key = str(item.get("order_item_id") or "").strip()
        route = item.get("preparation_route") or "supplier_file"
        try:
            quantity = int(item.get("quantity"))
        except (ValueError, TypeError):
            return None
        if not key or key in expected or quantity < 1 or route not in {"direct_assembly", "supplier_file"}:
            return None
        expected[key] = (route, quantity)
    if all(route == "direct_assembly" for route, _ in expected.values()):
        # Residual supplier custody contradicts an exclusively direct route.
        return "direct_preparation" if not physical_pieces else None
    received = Counter()
    seen = set()
    for piece in physical_pieces:
        key = str(piece.get("order_item_id") or "").strip()
        identity = str(piece.get("piece_id") or "").strip()
        if not identity or identity in seen or key not in expected:
            return None
        seen.add(identity)
        if expected[key][0] != "supplier_file":
            return None
        if piece.get("supplier_dispatch_status") != "received" or not piece.get("received_at"):
            return None
        received[key] += 1
    return "supplier_received" if all(
        route == "direct_assembly" or received[key] == quantity
        for key, (route, quantity) in expected.items()
    ) else None
