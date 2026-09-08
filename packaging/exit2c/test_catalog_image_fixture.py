"""Unit reproduction using application bodies, without server/Mongo/network.

The DB, already-mapped item DTOs and rejecting provider are synthetic adapters.
Image enrichment, identity enrichment and review refresh decisions are the
actual application implementations, loaded without importing the server.
This does not identify the historical 114 requests or prove HTTP acceptance.
"""
import ast
import asyncio
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
import unittest
from urllib.parse import urlsplit


BACKEND = Path("/opt/mezan/backend")
if not BACKEND.is_dir():
    BACKEND = Path(__file__).resolve().parents[2] / "backend"


def load_application_module(name):
    spec = importlib.util.spec_from_file_location(
        "catalog_fixture_" + name, BACKEND / "order_engine" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Item(SimpleNamespace):
    def model_dump(self, *, mode):
        return json.loads(json.dumps(vars(self), default=lambda value: vars(value)))

    def model_copy(self, *, update):
        return Item(**{**copy.deepcopy(vars(self)), **update})


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def limit(self, limit):
        return Cursor(self.rows[:limit])

    def sort(self, field, direction):
        return Cursor(sorted(self.rows, key=lambda row: row.get(field, ""),
                             reverse=direction < 0))

    async def to_list(self, limit):
        return copy.deepcopy(self.rows[:limit])

    def __aiter__(self):
        async def iterate():
            for row in self.rows:
                yield copy.deepcopy(row)
        return iterate()


class Collection:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def find(self, query, projection):
        # All catalog documents are synthetic and belong to the one test owner.
        return Cursor([row for row in self.rows if row["user_id"] == query["user_id"]])

    async def update_one(self, *args, **kwargs):
        raise AssertionError("UNEXPECTED_CATALOG_WRITE")


class Database:
    def __init__(self, rows):
        self.salla_products = Collection(rows)

    def __getitem__(self, name):
        return self.salla_products if name == "salla_products" else Collection()


class MezanCollection(Collection):
    def find(self, query, projection):
        rows = [row for row in self.rows
                if row["user_id"] == query["user_id"]
                and row["product_key"] in query["product_key"]["$in"]
                and "deleted_at" not in row]
        return Cursor(rows)


class CompositeDatabase(Database):
    def __init__(self, rows, mezan_rows):
        super().__init__(rows)
        self.mezan_rows = MezanCollection(mezan_rows)

    def __getitem__(self, name):
        if name == "order_review_mezan_images":
            return self.mezan_rows
        return super().__getitem__(name)


def composite_review_functions(calls):
    """Actual application composition; only persistence/provider/DTOs are adapters."""
    scope = dict(Any=Any, Optional=Optional, hashlib=hashlib, json=json)
    path = BACKEND / "order_review_routes.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {"_text", "_normalized", "_is_personal_option",
             "build_image_preference_identity", "_item_view"}
    constants = {"_PERSONAL_OPTION_HINTS", "_VISUAL_OPTION_PREFIXES"}
    selected = [node for node in tree.body
                if (isinstance(node, ast.FunctionDef) and node.name in names)
                or (isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id in constants
                    for target in node.targets))]
    if len(selected) != len(names) + len(constants):
        raise AssertionError("APPLICATION_COMPOSITION_NOT_FOUND")
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), scope)
    base = SimpleNamespace(**scope)
    wrapper_scope = dict(Any=Any, base=base,
                         MEZAN_IMAGES="order_review_mezan_images",
                         MEZAN_IMAGE_PREFIX="/api/order-reviews-v1/mezan-images/",
                         _original_review_item_identities=review_function(calls),
                         _original_item_view=base._item_view)
    path = BACKEND / "order_review_image_modes.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    selected = [node for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in {"_image_url", "_review_item_identities", "_item_view"}]
    if len(selected) != 3:
        raise AssertionError("APPLICATION_WRAPPER_NOT_FOUND")
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), wrapper_scope)
    return (base, wrapper_scope["_review_item_identities"], wrapper_scope["_item_view"])


class RejectedProvider(Exception):
    pass


def review_function(calls):
    images = load_application_module("product_image_enrichment")
    identity = load_application_module("product_identity_enrichment")

    async def reject(db, user_id, method, path, **kwargs):
        category = "PRODUCT_SEARCH" if path == "/products" else "PRODUCT_DETAIL"
        calls.append((method, category))
        raise RejectedProvider("SYNTHETIC_UNSUPPORTED")

    scope = dict(Any=Any, OrderDTO=Any,
                 enrich_order_item_images=images.enrich_order_item_images,
                 enrich_order_item_identity=identity.enrich_order_item_identity,
                 map_order_item_identities=lambda order: copy.deepcopy(order.items),
                 call_salla=reject, SallaError=RejectedProvider)
    tree = ast.parse((BACKEND / "order_review_routes.py").read_text(encoding="utf-8"))
    selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in {"_text", "_normalized", "_review_item_identities"}]
    if len(selected) != 3:
        raise AssertionError("APPLICATION_BODY_NOT_FOUND")
    exec(compile(ast.Module(body=selected, type_ignores=[]),
                 str(BACKEND / "order_review_routes.py"), "exec"), scope)
    return scope["_review_item_identities"]


def fixture(*, absolute, incomplete=False):
    rows, items = [], []
    for index, colour in enumerate(("ط°ظ‡ط¨ظٹ", "ظپط¶ظٹ")):
        product = "synthetic-product-" + str(index)
        paths = ["/api/order-reviews-v1/mezan-images/" + product + suffix
                 for suffix in ("-front", "-back")]
        urls = [("http://127.0.0.1:8001" if absolute else "") + path for path in paths]
        if incomplete and index == 0:
            urls = urls[:1]
        rows.append(dict(user_id="synthetic-owner", product_id=product, sku=product,
                         main_image=urls[0], images=[{"url": url} for url in urls],
                         gallery_refreshed_at="2026-01-01T00:00:00+00:00"))
        items.append(Item(order_item_id="synthetic-item-" + str(index),
                          product_id=product, sku=product, quantity=2,
                          options=[{"name": "ط§ظ„ظ„ظˆظ†", "value": colour}],
                          image_url=urls[0], image_urls=list(urls)))
    return Database(rows), SimpleNamespace(items=items)


class CatalogImageFixtureTests(unittest.TestCase):
    def composite_views(self):
        original_db, order = fixture(absolute=True)
        # DTO adapter retains actual Arabic option attributes expected by the
        # application identity function; no HTTP/provider or business stubs.
        for item in order.items:
            item.options = [Item(**option) for option in item.options]
        before = copy.deepcopy(order.items)
        calls = []
        base, review, view = composite_review_functions(calls)
        mezan_rows = []
        expected = {}
        for item in order.items:
            product_key, _, _ = base.build_image_preference_identity(item)
            relative = [urlsplit(url).path for url in item.image_urls]
            expected[item.order_item_id] = (list(item.image_urls), relative)
            for index, url in enumerate(relative):
                mezan_rows.append(dict(user_id="synthetic-owner", product_key=product_key,
                                       id=url.rsplit("/", 1)[1], created_at=str(index)))
        db = CompositeDatabase(original_db.salla_products.rows, mezan_rows)
        actual = asyncio.run(review(db, "synthetic-owner", order))
        self.assertEqual(calls, [], "UNEXPECTED_PROVIDER_REFRESH")
        views = [view(item, None, None) for item in actual]
        for old, new, result in zip(before, actual, views):
            for field in ("order_item_id", "product_id", "sku", "quantity", "options"):
                self.assertTrue(getattr(old, field) == getattr(new, field),
                                "COMPOSITE_ITEM_SEMANTICS_CHANGED")
            self.assertTrue(base.build_image_preference_identity(old)
                            == base.build_image_preference_identity(new),
                            "COMPOSITE_IDENTITY_CHANGED")
            self.assertTrue(result["options"] == old.model_dump(mode="json")["options"],
                            "COMPOSITE_OPTIONS_CHANGED")
        return views, expected

    def test_composite_application_gallery_refutes_old_exact_two_contract(self):
        views, _ = self.composite_views()
        # Red-before: the former acceptance predicate rejects the real composed
        # response, even though all fixture image identities are present.
        for view in views:
            self.assertFalse(len(view["gallery"]) == 2,
                             "OLD_GALLERY_PREDICATE_UNEXPECTEDLY_PASSED")

    def test_composite_application_gallery_has_exact_catalog_and_mezan_images(self):
        views, expected = self.composite_views()
        for view in views:
            absolute, relative = expected[view["order_item_id"]]
            self.assertTrue(view["gallery"] == absolute + relative,
                            "COMPOSITE_GALLERY_MEMBERSHIP_CHANGED")
            self.assertTrue(len(view["gallery"]) == len(set(view["gallery"])) == 4,
                            "COMPOSITE_GALLERY_DUPLICATE")
            self.assertTrue(view["mezan_images"] == relative,
                            "MEZAN_IMAGE_PROJECTION_CHANGED")
            self.assertTrue(view["selected_image_url"] == absolute[0],
                            "DEFAULT_IMAGE_CHANGED")

    def exercise(self, *, absolute, incomplete=False):
        db, order = fixture(absolute=absolute, incomplete=incomplete)
        calls = []
        actual = asyncio.run(review_function(calls)(db, "synthetic-owner", order))
        for before, after in zip(order.items, actual):
            for field in ("order_item_id", "product_id", "sku", "quantity", "options"):
                self.assertTrue(getattr(before, field) == getattr(after, field),
                                "ITEM_SEMANTICS_CHANGED")
        return calls, actual

    def test_relative_images_refresh_despite_refreshed_catalog_red_before(self):
        calls, items = self.exercise(absolute=False)
        self.assertEqual(calls.count(("GET", "PRODUCT_DETAIL")), 2)
        self.assertEqual(calls.count(("GET", "PRODUCT_SEARCH")), 2)
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(not item.image_urls for item in items))

    def test_absolute_loopback_catalog_does_not_refresh_green_after(self):
        calls, items = self.exercise(absolute=True)
        self.assertEqual(calls, [])
        self.assertTrue(all(len(item.image_urls) == 2 for item in items))

    def test_incomplete_absolute_catalog_retains_original_refresh(self):
        calls, items = self.exercise(absolute=True, incomplete=True)
        self.assertEqual(calls, [("GET", "PRODUCT_DETAIL"), ("GET", "PRODUCT_SEARCH")])
        self.assertEqual([len(item.image_urls) for item in items], [1, 2])

    def test_absolute_images_preserve_resource_paths_and_snapshot_catalog_agreement(self):
        _, old = fixture(absolute=False)
        new_db, new = fixture(absolute=True)
        for old_item, new_item, row in zip(old.items, new.items, new_db.salla_products.rows):
            self.assertTrue([urlsplit(url).path for url in new_item.image_urls] == old_item.image_urls,
                            "IMAGE_RESOURCE_CHANGED")
            self.assertTrue(new_item.image_urls == [image["url"] for image in row["images"]],
                            "SNAPSHOT_CATALOG_MISMATCH")
            self.assertTrue(all(urlsplit(url).hostname == "127.0.0.1" for url in new_item.image_urls),
                            "NON_LOCAL_IMAGE")


if __name__ == "__main__":
    unittest.main()
