"""Freeze the approved #1250/#1251 financial path while presentation changes."""
import ast, hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
FUNCTION_HASHES = {'_supplier_invoice_display_line_key': '64779fc542f11dd3c351df68c6efc5f57db88ea4c948f3e937dafe28a27aa9b1', 'group_supplier_invoice_lines_for_display': 'edeb2b45b1439694e7d840ad78cb8b21bd7b823a698088c375a8fa1825111885', 'build_supplier_receiving_invoice': 'f60f25b5b4d17c8bf8d3371105359cbde12d5693f62dbb986f5d20a424188549', 'supplier_service_completion_update': '3326f9585d0e6dc18e171d74f78d8ef076421fcac4050680e06ff45f8d555b80', 'close_session': '3c67e5d94ef560b4791c54cbe805d288cc18637993d1c6b7850b3dfed3ddda40'}
FILE_HASHES = {'backend/supplier_native_invoice_v2.py': '55191669db57265a54dfbf06cc9374fd147d7bffaf0406cb6925e765d9a3a152', 'backend/supplier_invoice_integrity.py': 'b260f64f2d498c90f0f054303032aef931e9b70a49b22fb8b2f27b5fd306e91e', 'backend/supplier_receiving_read_scope.py': 'e9480d68245aea3fb5a5dff5d053e4c16771968a0a8ad8d6d5c412a2b4ace8a4', 'backend/accounting_atomic.py': '46a4aaf279d81c99db78234e0e5e99c2ac591c68eea82f9294e4501f2d80cc78'}

def test_approved_close_posting_and_service_linkage_are_unchanged():
    source=(ROOT/"backend/supplier_receiving_routes.py").read_text(encoding="utf-8")
    tree=ast.parse(source)
    actual={n.name:hashlib.sha256(ast.get_source_segment(source,n).encode()).hexdigest() for n in ast.walk(tree)
       if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in FUNCTION_HASHES}
    assert actual==FUNCTION_HASHES
    for path,expected in FILE_HASHES.items():
        assert hashlib.sha256((ROOT/path).read_text(encoding="utf-8").encode()).hexdigest()==expected,path
