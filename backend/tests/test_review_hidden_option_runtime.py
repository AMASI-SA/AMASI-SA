"""Hidden-option approval through the production router/DB-adapter combination.

Synthetic loopback Mongo only. Reuse the fixture's provider-call prohibition;
never import server startup, run workers, or use live order data.
"""
import unittest
from unittest.mock import patch

import order_review_routes as routes
import order_review_export_controls as controls
import order_review_completion as completion
from review_acceptance_config_guard import AcceptanceConfigDatabase
import test_review_attempt_closure as closure


class HiddenOptionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = closure.ReviewAttemptClosureTests.asyncTearDown
    source_payload = closure.ReviewAttemptClosureTests.source_payload
    webhook = closure.ReviewAttemptClosureTests.webhook
    display = closure.ReviewAttemptClosureTests.display
    request = closure.ReviewAttemptClosureTests.request
    submit = closure.ReviewAttemptClosureTests.submit
    assert_binding = closure.ReviewAttemptClosureTests.assert_binding
    assert_commit = closure.ReviewAttemptClosureTests.assert_commit

    async def asyncSetUp(self):
        await closure.ReviewAttemptClosureTests.asyncSetUp(self)
        # server.py imports this wrapper, which replaces three base functions.
        # Restore the pre-test functions so this module does not contaminate
        # neighboring router-only suites in the same Python process.
        names = ("_preference_map", "_review_item_identities", "_item_view")
        originals = {name: getattr(routes, name) for name in names}
        import order_review_image_modes as images
        for name in names:
            setattr(routes, name, originals[name])
            replacement = patch.object(routes, name, getattr(images, name))
            replacement.start()
            self.addCleanup(replacement.stop)
        async def actor():
            return self.actor
        production_db = AcceptanceConfigDatabase(self.db)
        self.app.include_router(images.make_order_review_router(production_db, actor), prefix="/runtime")
        self.app.include_router(controls.make_order_review_export_controls_router(production_db, actor), prefix="/runtime")
        self.route_prefix = "/runtime"

    async def exercise(self, *, show_again=False):
        number = "synthetic-runtime-options"
        source = self.source_payload(number=number)
        source["items"][0]["options"] = [
            {"id": "synthetic-size", "name": "المقاس", "value": "M"},
            {"id": "synthetic-note", "name": "ملاحظة", "value": "Synthetic note"},
        ]
        self.assertTrue((await self.webhook(source))["synced"])
        before = await self.display(number)
        item_id = before["items"][0]["order_item_id"]
        values = [["size", "ملاحظة"], []] if show_again else [["size", "ملاحظة"]]
        for hidden in values:
            changed = await self.client.patch(
                f"/runtime/order-review-export-controls-v1/{number}/items/{item_id}",
                json={"manual_hidden_spec_keys": hidden},
            )
            self.assertEqual(changed.status_code, 200, changed.text)
            # Native card refreshes controls after receiving fresh consent.
            detail = await self.display(number)
            read_controls = await self.client.get(f"/runtime/order-review-export-controls-v1/{number}")
            self.assertEqual(read_controls.status_code, 200, read_controls.text)
        request = self.request(detail)
        await self.assert_commit(await self.submit(request, number), detail, request)
        await self.assert_commit(await self.submit(request, number), detail, request)
        workflow = await self.db[completion.WORKFLOWS].find_one({"order_number": number})
        self.assertEqual(workflow["items"][0]["manual_supplier_export_excluded_spec_keys"], sorted(values[-1]))
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 1)
        self.external.assert_not_awaited()

    async def test_hidden_options_complete_on_production_router_and_adapter(self):
        await self.exercise()

    async def test_hidden_then_visible_options_complete_on_same_runtime(self):
        await self.exercise(show_again=True)


if __name__ == "__main__":
    unittest.main()
