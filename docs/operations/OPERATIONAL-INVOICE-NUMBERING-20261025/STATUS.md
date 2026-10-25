# Operational invoice numbering — 2026-10-25

Task: automatic internal Mezan purchase-invoice number; optional supplier reference in Android supplier invoice creation.
Branch: codex/operational-invoice-numbering-20261025.
Base: 0d8f4524a26978471a72662cb56bc5e3a4584530 (fresh origin production fetch).

Implemented:
- New client sends supplier_invoice_number, including empty string; server allocates MZ-PI-000001 style owner-scoped sequence inside existing state CAS mutation.
- Legacy invoice_number payloads, invoice identities, request replay hashes and saved records remain unchanged. Mixed manual internal/new external fields reject.
- Retry returns the same invoice; concurrent distinct purchases receive unique numbers. Supplier reference duplicates reject across purchases, exchanges and canonical MZ2 invoices.
- Payment/adjustment lookup accepts internal or supplier reference. Late canonical collision uses supplier reference, not automatic internal number.
- Supplier reference is optional, so distinct commands without a reference cannot identify duplicate real-world documents; request retries remain deduplicated.

Verification:
- 61 PASS: invoice numbering (6), inventory purchases (19), exchanges and supplier adjustments (36), real local Mongo on 127.0.0.1:27305.
- Includes simultaneous creates, retries, payment retry, legacy HTTP replay, lookup by both references, duplicate external reference, reserved-number collision and failure atomicity.
- Dedicated DB directory D:/codex-test-data/invoice-numbering-20261025. All test databases retained. Harness replaces test fixture drop_database with a prefix-checked no-op.
- Runner and output: D:/codex-evidence/operational-invoice-numbering-20261025/verify_preserving_databases.py and backend-tests.log.
- Native counterpart: AMASI-SA/amasi-mobile branch codex/operational-apk-readiness-20261025. Purchases54 PASS; TypeScript PASS.

Release state: NOT merged or deployed. No financial/material writers enabled. No actual business records written. Production changed: no. No shared /app changes, no release lease acquired or cleared, no release intent modified.

Next: inspect Draft PR/CI, review new API and native counterpart, then follow v5 source A/build/intent B release protocol with fresh guard/publishing checks. Publish compatible backend before building/releasing new native version. Current archived APK1.0.12/code15 has picker changes only, not automatic numbering. Do not claim APK numbering ready until replacement package is built, signed and device-verified.
