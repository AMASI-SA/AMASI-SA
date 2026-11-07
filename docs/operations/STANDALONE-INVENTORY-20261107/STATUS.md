# Independent inventory preparation — WIP

Owner decision (2026-11-07): prepare inventory independently under Products. Stock purchases serve future matching orders; current customer-order preparation purchases remain unchanged. Do not integrate reservation, fulfillment, assignment, consumption or order costing until inventory is complete and separately authorized.

Base: 8947eb0b4ad5b0370efecdaadc044268693122da.
Task branch: codex/standalone-inventory-20261107.

## Implemented slice
- Protected, lazy /inventory-v2 workspace under Products.
- Current stock, owner opening, purchase entry and purchase reports reuse existing APIs and permissions; no copied ledger or inventory state.
- Explicit deferred fulfillment notice. URL tab permissions fail closed. Default entry only mounts the stock reader when authorized.
- Web operational balances links to the standalone page. Existing Android and operational screens remain available during preparation.
- No backend, fulfillment, stock-admission or accounting writer change. Opening approval remains subject to the existing closed writer and valuation requirements.

## Read-only findings and remaining work
Observed live before implementation: physical writer closed; empty unfiltered stock results; purchase catalog blocked by incomplete variant identity/name. This slice does not resolve those business readiness blockers.
1. Make purchase catalog failures item-specific with visible exclusions and strict server revalidation. Preserve current order preparation catalog behavior.
2. Design and verify isolated stock purchase -> physical receiving -> valuation -> supplier/bank linkage; existing operational purchase registration alone is not physical receipt proof.
3. Reconcile operational opening quantities/costs with valuation without generating supplier debt or duplicating existing stock.
4. Verify fractional component quantities through purchasing, receiving and returns.
5. Verify valued supplier returns, damage and stocktake adjustments with once-only monetary effects.
6. Integrate stock asset value into operational financial position with explicit noncash presentation and no double expense.
7. Run isolated transactional Mongo and UI acceptance for the complete standalone lifecycle.
8. Only after separate owner authorization, plan matching/reservation/consumption for customer orders. Never alter current preparation as part of this task.

No production data, configuration, release lease, merge or deployment changed. This is a workspace foundation, not complete inventory acceptance or activation.

## Verification
Pending focused frontend checks after locked dependency installation. Git diff whitespace validation passed. Prior review could not run Python tests because pytest was absent; not claimed as passed.

## Durable checkpoint
PR: https://github.com/AMASI-SA/AMASI-SA/pull/1796 (Draft).
Implementation checkpoint: 1a201a7a2e35ee0ef5d1c32e8ed9a6f5bf5caee8.
Local frozen-lockfile install did not complete; offline attempt exited 1 because @jest/environment 27.5.1 was not cached. No functional test pass claimed. CI Operational Balance Web run 42387561312 was queued at inspection; other checks were in progress. Do not infer acceptance from these statuses.
Next: inspect exact implementation CI logs, resolve failures, then implement the isolated catalog/receiving/valuation stages above. All fulfillment integration remains deferred.

## 2026-11-07 continuation — WIP, not release ready
- Owner now authorizes inventory fixes deployment. Subsequent answers: owner will create new warehouse/cabinets personally after deployment; each product save must immediately record physical quantity (not draft/full-batch approval).
- Refreshed remote task bb178c43159e811d48c58a81f21e251adc8eb360 and merged production c634686 into local integration branch codex/inventory-numbering-20261107. Original task branch/PR #1796 preserved.
- Implementing opt-in compact cabinet-row-slot display on NEW warehouses; legacy barcode/code/ID and old warehouse remain intact. Independent stock admission has its own paused control; existing physical and financial controls untouched.
- WIP repeated single-entry inventory path reuses canonical opening validation and pending valuation receipts; idempotent request IDs, owner transactions, target isolated warehouse, no order integration/financial journals. UI implementation pending dedicated verification.
- Purchase screen configured catalog now avoids the unused legacy variant reader that blocked the entire screen.
- Local locked frontend installation succeeded. 58 existing OpeningInventory/InventoryPurchases tests passed. InventoryWorkspace test hit Jest 27 inability to resolve react-router/dom; fix test harness before claiming pass. Existing 6 warehouse generation tests passed. Real isolated Mongo tests being prepared; no transaction acceptance claim yet.
- No Production changes, merge, deployment or live stock writes. Do not deploy this WIP. Next: targeted tests (new control isolation, repeated saves, replay/conflict, capacity, rollback, variants, old opening regression), frontend save/retry/navigation, V5 release verification.
- Issue #1006 previously refused additional comments because its comment limit was reached; this status and PR are the durable checkpoint.

## Acceptance checkpoint — PR #1799
Remote checkpoint: 6839d25b068e86151ee3a667ea76e57b633c0720 at codex/inventory-numbering-20261107; https://github.com/AMASI-SA/AMASI-SA/pull/1799. Continues original #1796 on production base c63468631897868771e4f3d5176011a82c848044. GitHub Contents/Git Data used because shell git push has no credentials.

Implemented: opt-in new warehouse standalone scope; default new UI display cabinet-row-slot with warehouse name; warehouse-wide new cabinet numbering across sections; immutable internal IDs/codes/barcodes; old warehouses unchanged. Dedicated /inventory-v2 page and owner-only single-entry actual stock save. Successful save clears form and shows saved table; receipt/entry/location/event commit in one owner-serialized transaction. Network retry retains exact idempotency key. Silver 50 and gold 30 remain separate identities; fractional components use canonical units. New independent writer remains default closed with its own owner control; does not open financial or existing physical writers. Current order eligibility remains denied via pending valuation. New warehouse/cabinets/quantities will be entered by owner, not fabricated by agent.

Fresh local evidence: 107 backend tests + 3 subtests PASS on isolated MongoDB 8.0.12 replica set (standalone, original opening, warehouse generation, default-closed, cross-process admission, physical adoption). All 386 operational frontend tests PASS (30 suites), including 7 new per-item save/recovery cases and 8 workspace routing cases. Added further HTTP configured-catalog/strict control, rollback and profile isolation tests within backend pass. Full operational backend suite and final exact-source build/CI remain pending. No Production mutation, merge, release lease or deployment yet.

Remaining inventory accounting lifecycle work remains explicitly deferred: purchases are supplier obligations, not physical proof; entry quantities have operational cost evidence with pending native valuation, no bank/journal effects or order reservation. This release prepares stock input, not automatic procurement receiving, valued returns/adjustments or full financial cutover.


## Published checkpoint — 2026-11-07
- User decisions: owner creates new warehouse/cabinets personally; immediate real per-item save. No live quantities, warehouse, cabinet, or independent writer control entered by agent.
- PR #1799 merged as dda8902aa2fabbd447f79a7da8dfdf57c3e2e64f. Source A 236749bbd30f6b42e2723a5ade259866aa5846a2; base J c63468631897868771e4f3d5176011a82c848044; intent-only B 1b43df9371f8dfa01e26f2c5711407f8e67668e8. Tested source tree 9b22ac88632953d9a056bd7c2be5d720c3d898a5. No source changes after freeze. Pre-freeze history correction preserved at codex/inventory-1799-checkpoint-8fb95313.
- Fresh validation: 2,806 operational backend tests PASS; 386 operational frontend tests PASS; targeted 107 + 3 subtests PASS; exact-source entry UI 7 PASS; all PR workflows successful (unrelated TikTok candidate skipped). Governed two-pass build PASS. Emergent clean clone adapter/package proof PASS (49.82s), merged build PASS (63.59s).
- First Cloud Build failed before deployment: 5dc7d3e5-4397-424b-b95c-c84bac042ebf, build a963257b-fdf3-4f24-902c-3607e41a7ebb. Cloud snapshot omitted 93 of 159 workflows while /app contained all 159 with clean git diff. Aborted matching lease. No guard changes or missing-file relaxation. Rewrote identical workflow bytes to request sync and re-ran prepare/prepublish successfully. Emergent's automatically started chat was paused per owner policy.
- Second publish succeeded Nov 7 22:03 Riyadh, version f0450b8 (100th publish). Live health reports exact A and rg5-267f068f78467723886ec064b1a775f08aabe499f05b8fac4cf257f407322f28 with verified identity and frontend proof true. Authenticated live /inventory-v2, entry context, and standalone warehouse creation form PASS. Old بيت العود RUH-WH001 preserved. All unrelated untracked /app files preserved.
- Production changed: YES, inventory UI/API deployed. Current order fulfillment and financial writers remain unchanged; stock entry valuation/fulfillment integration deferred. No full inventory accounting completion claimed.
- Full production_release_guard.py verify is running across all public assets for three probes; lease remains active until completion. Do not start another release while active. Next safe action: read /tmp/mezan-inventory-verify-1799.json and guard status in /app; require verified:true and active:false. Preserve final outcome in this document/PR. Issue #1006 remains full.
- This documentation checkpoint is after deployment on the task branch only, not a new release source.
