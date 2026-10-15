# Build20 supplier invoice integrity — independent Draft PR #1159

Base: `aa0eac73128dfcded4a1aaaa1217cb6c9d16810c`.
Branch: `fix/build20-supplier-invoice-integrity-20260926`.
Protected PRs #1155 and #1156 must not be changed by this task.
Mobile continuation: AMASI-SA/amasi-mobile#209 above accepted `89bb496ef6b1867bf96a952065894ec51646563a`.

## Authority
The persisted `mezan_supplier_invoices_v2` document, its session summary and its original supplier-payable transaction group are the single source of truth. Exact confirmed minor-unit total, supplier, session, approved employee, invoice identity and both balanced ledger entries are checked inside the existing transaction before commit. A mismatch rolls back every transactional write. Post-close Mobile GET reads the committed invoice before success.

The permanent document source retains id, invoice_number, supplier_id, session_id, total_halalas, SAR currency, approved_at, ledger_txn_group_id and ledger_entry_ids. Future Mezan 2 accounting must consume that existing identity/group, not create another journal for the same invoice. No Salla/Qoyod writes.

## Owner correction: PDF is presentation, never accounting authority
The prior PDF numeric-text experiment is superseded. The original PDF generator blob `1a8f80f14efbf3050fac12fc89fd024e527634f5` is restored unchanged. New tests spy on the renderer input at the real HTTP route after transaction commit and compare invoice id, number, supplier, session, lines and total with the persisted row. Independent query draft/total values cannot replace it; absent/foreign invoices do not reach the renderer; renderer failure cannot change already committed accounting. No PDF parsing, text extraction or OCR is used as an integrity gate.

## Verification state
28 transaction/presentation-source scenarios plus existing regression tests are configured in `.github/workflows/build20-invoice-integrity.yml`. Exact final-head CI must be read before acceptance. Older CI evidence from the superseded PDF experiment is not acceptance evidence for this revision.

## Historical investigation remains blocked
No connected Production Mongo read channel was available. The failed and control session IDs, their scan events and actual ledger rows have NOT been read. Historical root cause is UNDETERMINED, not inferred from code or synthetic tests. No historical invoice repair or live data modification.

No merge, deployment, APK/EAS/OTA, final UAT, Performance Gate or Production mutation. Final exact heads/tests and remaining blockers are recorded in Issue #1006 and PR comments.
