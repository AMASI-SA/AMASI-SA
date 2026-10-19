# Completed shipment print recovery — verified source candidate

Production base: e015b913ce950d6e5cf52cc0f1893da94b7b30b4.
Owner authorizes urgent Backend deployment with rollback. Revised acceptance: current local products ready and fresh Salla completed; delivery-outbox history is not a print prerequisite.

Implementation: assembly_print_completion reads current physical/virtual units and exact source-item coverage in a Mongo snapshot, checks fresh Salla identity/status/shipment with GET only, then independently rereads the local evidence before returning the existing saved label. Missing, held, cancelled, archived or duplicate units fail closed. Harmless workflow revision/outbox progress changes do not block. Completion GET, legacy issue and legacy assembly-search inline labels use the read-only recovery. Permissions are rechecked after provider IO. No Ready, stock, financial, provider POST or new-shipment writes are introduced. Queue custody/reprint restrictions remain unchanged.

Fresh local validation on an isolated MongoDB 8 replica set:
- New print and actual route suites: 18 passed, 8 subtests passed, 20.75 seconds.
- Delivery, status transition, v2/legacy readback, legacy print, G47 and saved-label suites: 364 passed, 100 subtests passed, 250.09 seconds.
- Independent final code review: no remaining blocker under revised owner policy. Reviewer did not rerun tests.

The earlier fingerprint-bypass experiment was rejected and removed; it is not this candidate. Preview database had no target order, so it is not Production diagnosis. Live UI established local-ready/completed versus pending-label mismatch, not database repair proof. No emulator print or latency improvement has yet been demonstrated.

Production changed: no. No merge, intent or deployment yet. Next: commit/push this source, open Draft PR, pass final CI, freeze intent-only B, rehearse and prepare rollback, then follow Release Guard prepare/prepublish and exact live verification. Existing unrelated Android/settings/custody work is excluded.

## Canonical identity correction candidate (after failed live acceptance)

PR1436 is deployed at16e44c3bd5478854bc3d12a9545f05519e61f22d, exact live verification passed but target print acceptance failed. New branch codex/print-canonical-items-20261019 is based on that Production HEAD.

Proven defect: raw Salla item ids were compared to canonical preparation ids. The production mapper prefixes identities with salla:order:item and supports source aliases/generated ids. A real Mongo regression changed the physical fixture to the actual canonical shape and FAILED with None before correction. The correction reuses map_salla_order without removing quantity, duplicate, owner, hold, readiness or fresh-provider checks. Physical and direct virtual fixtures now use canonical ids. Alias variants succeed without writes; raw and other-order identities fail.

Fresh focused tests including mapper and actual routes:47passed+13subtests,23.79s. Independent code review found no blocker in this narrow patch. Bundle/instant coverage remains a separate known compatibility limitation, not silently bypassed. Actual affected-order recovery remains unverified until deployment. Next CI, intent-onlyB, release rehearsal/rollback and gated publish/live readback; no new Production change from this candidate yet.

## Canonical correction deployment and live acceptance (2026-10-19)

PR1439 merged and deployed: 0d55bb0ce74df2759e6f9c4786f1b759b29ac0b0. Source A eab46fc1836917ca4f482a5be90ea012e7c820cb; intent-only B db26935fd97b4634149cf553f9ce55ce6962a77a; merge/B tree 9917baa7bfc3e76333e834cc498e87a118e7e507. Runtime rg5-aebc6cb4c8766a72f3b76bbc56ca5059d0d2e486ddf5ccd6814f220f51d97ed9.

Final CI:16 applicable PASS,4 nonapplicable SKIP. Readiness39241414811 PASS, review39241414754:453 tests+270 subtests PASS plus acceptance guard1 PASS. Source build39241084480 PASS. Rollback55f02eb62c2fc29733a609a23a329b05f3016ce0 is prepared, source build39241616265 and clean-clone readiness39242438355 PASS; not activated.

/app adapter build, prepare and prepublish exited0. Emergent visibly reported Deployment finished Oct19 02:35PM. Exact live verify exited0, verified=true, checks=3 with the identities above. Guard status active=false. No parallel lease remains.

LIVE ACCEPTANCE FAILED: fresh browser reload and target294349906 search still show2/2 products ready, completion awaiting confirmation, label awaiting verification, and order-status-changed blocker. Clicking read-only Update Status returned to the same disabled print state. No PDF opened and no native Android print verified. The canonical-id bug is fixed and regression-proven, but it was not sufficient to recover this target. Do not claim the outage resolved. Actual production source/coverage/provider rejection needs read-only diagnosis; preview DB is not production evidence and the earlier production Mongo connection timed out. No Ready replay, stock/financial repair, provider status POST or new AWB was performed. Do not replace missing evidence with another bypass. Separate Android/tracking/custody work excluded.
