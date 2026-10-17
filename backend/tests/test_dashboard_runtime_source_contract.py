"""Source parity guards for the approved reducer transplant (no DB/network)."""
import ast
import asyncio
import copy
import hashlib
from pathlib import Path
import subprocess
import time
import unittest

import dashboard_cpu_budget as cpu

BASE = '0abf4517afd10e9e331fc10dbbca1c60b981c505'
ROOT = Path(__file__).resolve().parents[2]
PAIRS = [
    ('order_currency.py', 'summarize_orders_sar'),
    ('orders_db.py', 'orders_to_parsed'),
    ('dashboard_v2_ads_executive.py', 'build_salla_ads_executive_breakdown'),
    ('product_catalog_cost_resolution.py', 'index_current_catalog_products'),
    ('dashboard_v2_routes.py', 'build_mezan_v2_product_cost'),
]


def function(text, name):
    return next(n for n in ast.parse(text).body if getattr(n, 'name', None) == name)


class RemoveCheckpoints(ast.NodeTransformer):
    """Remove only the explicitly approved scheduling scaffolding."""
    def visit_ImportFrom(self, node):
        if node.module in {'dashboard_cpu_budget', 'order_currency'}:
            return None
        return node

    def visit_Assign(self, node):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'budget':
            assert isinstance(node.value, ast.Call) and node.value.func.id == 'CPUWorkBudget'
            return None
        return self.generic_visit(node)

    def visit_Expr(self, node):
        if isinstance(node.value, ast.Await):
            assert ast.unparse(node.value.value) in {'budget.checkpoint()', 'checkpoint()'}
            return None
        return self.generic_visit(node)

    def visit_Await(self, node):
        call = node.value
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
            if call.func.id == 'summarize_orders_sar_cooperative':
                assert len(call.keywords) == 1 and call.keywords[0].arg == 'checkpoint'
                call.func.id, call.keywords = 'summarize_orders_sar', []
                return call
            if call.func.id == 'index_current_catalog_products_cooperative':
                call.func.id = '_index_products'
                return call
        return self.generic_visit(node)


class SourceContract(unittest.TestCase):
    def test_reducers_and_unchanged_shared_apis(self):
        for file, name in PAIRS:
            with self.subTest(function=name):
                baseline = subprocess.check_output(['git', 'show', BASE + ':backend/' + file], cwd=ROOT, text=True, encoding='utf8')
                current = (ROOT/'backend'/file).read_text(encoding='utf8')
                before = function(baseline, name)
                self.assertEqual(ast.dump(before), ast.dump(function(current, name)))
                adopted = RemoveCheckpoints().visit(copy.deepcopy(function(current, name+'_cooperative')))
                adopted.name = name
                if name == 'summarize_orders_sar':
                    self.assertEqual([a.arg for a in adopted.args.kwonlyargs], ['checkpoint'])
                    adopted.args.kwonlyargs, adopted.args.kw_defaults = [], []
                if isinstance(before, ast.FunctionDef):
                    adopted = ast.FunctionDef(**vars(adopted))
                self.assertEqual(ast.dump(before), ast.dump(adopted))

    def test_governor_and_data_access_allow_only_reviewed_telemetry(self):
        for file in ('resource_governor.py', 'mezan_profit_engine.py', 'sold_products_report_v2.py'):
            path = ROOT/'backend'/file
            if not path.exists():
                self.fail('Expected protected source is missing: '+file)
            before = subprocess.check_output(['git', 'show', BASE+':backend/'+file], cwd=ROOT)
            current = path.read_bytes().replace(b'\r\n', b'\n')
            if file == 'resource_governor.py':
                # PR1313 telemetry-only follow-up. Do not remove this guard or
                # silently accept policy changes: exact reviewed file, plus
                # live wait/cancellation/release contracts in test_phase1_*.
                self.assertIn(hashlib.sha256(current).hexdigest(), {
                    hashlib.sha256(before.replace(b'\r\n', b'\n')).hexdigest(),
                    'cc94fc8faeb0f811735f22423c7e6e501df1718faec5974904ec1eefe6e5a659',
                })
            else:
                self.assertEqual(before.replace(b'\r\n', b'\n'), current)

    def test_fixed_budgets(self):
        self.assertEqual((cpu.PARSER_BUDGET_MS, cpu.COST_PROFIT_BUDGET_MS,
                          cpu.ADVERTISING_BUDGET_MS, cpu.PRODUCT_INDEX_BUDGET_MS), (5, 5, 1, 5))

    def test_separate_product_cost_summary_endpoint_is_unchanged(self):
        before = subprocess.check_output(['git', 'show', BASE+':backend/dashboard_v2_routes.py'], cwd=ROOT, text=True, encoding='utf8')
        after = (ROOT/'backend/dashboard_v2_routes.py').read_text(encoding='utf8')
        def endpoint(text):
            factory = function(text, 'make_dashboard_v2_router')
            return next(n for n in factory.body if getattr(n, 'name', None) == 'product_cost_summary')
        self.assertEqual(ast.dump(endpoint(before)), ast.dump(endpoint(after)))


class SchedulingContract(unittest.IsolatedAsyncioTestCase):
    async def test_exhausted_budget_yields_and_resets(self):
        budget = cpu.CPUWorkBudget(5)
        budget.cpu = time.thread_time()-1
        seen = []
        asyncio.get_running_loop().call_soon(seen.append, 'interactive')
        await budget.checkpoint()
        self.assertEqual(seen, ['interactive'])
        self.assertLess(time.thread_time()-budget.cpu, .005)

    async def test_unexhausted_budget_does_not_yield(self):
        budget = cpu.CPUWorkBudget(5)
        budget.cpu = time.thread_time()+100
        seen = []
        asyncio.get_running_loop().call_soon(seen.append, 'interactive')
        await budget.checkpoint()
        self.assertEqual(seen, [])

    async def test_cancellation_does_not_complete_partial_work(self):
        budget = cpu.CPUWorkBudget(5)
        budget.cpu = time.thread_time()-1
        completed, errors = [], []
        loop = asyncio.get_running_loop()
        original = loop.get_exception_handler()
        loop.set_exception_handler(lambda loop, context: errors.append(context))
        async def work():
            await budget.checkpoint()
            completed.append(True)
        try:
            task = asyncio.create_task(work())
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0)
            self.assertEqual(completed, [])
            self.assertEqual(errors, [])
        finally:
            loop.set_exception_handler(original)

    async def test_budget_state_is_request_local(self):
        a, b = cpu.CPUWorkBudget(5), cpu.CPUWorkBudget(5)
        unchanged = b.cpu
        a.cpu = time.thread_time()-1
        await a.checkpoint()
        self.assertEqual(b.cpu, unchanged)


if __name__ == '__main__':
    unittest.main()
