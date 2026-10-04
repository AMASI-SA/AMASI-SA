"""Freeze the approved #1250/#1251 financial path while presentation changes."""
import ast, hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
FUNCTION_HASHES = {'_supplier_invoice_display_line_key': '1abfe35b1be503a6c3bd90d943f565e9138ba9b6fb4b082011cd2bbacbcecab9', 'group_supplier_invoice_lines_for_display': '2a0745f81a4cf51bb920bcf32f0e7eb4e7d191cd2e900ae104ae6088cc13ee5e', 'build_supplier_receiving_invoice': '4502cd128e49354fbe82d5882b96e90064e4b8eca48c8c0228fe0d26c0704ba9', 'supplier_service_completion_update': 'fb6494ecbb6eebdd706e1e1434aa469fb198bea8c0452e99b00c0445818b9ccc', 'close_session': 'dc64aa0ce76a42e2aed477107395aa05b0408f7a5b71e6acd1fb3340e975e8e2'}
FILE_HASHES = {'backend/supplier_native_invoice_v2.py': '55191669db57265a54dfbf06cc9374fd147d7bffaf0406cb6925e765d9a3a152', 'backend/supplier_invoice_integrity.py': 'b260f64f2d498c90f0f054303032aef931e9b70a49b22fb8b2f27b5fd306e91e', 'backend/supplier_receiving_read_scope.py': 'e9480d68245aea3fb5a5dff5d053e4c16771968a0a8ad8d6d5c412a2b4ace8a4', 'backend/accounting_atomic.py': '46a4aaf279d81c99db78234e0e5e99c2ac591c68eea82f9294e4501f2d80cc78'}

def test_approved_close_posting_and_service_linkage_are_unchanged():
    tree=ast.parse((ROOT/"backend/supplier_receiving_routes.py").read_text(encoding="utf-8"))
    actual={n.name:hashlib.sha256(ast.dump(n,include_attributes=False).encode()).hexdigest() for n in ast.walk(tree)
       if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in FUNCTION_HASHES}
    assert actual==FUNCTION_HASHES
    for path,expected in FILE_HASHES.items():
        assert hashlib.sha256((ROOT/path).read_text(encoding="utf-8").encode()).hexdigest()==expected,path
