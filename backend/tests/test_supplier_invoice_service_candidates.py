from __future__ import annotations

from typing import Any

import pytest

from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import BINDINGS, RESOURCES
from product_v2_routes import PRODUCTS
from supplier_receiving_routes import _supplier_invoice_service_candidate_context


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = [dict(row) for row in rows]

    def sort(self, *_args: Any, **_kwargs: Any) -> "_Cursor":
        return self

    async def to_list(self, length: int) -> list[dict[str, Any]]:
        return self._rows[:length]


class _Collection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = [dict(row) for row in rows]

    async def find_one(
        self,
        _query: dict[str, Any],
        _projection: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        return dict(self._rows[0]) if self._rows else None

    def find(
        self,
        _query: dict[str, Any],
        _projection: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> _Cursor:
        return _Cursor(self._rows)


class _Db:
    def __init__(self) -> None:
        self._collections = {
            PRODUCTS: _Collection([{
                "id": "product-1",
                "mezan_product_id": "product-1",
                "salla_product_id": "salla-1",
                "name": "منتج",
                "sku": "SKU-1",
            }]),
            PRODUCT_RESOURCE_BINDINGS: _Collection([
                {"resource_id": "already-product-linked"},
            ]),
            BINDINGS: _Collection([
                {"resource_id": "already-option-linked"},
            ]),
            RESOURCES: _Collection([
                {
                    "id": "already-product-linked",
                    "name": "مرتبطة بالمنتج",
                    "kind": "service",
                    "unit_cost": 10,
                },
                {
                    "id": "already-option-linked",
                    "name": "مرتبطة بخيار",
                    "kind": "service",
                    "unit_cost": 15,
                },
                {
                    "id": "new-service",
                    "name": "خدمة جديدة",
                    "kind": "service",
                    "unit_cost": 20,
                },
            ]),
        }

    def __getitem__(self, name: str) -> _Collection:
        return self._collections[name]


@pytest.mark.asyncio
async def test_service_candidates_exclude_product_and_option_links() -> None:
    product, candidates, product_links, option_links = (
        await _supplier_invoice_service_candidate_context(
            _Db(),
            user_id="merchant-1",
            session={"supplier_snapshot": {"id": "supplier-1"}},
            product_id="product-1",
        )
    )

    assert product["salla_product_id"] == "salla-1"
    assert product_links == {"already-product-linked"}
    assert option_links == {"already-option-linked"}
    assert [row["id"] for row in candidates] == ["new-service"]
