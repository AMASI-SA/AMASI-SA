"""No sensitive data may survive persisted refresh-failure diagnostics."""
import copy
import unittest
from operational_refresh_failure_trace import safe_failure_trace, MAX_STAGES, MAX_READS


def cursor(**changes):
    row = {"collection": "unified_orders", "outcome": "cancelled",
           "cursor_kind": "motor", "rows_retained": 640,
           "last_checkpoint_rows": 512, "queue_ms": 1450.2,
           "budget_ms": 15000.0, "first_row_ms": 3900.2,
           "last_checkpoint_ms": 11500.1,
           "read_elapsed_ms": 12400.2,
           "cancel_join_ms": 120.2, "close_ms": 45.2}
    row.update(changes)
    return row


class TimeoutTraceTests(unittest.TestCase):
    def test_exposes_only_known_stage_duration_and_primary_cursor(self):
        raw = {"code": "operational_refresh_timeout",
               "stage_timings": [
                   {"stage": "source_primary_reads", "outcome": "cancelled",
                    "duration_ms": 84520.13, "owner_id": "owner-secret",
                    "orders": [{"mobile": "secret"}]},
                   {"stage": "orders_cursor_read", "outcome": "cancelled",
                    "duration_ms": 13000.5}],
               "primary_orders_reads": [cursor(order_id="sensitive-order")],
               "sql": "raw sensitive data", "exception": "SECRET"}
        unchanged = copy.deepcopy(raw)
        result = safe_failure_trace(raw)
        self.assertEqual(raw, unchanged)
        self.assertEqual(len(result["stage_timings"]), 2)
        self.assertEqual(result["stage_timings"][0]["stage"], "source_primary_reads")
        self.assertEqual(result["primary_orders_reads"][0]["rows_retained"], 640)
        self.assertNotIn("secret", repr(result).lower())
        self.assertNotIn("order_id", repr(result))
        self.assertEqual(set(result), {"stage_timings", "primary_orders_reads"})

    def test_never_echo_untrusted_label_or_raw_error(self):
        output = safe_failure_trace({
            "stage_timings": [
                {"stage": "mobile_0555555", "duration_ms": 80, "outcome": "failed"},
                {"stage": ["malformed"], "duration_ms": 80, "outcome": "failed"},
                {"stage": "source_orders", "duration_ms": "80", "outcome": "failed"},
                {"stage": "source_orders", "duration_ms": float("nan"), "outcome": "failed"},
            ],
            "primary_orders_reads": [
                cursor(cursor_kind={"evil": True}), cursor(queue_ms=True),
                cursor(collection="customer_transactions"),
            ],
        })
        self.assertEqual(output, {"stage_timings": [], "primary_orders_reads": []})

    def test_bounded_trace_preserves_latest_20_and_4(self):
        output = safe_failure_trace({
            "stage_timings": [
                {"stage": "source_orders", "duration_ms": n, "outcome": "complete"}
                for n in range(80)],
            "primary_orders_reads": [cursor(rows_retained=n, last_checkpoint_rows=0)
                                     for n in range(25)]})
        self.assertEqual(len(output["stage_timings"]), MAX_STAGES)
        self.assertEqual(len(output["primary_orders_reads"]), MAX_READS)
        self.assertEqual(output["stage_timings"][0]["duration_ms"], 60)
        self.assertEqual(output["primary_orders_reads"][-1]["rows_retained"], 24)

    def test_future_invalid_time_and_identity_never_passes(self):
        self.assertEqual(safe_failure_trace({"stage_timings": [{"stage": "source_orders",
                         "duration_ms": -1, "outcome": "failed"}],
                         "primary_orders_reads": [cursor(rows_retained=2,
                                                        last_checkpoint_rows=3)]}),
                         {"stage_timings": [], "primary_orders_reads": []})

    def test_missing_diagnostics_are_empty(self):
        self.assertEqual(safe_failure_trace(None),
                         {"stage_timings": [], "primary_orders_reads": []})
        self.assertEqual(safe_failure_trace(["not an error object"]),
                         {"stage_timings": [], "primary_orders_reads": []})


if __name__ == "__main__":
    unittest.main()
