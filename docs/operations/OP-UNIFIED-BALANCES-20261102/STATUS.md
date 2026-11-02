# R03/R05 implementation checkpoint — 2026-11-03

**WIP: NOT deployed; no live financial action.** This section supersedes the implementation-pending description below; previous evidence is retained intact.

## New source and explicit limits

R03: The operational source now maps a verified frozen reporting envelope's root product base and existing external-service resource identities to the original preparation pieces/services. Product receipt is independent of service receipt, including partial service completion and old-order open exposure. Supplier history uses the first receiving event for the original product, not a later service supplier. Final preparation invoices replace the same component estimate. Physical children keep the native allocation/receipt path. No physical recipe, service binding, stock action, dashboard implementation or current catalog price is changed. Unsupported nested financial-only bases without a unique legacy receipt identity remain explicit unknowns, never omitted or treated as received. This limitation is distinct from supported nested physical components.

R05: A source-verified native invoice can be explicitly matched to existing operational supplier obligations and bank movements, or its EXACT missing operational amounts can be previewed for separate owner approval. Existing source journals, bank transfers, invoices, openings and historical movements are immutable. Preview reads one Mongo snapshot; approval uses the existing operational CAS/idempotency path, rechecks fresh proof, scope, identities and expected preview hash, and requires explicit identity and unrecorded-money confirmation. No approval or live data query was executed by this task. Unallocated or partially allocated old cash and pre-start native invoices remain review-required; they are not automatically rewritten/replayed. Refresh re-verifies saved source links and surfaces later source changes without auto-importing them.

A new owner-only report panel and read/candidates/preview/approve API adapters are authored. It presents supplier and bank before/after differences, preserves uncertain approval intents for exact retry, and cannot enable provider statement posting. UI tests are authored but NOT yet executed at this checkpoint. No financial activation is inferred from publishing this code.

## Tests actually run in this checkpoint

Exact local focused command: PYTHONPATH=/mnt/data/op_mapping_work/deps:backend:backend/tests OPERATIONAL_TEST_MONGO_URI=mongodb://127.0.0.1:27305/?replicaSet=operationalprojection MZ2_TEST_MONGO_URI=same python -B -m pytest --noconftest -q backend/tests/test_operational_balance_native_purchase_link_mongo.py backend/tests/test_operational_balance_native_purchase_review.py backend/tests/test_operational_balance_native_purchase_link.py backend/tests/test_operational_balance_bundle_legacy_receipts.py --tb=short --junitxml=mapping-focused.xml

Result before the subsequent UI/schema-scope edits: **159 passed, 0 failures/errors/skips**, 18.72 seconds. Included subsets:30 legacy-bundle cases,39 native-link pure cases,18 actual isolated Mongo preview/approval cases and72 retained sealed-source cases. Do not add these to prior1642. Original fixture-option failures and a real full-versus-projected invoice evidence-hash failure were preserved; canonical financial invoice fields now give identical evidence fingerprints without weakening sealed journal checks. Final whole-tree backend/HTTP/UI/security/build acceptance remains pending.

The initial temporary read-only source export workflow pins2661457a and supplied5035 SHA-verified Git blobs. Source export helper and any source-only transfer workflow MUST be removed before release. No /app, lease, production ref, release intent, native ledger, bank, refund, invoice, opening or policy/payroll activation was touched.

## Next safe action

Read exact remote source and finish combined acceptance, including R03 physical child receipts plus R05 mapped bank proof together, new UI, permissions/atomicity/retry. Preserve user release scope: include both mappings WITH all safely ready work, defer independent unfinished backlog. Produce included/deferred matrix; do not waive genuine included dependencies or existing release-identity failure. Real financial differences require actual owner review/approval, not automatic code-deployment backfill. Record every verified milestone in DELIVERY-REGISTER and PR1687.

---
## Preserved previous record (historical)

# OP-UNIFIED-BALANCES-20261102 — ready-release scope and durable delivery register

**DRAFT / NOT publication-ready / NOT published / NO live financial correction or activation.**

## Controlling owner instructions — supersede earlier all-backlog publication gate

Owner scope decision, PR1687 comment **6588193710**: complete BOTH (1) bundle financial-cost to unchanged legacy receipt mapping and (2) native purchase invoice/payment to operational-balance identity matching, then include them WITH ALL other implemented, verified, safely releasable fixes in ONE next release. Do not publish just the two fixes. Incomplete independent work is deferred for later implementation and separate releases, rather than holding all ready work. Preserve every original task and every newer production change.

The subsequent explicit owner instruction requires every completed and outstanding task to be recorded in GitHub, with the registration reference sent back to the owner for future conversation continuity. This is not a handoff request now. The continuous work inventory and per-milestone procedure are in **DELIVERY-REGISTER.md**, initial record **001**. Use stable work IDs R01–R14, updating facts after each milestone; keep implementation, tests, merge, live publication and financial activation separate.

## Verified starting point for this documentation checkpoint

Repository: AMASI-SA/AMASI-SA. Branch: integration/operational-balances-unified-20261102. Draft PR:1687.

- Application-preserving tested source: **c885bb8cf1ff7757a6d7cd623c6a238e0a10c8b5**, root **c238635c2b67bc4f5e369581887023346b6bc9d0**; verified via current PR/ref/commit reads.
- Last application checkpoint: **81d55f2609454d57b19e4611c22236e1ab86d1d3**. Production inclusion merge: **5f9d28ca36fdbc4a0b03e2b17a38f504120a1fca**.
- Latest production ref read: **79187db4b6212993f94338830298e467e3773d68**. Inclusion/merge is not new live deployment proof.
- Latest detailed test record: comment **6587978962**; run **41520247570** on exactc885, **1642 backend and316 frontend/26suites**, zero failure/error/skip. These are previously verified results, NOT tests rerun during this documentation checkpoint. Exact artifact IDs/digests are retained in DELIVERY-REGISTER.md and the evidence comment.
- Release Readiness **41520247573 FAILED** at sourceA/intentB classification; final production build/rehearsal skipped. The release-sequencing change does not waive this failure, security/source checks or any dependency of an included behavior.

The preceding complete STATUS.md is preserved BYTE-FOR-BYTE in **STATUS-PRE-READY-RELEASE-20261102.md**, original blob **d98d15607f0ad6fd5843b4a8d7f2eff64db38c5f**. All earlier PRE/COMBINED/PAYMENT/SUPPLIER/INCOMING-PRODUCTION evidence and original failures remain untouched. Its older requirement to finish every backlog item before any release is historical; the current owner decision above governs.

## Current-release work and remaining boundaries

Full itemized inventory: DELIVERY-REGISTER.md, R01–R14.

- Already implemented/tested candidate slices: approved post-start supplier invoices on old orders, final-component estimate replacement, consistent saved-vs-proposed supplier preview, original-SAR/exact-status ordinary/physical-bundle remaining costs and partial receipts, inclusive-cutoff payroll preview/approval, actual post-start delivery earning on old orders and noncash bank/POS review, four external loan actions with unchanged historic balances and scoped permissions, truthful warning aggregation/scoped completeness and usable financial report sections. Each included slice still needs the final combined candidate/dependency checks. No live activation is implied.
- **Immediate incomplete mapping1 (R03):** newer reporting_cost may contain parent base/service financial costs without a corresponding physical leaf. Bind frozen financial costs to authoritative EXISTING legacy piece/service receipt identities and partial quantities; preserve old services/components/invoicing and support applicable pre-invoice receipt evidence. Current bundle_financial_policy_operational_mapping_required is a guard, NOT a finished mapper. Never add physical current_product or invent receipt coverage.
- **Immediate incomplete mapping2 (R05):** sealed native invoice/liability/tax/journal/bank source proof is implemented read-only; operational obligation/payment identity matching remains incomplete. Compare existing facts, reject collisions and show actual differences without double-counting invoice/debt/bank/payment or silently importing history. Keep bundle_received_purchase_financial_matching_required until the specific evidence chain is supported.
- Deferred when genuinely independent: provider dated capture/plan/void/refund ingestion and actual merchant policy/gross-net certification; provider postsettlement fee/VAT/partial-multiorder bank reconciliation; full current-carrier/COD/forecast coverage; expanded customer exchange/return/shipping acceptance; remaining real source/advertising-start/FX/salary warning causes and complete common-period expected profit. Do not claim these completed or discard them. Retain provider_postsettlement_refund_review_required and statement posting_allowed=false; no automatic provider fee-model activation.
- Required BEFORE any included behavior is released: its functional and permission checks, truthful unavailable states for deferred dependencies, necessary visual/interface checks, exact final combined tests and source/security/build readiness. Do not hide real missing data to make a report look complete. Deferred comprehensive report scope does not waive safety/quality of the sections actually shown.
- Required BEFORE actual financial correction/activation: owner-scoped read-only current/proposed differences, explanations and explicit approval for actual new recognition. Existing openings are saved startup balances, not new inputs; old customer-order age or hypothetical overlap does not exclude new approved supplier invoices. No live overlap has been established. Do not reset/re-enter openings, reinterpret old generic movements or silently backfill through refresh.

## Release and next action

First complete R03 andR05, preserving production and all ready work. Produce an included/deferred execution matrix for the final candidate. Run exact combined source/reducer/Mongo/UI/idempotence/permission/atomic rollback checks and fix real regressions. Then create a fresh unified sourceA, governed A/B build and intent-onlyB; do not reuse inherited dashboard intent or squash/rebase after freezing. Read actual production_release_guard.py status BEFORE /app or release preparation, require inactive coordination, then authorized prepare/prepublish and ONE publication of the ready combined release. Success requires live verify3 and status active=false. Never cancel another task's lease.

This checkpoint changes documentation only. No mapper implementation, new code test, production merge/deployment, database/opening correction, old replay, supplier/provider/bank/stock/refund operation, payroll/policy activation, /app,lease or release-intent action occurred. The next implementation milestone must be separately recorded with its exact code/tests/result and sent registration reference. Issue1006 remains capped; PR1687 plus these files are the continuation record.
