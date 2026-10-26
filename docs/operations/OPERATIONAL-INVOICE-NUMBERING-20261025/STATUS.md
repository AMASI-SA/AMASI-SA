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

Remote checkpoint: ac53114cfcacf999616fe9fc40335a248e936bac.
Native counterpart checkpoint: fd36d0cbfa844be2b95dc9eb3f7345da2594f351, verified against remote.
Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1508. CI pending on creation; no pass inferred. Numbering tests renamed to match existing operational-balance CI discovery glob. Both source worktrees were clean after checkpoint pushes.

## Configured catalog root repair — 2026-10-26

User requests fix + final Android package. Added opt-in configured=true catalog mode: components from catalog(), product descriptions/options from existing canonical products(). Legacy catalog default and variant validation unchanged. save_purchase builds legacy product variant catalog only when at least one line actually uses legacy identity; fully configured lines still validate canonical product/options/location through describe(). No fabricated IDs or physical inventory mutations.

Regression reproduces numeric legacy variant label failure: legacy catalog409 remains; configured catalog succeeds; invented configured value422 leaves state unchanged; valid raw/ready purchase succeeds with original product and physical collections untouched. 81 tests PASS on dedicated replica-set Mongo127.0.0.1:27306 (invoiceCatalog), databases retained, covering metadata, variants, inventory, numbering and supplier adjustments. Earlier standalone run59 PASS/2 environment failures (transactions require replica set); no regression assertion was weakened.

Native counterpart87ee444752ceeef020fe99c198d521e25e3d2691 requests configured mode, keeps independent supplier data on catalog errors, blocks new saves on failed choices and retains durable retries. Purchases57 PASS and TypeScript PASS; code17 build pending. Server deployment required. Other conversation01a15540-73c4-7031-9fc2-7aa300339542 is being asked to coordinate fresh guard checks then v5 build/intent/deployment. No production writes, guard mutation or deployment performed here.

## Clean release source — 2026-10-26

PR1508 source0c458c7b165e4baec164698e3f0b5cfa50c8ba23 passes backend/operational/G47/CodeQL CI but fails release source classification: old merged history includes intent transitions after J. Preserved original branch and PR. New branch codex/operational-purchase-release-20261026 at D:/codex-worktrees/operational-purchase-release-20261026 starts from freshly fetched Production56560cf68eb08fa8ef5de9780701722c283fbc95. Applied only the eight-file task diff from04db2224d68d6e5ed5629391355c4b45ab8b6d15..0c458c7b165e4baec164698e3f0b5cfa50c8ba23, without old merge ancestry or intent changes. Production and shared /app unchanged.

Fresh combined verification97 PASS in31.91s on dedicated Mongo replica set127.0.0.1:27306: purchase metadata, inventory variants, inventory, invoice numbering, supplier adjustments and exchanges. All databases retained using prefix-checked no-op cleanup. Runner/evidence: D:/amasi-build/evidence/operational-apk-catalog-20261026/verify_release_source.py and release-source-tests.log. No production writes.

Android1.0.14/code17 source87ee444752ceeef020fe99c198d521e25e3d2691 built/signed, installed viaADB with login retained. APK SHA25684b3b2d259a2fb0418d771893ab8d40264f5c552a38c52702e4ef3f2b05bd3e3. Nine routes render, no fatal errors, but purchase catalog remains blocked on live server awaiting this release. Writers remain disabled. Native checkpoint67435b8 records build/device evidence.

Next: verify new source classification, open replacement Draft PR, complete official two-build artifact/Intent B and review required CI. Other task01a15540-73c4-7031-9fc2-7aa300339542 has been told this task owns new Source A and asked only for fresh Guard/Manage Publishing/access evidence until final source handoff. No fresh guard response received; never clear another lease or infer deployment success. Official publication and three verify probes remain outstanding.
