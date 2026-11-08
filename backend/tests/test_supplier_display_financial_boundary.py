"""Pin the reviewed financial path, including Build44 recovery metadata.

Build44 adds attempt/fence checks and commit identity to close_session, and
excludes those transport fields from the historic native financial payload hash.
Close now checks earlier invoices for the same piece and charge component
before posting. The transactional duplicate tests cover this pinned change.
The remaining posting/integrity/display functions stay pinned independently.
accounting_atomic is pinned to current Production 8c9ef8cd, unchanged here.

Employee invoice approval scope (2026-11-08): close now creates immutable
catalog-price proposals instead of immediately applying shared prices. The
invoice/ledger amounts, writer and integrity paths are unchanged. Invoice-local
service scope and per-piece customer-option conditions are preserved by the
builder, display grouping and service-completion snapshot. The four function
hashes below were reviewed against 0241e162 and advanced for those changes;
the display key and all four financial file hashes remain unchanged. Behavioral
coverage is in test_employee_price_approval and test_supplier_invoice_local_services.
"""
import ast, hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
FUNCTION_HASHES = {'_supplier_invoice_display_line_key': '64779fc542f11dd3c351df68c6efc5f57db88ea4c948f3e937dafe28a27aa9b1', 'group_supplier_invoice_lines_for_display': '3ba57d13fd804a74f5c8ef8ef822beab6bd61d56eb2d7c4d686ebfcb68550e47', 'build_supplier_receiving_invoice': 'dcc4e40debe5e3a0ccd521705e2031fe9028bd32ae6dcd4acccfb16d3c4376ae', 'supplier_service_completion_update': '0fc57132ca37647b56633e7386c537ada7531e735cb6d1770eecac390256f87d', 'close_session': 'b5f7d79f5ea3b923d704f06352c960d145754f724f42a3e0c755239cd3c3a35a'}
FILE_HASHES = {'backend/supplier_native_invoice_v2.py': '7e1bb858ff05204343b73eea742097233006a80d43acc26976a507c51e37aff5', 'backend/supplier_invoice_integrity.py': 'b260f64f2d498c90f0f054303032aef931e9b70a49b22fb8b2f27b5fd306e91e', 'backend/supplier_receiving_read_scope.py': 'e9480d68245aea3fb5a5dff5d053e4c16771968a0a8ad8d6d5c412a2b4ace8a4', 'backend/accounting_atomic.py': '3bac96bdf253fc4a58f43bfa05a9343c0f1351be4563e047736af1bb458e189d'}

def test_approved_close_posting_and_service_linkage_are_unchanged():
    source=(ROOT/"backend/supplier_receiving_routes.py").read_text(encoding="utf-8")
    tree=ast.parse(source)
    actual = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name not in FUNCTION_HASHES:
            continue
        segment = ast.get_source_segment(source, node)
        if node.name == "close_session":
            # PR #1320 only moves repeated index DDL behind the router's
            # successful-once gate. Retain the ORIGINAL financial source hash:
            # no other authorization, transaction, posting or linkage change is
            # normalized. Dedicated index tests exercise failure/retry/concurrency.
            current = "        await ensure_indexes_once()"
            original = "        await ensure_supplier_receiving_indexes(db)"
            assert segment.count(current) == 1
            assert original not in segment
            segment = segment.replace(current, original, 1)
        actual[node.name] = hashlib.sha256(segment.encode()).hexdigest()
    assert actual==FUNCTION_HASHES
    for path,expected in FILE_HASHES.items():
        assert hashlib.sha256((ROOT/path).read_text(encoding="utf-8").encode()).hexdigest()==expected,path
