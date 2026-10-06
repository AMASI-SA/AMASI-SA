import asyncio, types, unittest
from unittest.mock import patch
from p1253_diagnostic_contract import CANDIDATE, schedule, smoke_workloads
import p1253_paired_linux as paired

class Reads:
    def __init__(self): self.commands=self.documents=self.query_us=0;self.failures=[]
    def succeeded(self,event): pass

class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self,state):
        events=[]
        class Client:
            def __init__(self,*args,**kwargs):self.reads=kwargs["event_listeners"][0]
            def __getitem__(self,name):return self
            def close(self):events.append("close")
        def endpoint(db,mode):
            async def call(**kwargs):
                events.append(kwargs["user"]["id"])
                db.reads.commands+=1;db.reads.documents+=1
                await asyncio.sleep(0)
                return {"totals":{"amount":50}}
            return call
        class Probe:
            def install(self):events.append("probe")
            def finish(self):return {"spill_cleanup_complete":True}
        def reset():events.append("reset");return {"VmRSS":10,"VmHWM":10}
        def peak():events.append("peak");return {"VmRSS":20,"VmHWM":25}
        def signature(value):events.append("signature");return "test-signature"
        bench=types.SimpleNamespace(Reads=Reads,AsyncIOMotorClient=Client,URI="mongodb://127.0.0.1:27261",
            v2_endpoint=endpoint,SpillProbe=Probe,jsonable_encoder=lambda x:x,signature=signature,financial_values=lambda x:x)
        workload=next(w for w in smoke_workloads() if len(w["tenants"])==4)
        row=next(r for r in schedule(1,[workload]) if r["source"]==CANDIDATE and r["state"]==state)
        with patch.object(paired,"reset_rss_window",reset),patch.object(paired,"rss_status",peak),patch.object(paired,"source_identity",return_value={"head":CANDIDATE}):
            result=await paired.measure(bench,types.SimpleNamespace(database="test",target="candidate"),row)
        self.assertEqual([r["tenant"] for r in result["requests"]],["tenant-0","tenant-1","tenant-2","tenant-3"])
        self.assertEqual(result["mongo_read_commands"],4)
        self.assertEqual(result["rss_peak_bytes"],25)
        self.assertLess(events.index("reset"),events.index("peak"))
        self.assertLess(events.index("peak"),events.index("signature"))
        self.assertEqual(result["warmup"]["commands"],4 if state=="warm" else 0)
        self.assertEqual(events.count("tenant-0"),2 if state=="warm" else 1)
        return result
    async def test_cold_no_warmup_and_measured_counters(self):await self.exercise("cold")
    async def test_warm_once_excluded_from_measured_counters(self):await self.exercise("warm")
    async def test_profile_rejected(self):
        with self.assertRaises(ValueError):await paired.measure(None,None,{"profile":True})
