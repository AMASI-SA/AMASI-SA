"""Pure local boundary tests; no application/database/provider imports."""
import ast
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from independent_callbacks import classify_callbacks

# Independently enumerated from actual decorator/append registration sites.
PAIRS = [
 ("advertising_product_watch_scheduler_v3", "attach_advertising_product_watch_scheduler", "_start", "_stop"),
 ("campaign_ai_subprocess_scheduler", "attach_campaign_ai_subprocess_scheduler", "_start_campaign_ai_subprocess_scheduler", "_stop_campaign_ai_subprocess_scheduler"),
 ("product_google_taxonomy_ai_pilot", "make_product_google_taxonomy_ai_pilot_router", "start_resumable_run_loop", "stop_resumable_run_loop"),
 ("integrations_control_center.ads_auto_sync_scheduler", "attach_ads_auto_sync_scheduler", "start", "stop"),
 ("integrations_control_center.snapchat_capi_purchases", "attach_snapchat_capi_purchase_routes", "start", "stop"),
 ("snapchat_v2.scheduler", "attach_shadow_scheduler", "start", "stop"),
]

def callback(module, outer, name):
    async def must_not_run():
        raise AssertionError("Classification must never execute callbacks")
    must_not_run.__module__ = module
    must_not_run.__qualname__ = outer + ".<locals>." + name
    return must_not_run

class CallbackClassificationTests(unittest.TestCase):
    def fixtures(self):
        return ([callback(m, o, a) for m, o, a, b in PAIRS],
                [callback(m, o, b) for m, o, a, b in PAIRS])

    def test_reviewed_callbacks_accept_exact_pairs_without_execution(self):
        starts, stops = self.fixtures()
        self.assertEqual(classify_callbacks(starts, stops), (tuple(starts), tuple(stops)))

    def test_original_guard_rejects_three_append_registered_families(self):
        old = {p[0] for p in PAIRS[:3]}
        starts, _ = self.fixtures()
        rejected = [c.__module__ for c in starts if c.__module__ not in old]
        self.assertEqual(rejected, [p[0] for p in PAIRS[3:]])

    def test_unknown_same_module_callback_is_rejected_before_execution(self):
        starts, stops = self.fixtures()
        starts.append(callback(PAIRS[0][0], PAIRS[0][1], "unreviewed_task"))
        with self.assertRaisesRegex(RuntimeError, "unclassified"):
            classify_callbacks(starts, stops)

    def test_unknown_shutdown_and_missing_pair_and_duplicate_are_rejected(self):
        starts, stops = self.fixtures()
        for a, b in [(starts, stops + [callback("unknown", "router", "stop")]),
                     (starts, stops[:-1]), (starts + [starts[0]], stops)]:
            with self.subTest(start_count=len(a), stop_count=len(b)), self.assertRaises(RuntimeError):
                classify_callbacks(a, b)

    def test_registered_names_match_actual_source_and_have_no_inline_index_write(self):
        root = Path(__file__).resolve().parents[1]
        for module, outer, start, stop in PAIRS:
            tree = ast.parse((root / (module.replace(".", "/") + ".py")).read_text(encoding="utf-8"))
            factory = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == outer)
            for name, event in [(start, "startup"), (stop, "shutdown")]:
                fn = next(n for n in factory.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
                decorated = any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "on_event" and d.args[0].value == event for d in fn.decorator_list)
                appended = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "append" and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "on_" + event and n.args and isinstance(n.args[0], ast.Name) and n.args[0].id == name for n in ast.walk(factory))
                self.assertTrue(decorated or appended, (module, name, event))
                self.assertFalse(any(isinstance(n, ast.Attribute) and n.attr in {"create_index", "create_indexes"} for n in ast.walk(fn)))

if __name__ == "__main__":
    unittest.main()
