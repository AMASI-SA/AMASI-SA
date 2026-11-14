"""Run the new complexity regression against J's old reader in memory only."""
import ast
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
REPO = Path('/workspace/scratch/019fa8a18b3d/mezan-source-collection-fix')
BASE = '9c71435f4a19977c09f4331dee8fddbfa12a45eb'
RELATIVE = 'backend/operational_delivery_evidence.py'
sys.path[:0] = [str(REPO / 'backend'), str(REPO / 'backend/tests')]
raw = subprocess.check_output(['git', 'show', BASE + ':' + RELATIVE], cwd=REPO)
function = next(node for node in ast.parse(raw).body if isinstance(node, ast.FunctionDef)
                and node.name == 'recorded_courier_fee')
namespace = {'Decimal': Decimal}
# The filename lets the unchanged regression count this isolated function's
# line events. No original module or on-disk source is replaced.
exec(compile(ast.Module(body=[function], type_ignores=[]), str(REPO / RELATIVE), 'exec'), namespace)
legacy = namespace['recorded_courier_fee']
import operational_courier_fee_history as history
history.build_recorded_courier_fee_lookup = lambda value: lambda identity: legacy(value, identity)
print(json.dumps({'baseline_commit': BASE, 'baseline_source_sha256': hashlib.sha256(raw).hexdigest(),
                  'patch_scope': 'child-process memory only', 'repository_writes': False}), flush=True)
import pytest
raise SystemExit(pytest.main(['--noconftest', '-q', '-p', 'no:cacheprovider', '--asyncio-mode=auto',
    '--tb=short', '--junitxml=' + str(ROOT / 'fee-index-negative.xml'),
    'backend/tests/test_operational_balance_recorded_courier_fee_index.py::test_history_work_scales_with_input_instead_of_orders_times_history']))
