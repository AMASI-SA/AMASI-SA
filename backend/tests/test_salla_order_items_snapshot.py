"""Provider-boundary proofs: partial or conflicting reads cannot remove items."""
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch


# Loading this pure module directly also permits stdlib-only execution without
# importing the integration package's HTTP/database registration side effects.
_path = Path(__file__).resolve().parents[1] / "salla_integration" / "order_items_snapshot.py"
_spec = importlib.util.spec_from_file_location("order_items_snapshot_contract", _path)
snapshot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(snapshot)
CompleteOrderItems = snapshot.CompleteOrderItems


def item(identity=1, **changes):
    return {"id": identity, "quantity": 1, "product_id": "product-1", **changes}


def page(number, rows, total_pages=2, **metadata):
    return {"data": rows, "pagination": {
        "currentPage": number, "totalPages": total_pages, **metadata,
    }}


class CompleteOrderItemsTests(unittest.TestCase):
    def test_public_validation_does_not_confer_complete_snapshot_provenance(self):
        rows = [item()]
        self.assertIsNone(snapshot.validate_order_items(rows, order_id="o"))
        self.assertIs(type(rows), list)
        self.assertEqual(rows, [item()])
        self.assertIsNone(snapshot.validate_order_items([], order_id="o"))
        for invalid in ([item(quantity=0)], [item(), item()], [item(order_id="other")]):
            with self.subTest(rows=invalid), self.assertRaises(ValueError):
                snapshot.validate_order_items(invalid, order_id="o")

    def test_copies_rows_and_retains_only_internal_provenance(self):
        rows = [item(options=[{"value": "synthetic"}])]
        proof = CompleteOrderItems(rows, order_id=100)
        rows[0]["quantity"] = 4
        rows[0]["options"][0]["value"] = "changed"
        self.assertEqual(proof.order_id, "100")
        self.assertEqual(proof[0]["quantity"], 1)
        self.assertEqual(proof[0]["options"][0]["value"], "synthetic")
        self.assertIs(proof.validate_for("100"), proof)
        decoded = json.loads(json.dumps(proof))
        self.assertIs(type(decoded), list)
        self.assertNotIsInstance(decoded, CompleteOrderItems)

    def test_product_identity_is_optional_but_quantity_is_not_invented(self):
        proof = CompleteOrderItems([{"id": "native", "quantity": "1.50"}], order_id="o")
        self.assertIs(proof.validate_for("o"), proof)
        with self.assertRaisesRegex(ValueError, "quantity"):
            CompleteOrderItems([{"id": "native"}], order_id="o")

    def test_agreeing_native_product_and_order_identities_are_valid(self):
        proof = CompleteOrderItems([item(
            product_id=9, product={"id": "9"}, order_id=100, order={"id": "100"},
        )], order_id=100)
        self.assertIs(proof.validate_for(100), proof)

    def test_invalid_native_identifiers_quantities_and_conflicts_are_rejected(self):
        invalid = [None, [], "item", {}, item(id=None), item(id=True), item(id=False),
                   item(id=" "), item(id=1.5), item(id={"id": 1}),
                   item(product_id=True), item(product={"id": False}), item(product="bad"),
                   item(product_id="a", product={"id": "b"}), item(order_id="other"),
                   item(order={"id": "other"}), item(order_id=False), item(order=[])]
        invalid += [item(quantity=value) for value in (
            None, True, False, 0, -1, float("nan"), float("inf"), "NaN", "Infinity", "", "bad", [], {},
        )]
        for row in invalid:
            with self.subTest(row=row), self.assertRaises(ValueError):
                CompleteOrderItems([row], order_id="o")

    def test_duplicate_id_types_cannot_conceal_duplicate_membership(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            CompleteOrderItems([item(1), item("1")], order_id="o")

    def test_with_rows_allows_sku_enrichment_and_reordering(self):
        proof = CompleteOrderItems([item(1), item(2, quantity="2.0")], order_id="o")
        enriched = [item(2, quantity=2, sku="SKU-2"), item(1, sku="SKU-1")]
        fresh = proof.with_rows(enriched)
        self.assertIsInstance(fresh, CompleteOrderItems)
        self.assertEqual(fresh[0]["sku"], "SKU-2")
        self.assertIs(proof.validate_for("o", enriched), proof)
        enriched[0]["quantity"] = 9
        self.assertEqual(fresh[0]["quantity"], 2)

    def test_revalidation_detects_mutated_identity_quantity_product_and_membership(self):
        mutations = (
            lambda rows: rows[0].update(id=9),
            lambda rows: rows[0].update(quantity=2),
            lambda rows: rows[0].update(product_id="other"),
            lambda rows: rows.append(item(2)),
            lambda rows: rows.clear(),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                proof = CompleteOrderItems([item()], order_id="o")
                mutate(proof)
                with self.assertRaisesRegex(ValueError, "snapshot_changed"):
                    proof.validate_for("o")
                with self.assertRaises(ValueError):
                    proof.with_rows([item()])

    def test_external_rows_must_match_and_proof_cannot_change_order(self):
        proof = CompleteOrderItems([item()], order_id="o")
        for rows in ([], [item(quantity=2)], [item(product_id="other")], [item(2)]):
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, "snapshot_changed"):
                proof.with_rows(rows)
        with self.assertRaisesRegex(ValueError, "order_mismatch"):
            proof.validate_for("other")
        with self.assertRaises(ValueError):
            proof.validate_for(True)


class CompleteOrderItemsFetchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pause = patch.object(snapshot.asyncio, "sleep", new=AsyncMock())
        self.pause.start()
        self.addCleanup(self.pause.stop)

    async def fetch(self, responses):
        provider = AsyncMock(side_effect=responses)
        result = await snapshot.fetch_complete_order_items(None, "owner", "100", request=provider)
        return result, provider

    async def test_documented_complete_array_is_read_once_without_pagination_arguments(self):
        rows = [item(order_id=100), {"id": 2, "quantity": Decimal("2.5")}]
        result, request = await self.fetch([{"data": rows}])
        self.assertIsInstance(result, CompleteOrderItems)
        request.assert_awaited_once_with(None, "owner", "GET", "/orders/items", params={"order_id": "100"})
        self.assertEqual(result, rows)

    async def test_complete_empty_response_remains_explicit_proof(self):
        responses = ({"data": []}, page(1, [], 0, total=0), page(1, [], 1, total=0))
        for response in responses:
            with self.subTest(response=response):
                result, _ = await self.fetch([response])
                self.assertIsInstance(result, CompleteOrderItems)
                self.assertEqual(result, [])
                self.assertIs(result.validate_for("100"), result)

    async def test_all_explicit_pages_are_read_even_when_first_is_short(self):
        result, request = await self.fetch([
            page(1, [item(1)], perPage=50, total=2),
            page(2, [item(2)], perPage=50, total=2),
        ])
        self.assertEqual([row["id"] for row in result], [1, 2])
        self.assertEqual(request.await_args_list[1].kwargs["params"], {"order_id": "100", "page": 2})

    async def test_explicit_next_links_can_prove_completion_without_total_pages(self):
        result, request = await self.fetch([
            {"data": [item(1)], "pagination": {"currentPage": 1, "links": {"next": "https://provider.invalid/items?page=2"}}},
            {"data": [item(2)], "pagination": {"currentPage": 2, "links": {"next": None}}},
        ])
        self.assertEqual(len(result), 2)
        self.assertTrue(all(call.args[3] == "/orders/items" for call in request.await_args_list))

    async def test_malformed_responses_or_rows_are_never_silently_filtered(self):
        responses = (None, [], {}, {"data": {}}, {"data": [None]},
                     {"data": [item(), "bad"]}, {"data": [{"id": 1}]},
                     {"data": [], "success": False})
        for response in responses:
            with self.subTest(response=response), self.assertRaises(ValueError):
                await self.fetch([response])

    async def test_duplicate_ids_within_or_across_pages_are_rejected_even_if_equal(self):
        cases = ([{"data": [item(1), item("1")]}],
                 [page(1, [item(1)]), page(2, [item(1)])])
        for responses in cases:
            with self.subTest(responses=responses), self.assertRaisesRegex(ValueError, "duplicate"):
                await self.fetch(responses)

    async def test_invalid_or_ambiguous_pagination_cannot_authorize_a_short_snapshot(self):
        metadata = (None, {}, {"totalPages": 1}, {"currentPage": 1},
                    {"currentPage": True, "totalPages": 1},
                    {"currentPage": 1.0, "totalPages": 1},
                    {"currentPage": 2, "totalPages": 2},
                    {"currentPage": 1, "totalPages": "1.5"},
                    {"currentPage": 1, "totalPages": 1, "total_pages": 2},
                    {"currentPage": 1, "totalPages": 1, "perPage": 0},
                    {"currentPage": 1, "links": {"next": True}},
                    {"currentPage": 1, "links": {"next": "?page=1"}},
                    {"currentPage": 1, "links": {"next": "?page=2&page=3"}},
                    {"currentPage": 1, "totalPages": 2, "links": {"next": None}},
                    {"currentPage": 1, "totalPages": 1, "links": {"next": "?page=2"}})
        for value in metadata:
            with self.subTest(metadata=value), self.assertRaises(ValueError):
                await self.fetch([{"data": [item()], "pagination": value}])

    async def test_changed_or_disappearing_pagination_fails_without_partial_result(self):
        cases = (
            [page(1, [item(1)]), page(2, [item(2)], 3)],
            [page(1, [item(1)]), {"data": [item(2)]}],
            [page(1, [item(1)]), page(1, [item(2)])],
            [page(1, [item(1)], total=2), page(2, [item(2)])],
            [page(1, [item(1)], perPage=50), page(2, [item(2)], perPage=25)],
        )
        for responses in cases:
            with self.subTest(responses=responses), self.assertRaises(ValueError):
                await self.fetch(responses)

    async def test_premature_empty_page_and_incorrect_totals_are_rejected(self):
        cases = ([page(1, [], 2)], [page(1, [item()], 0)],
                 [page(1, [item()], 1, total=2)], [page(1, [item()], 1, total=0)],
                 [page(1, [item(1)]), page(2, [])],
                 [page(1, [item(1), item(2)], 1, perPage=1)])
        for responses in cases:
            with self.subTest(responses=responses), self.assertRaises(ValueError):
                await self.fetch(responses)

    async def test_provider_failure_after_first_page_never_returns_partial_items(self):
        provider = AsyncMock(side_effect=[page(1, [item()]), RuntimeError("provider unavailable")])
        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            await snapshot.fetch_complete_order_items(None, "owner", "100", request=provider)

    async def test_hard_page_and_row_budgets_are_enforced(self):
        with self.assertRaisesRegex(ValueError, "page_limit"):
            await self.fetch([page(1, [item()], 101)])
        with self.assertRaisesRegex(ValueError, "row_limit"):
            await self.fetch([{"data": [item(index) for index in range(5001)]}])
        responses = [{"data": [item(number)], "pagination": {
            "currentPage": number, "links": {"next": f"?page={number + 1}"},
        }} for number in range(1, 101)]
        provider = AsyncMock(side_effect=responses)
        with self.assertRaisesRegex(ValueError, "page_limit"):
            await snapshot.fetch_complete_order_items(None, "owner", "100", request=provider)
        self.assertEqual(provider.await_count, 100)

    async def test_invalid_order_identity_does_not_contact_provider(self):
        provider = AsyncMock()
        for value in (None, "", " ", True, [], {}):
            with self.subTest(order_id=value), self.assertRaises(ValueError):
                await snapshot.fetch_complete_order_items(None, "owner", value, request=provider)
        provider.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
