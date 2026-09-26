"""One-shot, exact-blob guarded edits. Development-only, never connects to a DB."""
import ast
import hashlib
from pathlib import Path

p = Path('backend/supplier_receiving_routes.py')
s = p.read_text()
def blob(text):
    b = text.encode(); return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
assert blob(s) == '76dac6161f3b452879561ec3dd6ede1f7a2dfe8f', 'source drift'
def edit(before, after):
    global s
    assert s.count(before) == 1, ('ambiguous patch', before[:100],s.count(before))
    s = s.replace(before, after)
edit('from supplier_invoice_pdf import generate_supplier_invoice_pdf', 'from supplier_invoice_pdf import generate_supplier_invoice_pdf\nfrom supplier_invoice_integrity import CONTRACT as INVOICE_INTEGRITY_CONTRACT, require as require_invoice_integrity, verify_persisted_supplier_invoice')
edit('class SupplierReceivingSessionCloseRequest(BaseModel):\n    model_config = ConfigDict(extra="forbid")', '''class SupplierReceivingSessionCloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed_total_halalas: int | None = Field(default=None, gt=0, le=9_007_199_254_740_991, strict=True)
    expected_supplier_id: str | None = Field(default=None, min_length=1, max_length=160)''')
edit('if key not in {"_id", "user_id", "ledger_entry_ids"}', 'if key not in {"_id", "user_id"}')
edit('        "supplier": dict(row.get("supplier_snapshot") or {}),', '        "supplier": dict(row.get("supplier_snapshot") or {}),\n        "supplier_id": row.get("supplier_id"),\n        "financial_integrity_verified": row.get("financial_integrity_verified"),')
edit('    amount = round(int(invoice["total_halalas"]) / 100, 2)', '    amount = float(Decimal(int(invoice["total_halalas"])) / Decimal(100))')
old_start=s.index('        if _text(session.get("status")) == "closed":', s.index('    async def close_session('))
old_end=s.index('        if _text(session.get("status")) != "open":',old_start)
s=s[:old_start]+'''        effective_actor = {**user, "id": context["actor_id"], "name": _actor_name(user)}

        async def closed_result(closed: dict[str, Any], tx: Any = None) -> dict[str, Any]:
            kw = {"session": tx} if tx is not None else {}
            saved = await db[SUPPLIER_INVOICES].find_one(
                {"user_id": context["merchant_id"], "session_id": session_id}, {"_id": 0}, **kw,
            )
            require_invoice_integrity(isinstance(saved, dict), "closed_session_without_invoice")
            if saved.get("experiment_mode") is not True:
                saved = await verify_persisted_supplier_invoice(
                    db, user_id=context["merchant_id"], invoice_id=saved.get("id"),
                    session_id=session_id, supplier_id=closed.get("supplier_id"),
                    expected_total=payload.confirmed_total_halalas, actor_id=context["actor_id"], mongo_session=tx,
                )
            if payload.expected_supplier_id is not None:
                require_invoice_integrity(saved.get("supplier_id") == payload.expected_supplier_id, "selected_supplier_mismatch")
            return {
                "ok": True, "session": _public_session(closed),
                "supplier_invoice": _public_supplier_invoice(saved),
                "financial_invoice_created": saved.get("financial_invoice_created"),
                "liability_created": saved.get("liability_created"),
                "financial_integrity_verified": saved.get("financial_integrity_verified") is True,
                "experiment_mode": saved.get("experiment_mode"),
                "experiment_run_id": saved.get("experiment_run_id"),
                "idempotent": True, "qoyod_updated": False, "salla_updated": False,
            }

        if _text(session.get("status")) == "closed":
            return await closed_result(session)
''' +s[old_end:]
edit('''            if not fresh_session or _text(fresh_session.get("status")) != "open":''', '''            if fresh_session and _text(fresh_session.get("status")) == "closed":
                return await closed_result(fresh_session, mongo_session)
            if not fresh_session or _text(fresh_session.get("status")) != "open":''')
edit('''            invoice_id = f"msiv2_{uuid.uuid5(uuid.NAMESPACE_URL, f'{merchant_id}:{session_id}').hex}"''','''            require_invoice_integrity(
                bool(fresh_session.get("supplier_id")) and fresh_session.get("supplier_id")
                == (fresh_session.get("supplier_snapshot") or {}).get("id"), "session_supplier_snapshot_mismatch",
            )
            if payload.expected_supplier_id is not None:
                require_invoice_integrity(fresh_session["supplier_id"] == payload.expected_supplier_id, "selected_supplier_mismatch")
            if payload.confirmed_total_halalas is not None:
                require_invoice_integrity(draft["total_halalas"] == payload.confirmed_total_halalas, "confirmed_amount_mismatch")
            invoice_id = f"msiv2_{uuid.uuid5(uuid.NAMESPACE_URL, f'{merchant_id}:{session_id}').hex}"''')
edit('''                "supplier_approved_by": context["actor_id"],''', '''                "supplier_approved_by": context["actor_id"],
                "approved_by": context["actor_id"],
                "financial_integrity_contract": INVOICE_INTEGRITY_CONTRACT if not is_experiment else None,
                "financial_integrity_verified": not is_experiment,''')
edit('''                    actor=user,
                    invoice=invoice,''', '''                    actor=effective_actor,
                    invoice=invoice,''')
edit('''                        actor=user,
                        invoice_id=invoice_id,''', '''                        actor=effective_actor,
                        invoice_id=invoice_id,''')
edit('''            invoice_summary = {
                "id": invoice_id,''', '''            invoice_summary = {
                "id": invoice_id,
                "supplier_id": invoice["supplier_id"],
                "session_id": session_id,
                "ledger_entry_ids": invoice["ledger_entry_ids"],
                "financial_invoice_created": not is_experiment,
                "liability_created": not is_experiment,
                "financial_integrity_verified": not is_experiment,''')
edit('''                        "supplier_invoice": invoice_summary,
                        "financial_invoice_created": not is_experiment,''', '''                        "supplier_invoice": invoice_summary,
                        "financial_integrity_contract": INVOICE_INTEGRITY_CONTRACT if not is_experiment else None,
                        "financial_integrity_verified": not is_experiment,
                        "financial_invoice_created": not is_experiment,''')
edit('''            return {
                "ok": True,
                "session": _public_session(updated),
                "supplier_invoice": _public_supplier_invoice(invoice),''', '''            if not is_experiment:
                invoice = await verify_persisted_supplier_invoice(
                    db, user_id=merchant_id, invoice_id=invoice_id, session_id=session_id,
                    supplier_id=fresh_session["supplier_id"], expected_total=draft["total_halalas"],
                    actor_id=context["actor_id"], mongo_session=mongo_session,
                )
            return {
                "ok": True,
                "financial_integrity_verified": not is_experiment,
                "session": _public_session(updated),
                "supplier_invoice": _public_supplier_invoice(invoice),''')
edit('''                        "تعذّر اعتماد فاتورة المورد محاسبيًا؛ بقيت الجلسة "
                        "مفتوحة ولم تُحفظ الفاتورة. حاول مرة أخرى."''', '''                        "تعذّر تأكيد نتيجة الإغلاق. تحقق من سجل الجلسة والفاتورة "
                        "قبل إعادة المحاولة؛ قد يكون الخادم أكمل الحفظ."''')
# Only these two persisted-document read routes get a verifier. No historical repair.
start=s.index('    @router.get("/invoices/{invoice_id}")');end=s.index('    @router.post("/invoices/{invoice_id}/share-evidence")', start)
section=s[start:end]
needle='''            invoice_id=invoice_id,
        )'''
assert section.count(needle)==2
section=section.replace(needle,needle+'''
        if invoice.get("financial_integrity_contract") == INVOICE_INTEGRITY_CONTRACT:
            invoice = await verify_persisted_supplier_invoice(
                db, user_id=context["merchant_id"], invoice_id=invoice_id,
                session_id=invoice.get("session_id"), supplier_id=invoice.get("supplier_id"),
            )''')
s=s[:start]+section+s[end:]
ast.parse(s)
p.write_text(s)
print(p,blob(s))
