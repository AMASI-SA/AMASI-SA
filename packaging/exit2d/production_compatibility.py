"""Post-merge shipping/fee contracts, with real libraries and synthetic fixtures."""
import os
import sys
sys.path[:0] = ['/opt/mezan', '/opt/mezan/backend', '/opt/mezan/backend/tests']
from independent_runtime import validate_before_import
validate_before_import('web')
os.environ['OPERATIONAL_TEST_MONGO_URI'] = 'mongodb://127.0.0.1:27017'
os.environ['MZ2_TEST_MONGO_URI'] = 'mongodb://127.0.0.1:27017'
import pytest
names = ['test_fulfillment_assignment_scope_exit2d.py', 'test_operational_balance_courier_acquisition.py', 'test_provider_fee_source_terms.py',
    'test_provider_fee_runtime_binding.py', 'test_bnpl_fee_completeness_boundaries.py',
    'test_preparation_supplier_dispatch.py', 'test_shipping_saved_label_print.py',
    'test_assembly_print_completion.py', 'test_amasi_shipping_compat.py']
raise SystemExit(pytest.main(['--noconftest', '--asyncio-mode=auto', '-p', 'no:cacheprovider', '-q', '--tb=short',
    *['/opt/mezan/backend/tests/' + name for name in names]]))
