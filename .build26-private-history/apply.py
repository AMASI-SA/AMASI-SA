from pathlib import Path
import os
import subprocess

BASE = '377ff1bef25cd977e09e759f60a69e2a96c05588'
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip() == BASE

def source(path, sha):
    assert subprocess.check_output(['git', 'rev-parse', f'HEAD:{path}'], text=True).strip() == sha, path
    return Path(path).read_text()

def replace(text, old, new):
    assert text.count(old) == 1, ('anchor_count', text.count(old), old[:120])
    return text.replace(old, new, 1)

p = 'backend/supplier_receiving_routes.py'
s = source(p, '2e4a8eacfa771fbf4f9754f864c29e355a5a3e0e')
s = replace(s, 'from supplier_invoice_pdf import generate_supplier_invoice_pdf\n', 'from supplier_invoice_pdf import generate_supplier_invoice_pdf\nfrom supplier_invoice_history import register_invoice_history_routes\n')
s = replace(s, '    """Expose merchant-wide closed invoice history while keeping writes actor-owned."""\n    public = _public_session(row)', '    """Personal history must not expose another employee\'s invoice."""\n    if _text(row.get("opened_by")) != _text(context.get("actor_id")):\n        return None\n    public = _public_session(row)')
a = s.index('async def _supplier_invoice_for_viewer(')
b = s.index('\n\nasync def _supplier_invoice_for_actor(', a)
s = s[:a] + '''async def _supplier_invoice_for_viewer(
    db: Any,
    *,
    context: dict[str, Any],
    invoice_id: str,
    projection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Employees read only their own invoices, including PDF and evidence.

    Genuine owner administration remains available. Native employee identity
    is restored by _actor_context before this query is constructed.
    """
    query: dict[str, Any] = {"user_id": context["merchant_id"], "id": _text(invoice_id)}
    if not context["is_owner"]:
        if not _text(context.get("actor_id")):
            raise HTTPException(status_code=403, detail={"code": "supplier_invoice_actor_required"})
        query["supplier_approved_by"] = context["actor_id"]
    fields = dict(projection or {"_id": 0})
    if any(value == 1 for value in fields.values()):
        fields["supplier_approved_by"] = 1
    row = await db[SUPPLIER_INVOICES].find_one(query, fields)
    if not row or (not context["is_owner"] and _text(row.get("supplier_approved_by")) != context["actor_id"]):
        raise HTTPException(status_code=404, detail={"code": "supplier_invoice_not_found"})
    return row
''' + s[b:]
s = replace(s, '    @router.get("/catalog")\n', '    register_invoice_history_routes(router, db, current_user, _actor_context, _require_permission, RECEIVE_PERMISSION)\n\n    @router.get("/catalog")\n')
s = replace(s, '''        # An open receiving draft is employee-owned and must never be exposed as
        # another employee's editable session. Completed supplier invoices,
        # however, are store records and should be visible to every employee
        # who has access to the My Products supplier-invoice page.
''', '''        # My Products is personal: both drafts and closed invoice history
        # belong to the authenticated employee, not their colleagues.
''')
s = replace(s, '''                    "user_id": merchant_id,
                    "status": "closed",
                    "supplier_invoice.id": {"$exists": True, "$ne": ""},''', '''                    "user_id": merchant_id,
                    "opened_by": context["actor_id"],
                    "status": "closed",
                    "supplier_invoice.id": {"$exists": True, "$ne": ""},''')
s = replace(s, '.sort("closed_at", -1)', '.sort([("closed_at", -1), ("id", -1)])')
Path(p).write_text(s)

p = 'backend/tests/test_supplier_receiving.py'
s = source(p, '1f5f12f5d7948bc45b5dd745803b88650e8efc1a')
s = replace(s, 'test_supplier_invoice_read_is_store_wide_but_share_writes_stay_creator_owned', 'test_supplier_invoice_reads_and_share_writes_are_employee_owned')
a = s.index('    viewed = await _supplier_invoice_for_viewer(', s.index('async def test_supplier_invoice_reads'))
b = s.index('\n\ndef test_mobile_supplier_invoice_history', a)
s = s[:a] + '''    for reader in (_supplier_invoice_for_viewer, _supplier_invoice_for_actor):
        with pytest.raises(HTTPException) as exc:
            await reader(db, context=context, invoice_id="invoice-1")
        assert exc.value.status_code == 404
        assert collection.find_one.call_args.args[0]["supplier_approved_by"] == "employee-1"
    collection.find_one.return_value = {**invoice, "supplier_approved_by": "employee-1"}
    own = await _supplier_invoice_for_viewer(db, context=context, invoice_id="invoice-1")
    assert own["invoice_number"] == "SI-1"
    collection.find_one.return_value = invoice
    owner = await _supplier_invoice_for_viewer(db, context={**context, "is_owner": True}, invoice_id="invoice-1")
    assert owner["invoice_number"] == "SI-1"
''' + s[b:]
s = replace(s, 'test_mobile_supplier_invoice_history_is_store_wide_but_share_management_stays_actor_owned', 'test_mobile_supplier_invoice_history_is_personal_and_share_management_stays_actor_owned')
s = replace(s, '''    assert other_employee["supplier_invoice"]["can_manage_share"] is False
    assert other_employee["supplier_invoice"]["created_by_name"] == "موظف آخر"''', '    assert other_employee is None')
s = replace(s, '    assert owner["supplier_invoice"]["can_manage_share"] is True', '    assert owner is None  # Personal history is not the owner accounting report.')
s = replace(s, 'test_catalog_keeps_active_draft_private_but_lists_closed_store_invoices', 'test_catalog_keeps_both_active_draft_and_closed_invoices_private')
s = replace(s, '''    assert '"opened_by": context["actor_id"]' in source''', '''    assert source.count('"opened_by": context["actor_id"]') >= 2''')
Path(p).write_text(s)

p = '.github/workflows/build20-invoice-integrity.yml'
s = source(p, '8b0e9ba10e8a6c9df64d3b3bd0dae5ae64605929')
s = replace(s, "      - 'backend/supplier_invoice_integrity.py'", "      - 'backend/supplier_invoice_integrity.py'\n      - 'backend/supplier_invoice_history.py'\n      - 'backend/tests/test_supplier_invoice_private_history.py'")
s = replace(s, '            tests/test_supplier_receiving.py \\\n', '            tests/test_supplier_receiving.py \\\n            tests/test_supplier_invoice_private_history.py \\\n')
Path(p).write_text(s)

for name, target in [('supplier_invoice_history.py', 'backend/supplier_invoice_history.py'), ('test_supplier_invoice_private_history.py', 'backend/tests/test_supplier_invoice_private_history.py')]:
    data = subprocess.check_output(['git', 'show', os.environ['GITHUB_SHA'] + ':.build26-private-history/' + name])
    assert not Path(target).exists(), target
    Path(target).write_bytes(data)
print('PRIVATE_INVOICE_PATCH_APPLIED; live_business_writes=0')
