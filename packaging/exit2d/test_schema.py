from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path("/opt/mezan/backend") if Path("/opt/mezan/backend").is_dir()
    else Path(__file__).resolve().parents[2] / "backend"))
from independent_schema import _IndexDatabase, IndexInitializationFailed


class SchemaBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_index_mutation_cannot_escape_even_if_helper_swallows_it(self):
        db = _IndexDatabase({})
        # A valid collection object is needed to construct the index boundary.
        db._database["orders"] = object()
        try:
            await db.orders.update_many({}, {"$set": {"fixture": True}})
        except IndexInitializationFailed:
            pass
        with self.assertRaises(IndexInitializationFailed):
            db.assert_success()

    async def test_actual_index_error_remains_failure_after_swallowed_exception(self):
        class Collection:
            async def create_index(self, *args, **kwargs):
                raise ValueError("synthetic duplicate")
        db = _IndexDatabase({"orders": Collection()})
        try:
            await db.orders.create_index("order_id", unique=True)
        except ValueError:
            pass
        with self.assertRaises(IndexInitializationFailed):
            db.assert_success()

    async def test_index_options_are_forwarded_to_real_collection(self):
        seen = []
        class Collection:
            def with_options(self, **kwargs):
                seen.append(kwargs)
                return self
            async def create_index(self, *args, **kwargs):
                seen.append((args, kwargs))
                return "fixture_index"
        db = _IndexDatabase({"orders": Collection()})
        self.assertEqual(await db.orders.with_options(write_concern="fixture-majority")
            .create_index("order_id", unique=True), "fixture_index")
        db.assert_success()
        self.assertEqual(seen, [{"write_concern": "fixture-majority"},
            (("order_id",), {"unique": True})])


if __name__ == "__main__":
    unittest.main()
