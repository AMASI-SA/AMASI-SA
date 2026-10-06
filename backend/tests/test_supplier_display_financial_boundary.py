"""Freeze the approved #1250/#1251 financial path while presentation changes."""
import ast, hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
FUNCTION_HASHES = {'_supplier_invoice_display_line_key': '64779fc542f11dd3c351df68c6efc5f57db88ea4c948f3e937dafe28a27aa9b1', 'group_supplier_invoice_lines_for_display': 'edeb2b45b1439694e7d840ad78cb8b21bd7b823a698088c375a8fa1825111885', 'build_supplier_receiving_invoice': 'f60f25b5b4d17c8bf8d3371105359cbde12d5693f62dbb986f5d20a424188549', 'supplier_service_completion_update': '3326f9585d0e6dc18e171d74f78d8ef076421fcac4050680e06ff45f8d555b80', 'close_session': '3c67e5d94ef560b4791c54cbe805d288cc18637993d1c6b7850b3dfed3ddda40'}
FILE_HASHES = {'backend/supplier_native_invoice_v2.py': '55191669db57265a54dfbf06cc9374fd147d7bffaf0406cb6925e765d9a3a152', 'backend/supplier_invoice_integrity.py': 'b260f64f2d498c90f0f054303032aef931e9b70a49b22fb8b2f27b5fd306e91e', 'backend/supplier_receiving_read_scope.py': 'e9480d68245aea3fb5a5dff5d053e4c16771968a0a8ad8d6d5c412a2b4ace8a4', 'backend/accounting_atomic.py': '46a4aaf279d81c99db78234e0e5e99c2ac591c68eea82f9294e4501f2d80cc78'}


# PR1 adds only the approved lifecycle preflight/claim and piece revalidation.
# Pin its complete source too; reconstruct and verify the ORIGINAL close hash
# below so approval of a guard cannot silently approve a financial-body change.
PR1_GUARDED_CLOSE_HASH = "3b176089ef34493764d96698e2359afa69d5bd2a04aac72cf01c79165e03b3a7"


def _approved_close_without_pr1_guards(source, node):
    guarded = ast.get_source_segment(source, node)
    assert hashlib.sha256(guarded.encode()).hexdigest() == PR1_GUARDED_CLOSE_HASH
    start = next(i for i, child in enumerate(node.body)
                 if isinstance(child, ast.ImportFrom) and child.module == "fulfillment_lifecycle")
    guards = node.body[start:]
    assert len(guards) == 5
    assert isinstance(guards[1], ast.ImportFrom) and guards[1].module == "fulfillment_lifecycle_execution"
    assert isinstance(guards[2], ast.Assign) and guards[2].targets[0].id == "lifecycle_close_targets"
    assert isinstance(guards[3], ast.If) and isinstance(guards[4], ast.AsyncWith)
    claim = guards[4]
    finalize = next(child for child in claim.body
                    if isinstance(child, ast.AsyncFunctionDef) and child.name == "finalize")
    revalidations = [child for child in finalize.body if isinstance(child, ast.If)
                    and "lifecycle_close_targets" in ast.unparse(child.test)]
    assert len(revalidations) == 1
    revalidation = revalidations[0]
    restored = []
    for number, line in enumerate(source.splitlines(keepends=True), 1):
        if not node.lineno <= number <= node.end_lineno:
            continue
        if guards[0].lineno <= number < claim.body[0].lineno:
            continue
        if revalidation.lineno <= number <= revalidation.end_lineno:
            continue
        if claim.body[0].lineno <= number <= claim.end_lineno and line.strip():
            assert line.startswith("    ")
            line = line[4:]
        if number == node.lineno:
            line = line[node.col_offset:]
        restored.append(line)
    return "".join(restored).rstrip("\r\n")


def test_approved_close_posting_and_service_linkage_are_unchanged():
    source=(ROOT/"backend/supplier_receiving_routes.py").read_text(encoding="utf-8")
    tree=ast.parse(source)
    actual={n.name:hashlib.sha256((_approved_close_without_pr1_guards(source,n) if n.name == "close_session"
                                else ast.get_source_segment(source,n)).encode()).hexdigest() for n in ast.walk(tree)
       if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in FUNCTION_HASHES}
    assert actual==FUNCTION_HASHES
    for path,expected in FILE_HASHES.items():
        assert hashlib.sha256((ROOT/path).read_text(encoding="utf-8").encode()).hexdigest()==expected,path
