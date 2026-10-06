import collections
import unittest
from p1253_diagnostic_contract import BASELINE,CANDIDATE,request_ids
from p1253_multi_workload_pilot import matrix_rows,select_mode,plan_budget

class MultiPilotContract(unittest.TestCase):
    def test_pilot_exact_160_balanced_independent_samples(self):
        rows=matrix_rows('pilot')
        self.assertEqual(len(rows),160)
        counts=collections.Counter((r['source'],r['state'],len(r['workload']['tenants'])) for r in rows)
        self.assertEqual(len(counts),16)
        self.assertEqual(set(counts.values()),{10})
        for i in range(0,len(rows),2):
            pair=rows[i:i+2];rep=pair[0]['repetition']
            self.assertEqual([r['source'] for r in pair],[BASELINE,CANDIDATE] if rep%2==0 else [CANDIDATE,BASELINE])
            for r in pair:
                n=r['workload']['concurrency']
                self.assertEqual(request_ids(r['workload']),[f'tenant-{j}' for j in range(n)])
                self.assertEqual(r['workload']['count'],100000)
                self.assertFalse(r['profile'])
                self.assertEqual(r['warmup_count'],0 if r['state']=='cold' else 1)

    def test_smoke_same_four_workloads_states_and_order_small_only(self):
        rows=matrix_rows('smoke')
        self.assertEqual(len(rows),32)
        self.assertEqual({r['workload']['count'] for r in rows},{12})
        self.assertEqual({len(r['workload']['tenants']) for r in rows},{1,2,3,4})
        self.assertEqual({r['state'] for r in rows},{'cold','warm'})
        self.assertEqual({r['repetition'] for r in rows},{0,1})

    def test_push_cannot_start_pilot(self):
        self.assertEqual(select_mode('push','pilot','P1253_PILOT_160_ONLY'),'smoke')
        self.assertEqual(select_mode('workflow_dispatch','smoke',''),'smoke')
        with self.assertRaises(ValueError):select_mode('workflow_dispatch','pilot','')
        self.assertEqual(select_mode('workflow_dispatch','pilot','P1253_PILOT_160_ONLY'),'pilot')
        with self.assertRaises(ValueError):select_mode('workflow_dispatch','full','P1253_PILOT_160_ONLY')
        with self.assertRaises(ValueError):select_mode('pull_request','pilot','P1253_PILOT_160_ONLY')

    def test_no_arbitrary_matrix_and_budget_fail_closed(self):
        with self.assertRaises(ValueError):matrix_rows('full')
        budget=plan_budget('pilot')
        self.assertEqual(budget['measured_windows'],160)
        self.assertEqual(budget['warmup_groups'],80)
        self.assertLess(budget['planning_seconds_with_margin'],340*60)
        self.assertFalse(budget['measured_multi_tenant_estimate'])
        self.assertFalse(plan_budget('pilot',available_seconds=60)['fits_available'])

if __name__=='__main__':unittest.main()
