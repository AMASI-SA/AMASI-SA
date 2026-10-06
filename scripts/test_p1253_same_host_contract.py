import unittest
from p1253_diagnostic_contract import BASELINE,CANDIDATE
from p1253_same_host_capacity import paired_rows

class SameHostContract(unittest.TestCase):
    def test_exact_twenty_samples_alternating_pairs(self):
        rows=paired_rows()
        self.assertEqual(len(rows),20)
        for i in range(10):
            pair=rows[i*2:i*2+2]
            self.assertEqual([r["source"] for r in pair],[BASELINE,CANDIDATE] if i%2==0 else [CANDIDATE,BASELINE])
            for r in pair:
                self.assertEqual(r["repetition"],i)
                self.assertEqual((r["state"],r["warmup_count"],r["profile"]),("warm",1,False))
                self.assertEqual(r["workload"],{"name":"100k-A-capacity","count":100000,"tenants":["tenant-0"],"concurrency":1,"kind":"single"})
