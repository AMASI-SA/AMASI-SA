"""Synthetic Mezan projection checks; no customer data or external I/O."""
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from backend.order_engine.whatsapp_stage_projection import stage_fact, StageNotVerified

NOW = 1800000000
DATE = datetime.fromtimestamp(NOW-90,timezone.utc)

def order(status="قيد التنفيذ", phone="0550000001", dt=DATE):
    return SimpleNamespace(order_number="301153281", status_native=status,
                           customer=SimpleNamespace(mobile=phone), engine_updated_at=dt)

def check(value=None, **kwargs):
    return stage_fact(order() if value is None else value, order_number="301153281",
                      verified_sender=kwargs.pop("sender","+966550000001"),
                      source_checked_at=kwargs.pop("checked",NOW-2),now=NOW,**kwargs)

class ProjectionTest(unittest.TestCase):
    def test_only_minimal_stage(self):
        result=check()
        self.assertEqual(result["stage"],"preparing")
        for key in ("order_number","customer","mobile","address","payment","tracking_url","items"):
            self.assertNotIn(key, result)
        self.assertNotIn("301153281",str(result))

    def test_wrong_customer_or_order_rejected(self):
        for value in (order(phone="0550000002"), SimpleNamespace(order_number="other",
                     customer=SimpleNamespace(mobile="0550000001"),status_native="تم التنفيذ",
                     engine_updated_at=DATE)):
            with self.assertRaisesRegex(StageNotVerified,"verification_required"):
                check(value)
        with self.assertRaises(StageNotVerified):
            check(sender="0550000002")

    def test_prepared_never_claim_shipped(self):
        result=check(order(status="تم التنفيذ"))
        self.assertEqual(result["stage"],"prepared")
        self.assertFalse(result["has_verified_tracking"])

    def test_cancel_request_not_cancelled(self):
        self.assertEqual(check(order(status="طلب إلغاء"))["stage"],"cancellation_requested")

    def test_stale_and_unknown_rejected(self):
        for value in (order(dt=DATE-timedelta(days=3)),order(status="pending"),
                      order(dt=None),order(dt=DATE+timedelta(days=2))):
            with self.assertRaises(StageNotVerified):
                check(value)
        with self.assertRaises(StageNotVerified):
            check(checked=NOW-90000)

    def test_old_terminal_is_explicitly_last_known(self):
        value=order(status="تم التوصيل",dt=DATE-timedelta(days=4))
        self.assertEqual(check(value)["stage"],"delivered")

if __name__=="__main__":
    unittest.main()
