import collections
import unittest
from p1253_diagnostic_contract import *

class Contract(unittest.TestCase):
    def test_equal_source_samples(self):
        rows=list(schedule(4)); counts=collections.Counter((r['workload']['name'],r['state'],r['source']) for r in rows)
        self.assertTrue(all(v==4 for v in counts.values()))
        for w in workloads():
            for state in ('cold','warm'):
                pair=[r['source'] for r in rows if r['workload']==w and r['state']==state]
                self.assertEqual(pair,[BASELINE,CANDIDATE,CANDIDATE,BASELINE]*2)
    def test_independent_scaling_and_stable_A(self):
        ws=[w for w in workloads() if w['count']==100000 and w['kind'] in ('single','independent')]
        self.assertEqual([len(w['tenants']) for w in ws],[1,2,3,4])
        for w in ws:
            self.assertEqual(request_ids(w)[0],'tenant-0')
            self.assertEqual(len(set(request_ids(w))),w['concurrency'])
    def test_same_key_separate(self):
        for w in workloads():
            if w['kind']=='same-key': self.assertEqual(len(set(request_ids(w))),1)
    def test_cold_warm_contract(self):
        for r in schedule(2):
            self.assertEqual(r['warmup_count'],0 if r['state']=='cold' else 1)
            self.assertFalse(r['profile'])
    def rows(self,n):
        return [dict(source=BASELINE,workload='A',state='cold',tenant='tenant-0',profile=False,repetition=i,ok=True,endpoint_seconds=i+1) for i in range(n)]
    def test_small_sample_tail_not_reported(self):
        r=summarize(self.rows(10)); self.assertIsNone(r['p95']); self.assertIsNone(r['p99'])
    def test_percentiles_same_population(self):
        r=summarize(self.rows(2000));self.assertEqual((r['p50'],r['p95'],r['p99']),(1000,1900,1980))
    def test_mixed_or_duplicate_samples_rejected(self):
        for field,value in [('source',CANDIDATE),('state','warm'),('tenant','tenant-1'),('profile',True),('repetition',0)]:
            rows=self.rows(2);rows[1][field]=value
            with self.assertRaises(ValueError):summarize(rows)
    def test_failed_samples_not_silent_success(self):
        rows=self.rows(2);rows[0]['ok']=False;self.assertEqual(summarize(rows)['status'],'FAILURES_PRESENT')
if __name__=='__main__':unittest.main()
