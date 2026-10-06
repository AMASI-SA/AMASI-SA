import unittest
from p1253_diagnostic_contract import CANDIDATE
from p1253_single_capacity import capacity_row

class CapacityContract(unittest.TestCase):
    def test_only_candidate_A_warm_once(self):
        row=capacity_row()
        self.assertEqual(row["source"],CANDIDATE)
        self.assertEqual(row["workload"]["tenants"],["tenant-0"])
        self.assertEqual(row["workload"]["count"],100000)
        self.assertEqual(row["workload"]["concurrency"],1)
        self.assertEqual((row["state"],row["warmup_count"],row["repetition"],row["profile"]),("warm",1,0,False))
