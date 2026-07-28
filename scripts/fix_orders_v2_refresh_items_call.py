"""Make the central V2 refresh own its Order Items API call directly."""
from pathlib import Path


PATH = Path("backend/order_engine/salla_refresh.py")
TEST = Path("backend/tests/test_fulfillment_v2_contract.py")

source = PATH.read_text(encoding="utf-8")
source = source.replace(
    "from salla_integration.sync import (\n"
    "    _enrich_order_receiving_bank,\n"
    "    _fetch_salla_order_items,\n"
    "    _salla_order_to_doc,\n"
    ")\n",
    "from salla_integration.sync import (\n"
    "    _enrich_order_receiving_bank,\n"
    "    _salla_order_to_doc,\n"
    ")\n",
    1,
)

marker = "\n\nasync def refresh_order_from_salla(\n"
if "async def _fetch_order_items(" not in source:
    helper = '''

async def _fetch_order_items(
    db: Any,
    user_id: str,
    internal_order_id: str,
) -> list[dict[str, Any]]:
    """Fetch authoritative line items through the Orders read permission."""
    response = await call_salla(
        db,
        user_id,
        "GET",
        "/orders/items",
        params={"order_id": str(internal_order_id)},
    )
    rows = response.get("data") if isinstance(response, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError(
            "Salla List Order Items returned invalid payload: "
            f"internal_order_id={internal_order_id}"
        )
    return [dict(row) for row in rows if isinstance(row, dict)]
'''
    if marker not in source:
        raise SystemExit("refresh function marker not found")
    source = source.replace(marker, helper + marker, 1)

source = source.replace(
    "items = await _fetch_salla_order_items(\n",
    "items = await _fetch_order_items(\n",
    1,
)
PATH.write_text(source, encoding="utf-8")

contract = TEST.read_text(encoding="utf-8")
if "assert '\"/orders/items\"' in refresh_source" not in contract:
    contract = contract.replace(
        "    assert 'f\"/orders/{internal_id}\"' in refresh_source\n",
        "    assert 'f\"/orders/{internal_id}\"' in refresh_source\n"
        "    assert '\"/orders/items\"' in refresh_source\n",
        1,
    )
TEST.write_text(contract, encoding="utf-8")

print("Central V2 Order Items call repaired.")
