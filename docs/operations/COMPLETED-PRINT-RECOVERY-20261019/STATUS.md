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
