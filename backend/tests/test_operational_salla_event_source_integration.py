"""Owner-scoped financial source health gate must not trust daily partial alone."""
import asyncio
import os
import sys
import types
import unittest
from unittest.mock import patch

from operational_payment_refresh_health_gate import require_current_refresh_health

ASOF="2026-11-24T20:27:00+03:00"
AT="2026-11-24T21:44:00+03:00"


def salla_daily():
    return {
        "schedule_version":"daily_10_riyadh_update_v1", "status":"partial",
        "completed_slot":"2026-11-24",
        "last_result":{
            "errors_count":0,"incomplete_count":0,
            "truncated":False,"coverage_complete":False,
            "coverage_reason":"provider_modified_since_filter_unavailable",
            "discovery":"recent_creations_and_verified_local_source_activity",
        },
    }


class _MongoTimeout:
    def __init__(self,secs): self.secs=secs
    def __enter__(self): return self
    def __exit__(self,*args): return False


def scope(status="partial"):
    health={
        "owner_id":"merchant1", "last_success_at":ASOF,
        "last_attempt":{"outcome":"success",
                        "started_at":"2026-11-24T20:20:00+03:00"},
    }
    schedule=salla_daily()
    schedule["status"]=status
    calls=[]
    class Db:
        def __getitem__(self,name):
            class Collection:
                async def find_one(self,query,fields,**options):
                    calls.append((name,query,fields,options))
                    return health if name=="operational_balance_refresh_health_v1" else schedule
            return Collection()
    store=types.ModuleType("operational_balance_store")
    store.fail=lambda code,*args: (_ for _ in ()).throw(ValueError(code))
    freshness=types.ModuleType("operational_balance_freshness")
    freshness.HEALTH="operational_balance_refresh_health_v1"
    native=types.ModuleType("operational_source_freshness")
    async def _salla(db,owner,at):
        return {"key":"salla","state":"failed","code":"salla_source_failed"}
    async def _safe(key, fn):
        return await fn()
    native._salla=_salla
    native._safe=_safe
    pymongo=types.ModuleType("pymongo")
    pymongo.timeout=_MongoTimeout
    return Db(),calls,{
        "operational_balance_store":store,
        "operational_balance_freshness":freshness,
        "operational_source_freshness":native,
        "pymongo":pymongo,
    }


class EventSourceGateIntegrationTests(unittest.TestCase):
    def run_gate(self, *, source_flag="0", proof=True, status="partial"):
        db,calls,modules=scope(status)
        with patch.dict(os.environ,{
            "OPERATIONAL_PAYMENT_EVENT_SCOPED_SOURCE_GATE_ENABLED":source_flag,
        }):
            with patch.dict(sys.modules,modules):
                asyncio.run(require_current_refresh_health(
                    db,"merchant1",{"owner_id":"merchant1","engine":{"as_of":ASOF}},
                    at=AT,event_proof_bound=proof))
        return calls

    def test_event_scope_off_never_bypasses_failed_salla_source(self):
        with self.assertRaisesRegex(ValueError,"gate_disabled"):
            self.run_gate(source_flag="0")

    def test_exact_new_event_proof_and_history_only_partial_are_allowed(self):
        calls=self.run_gate(source_flag="1")
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0][1]["owner_id"],"merchant1")
        self.assertEqual(calls[1][1],{"_id":"merchant1"})

    def test_source_proof_missing_is_refused(self):
        with self.assertRaisesRegex(ValueError,"payment_salla_source_unverified"):
            self.run_gate(source_flag="1",proof=False)

    def test_schedule_real_failure_is_refused(self):
        with self.assertRaisesRegex(ValueError,"daily_status_unverified"):
            self.run_gate(source_flag="1",status="failed")


if __name__=="__main__":
    unittest.main()
