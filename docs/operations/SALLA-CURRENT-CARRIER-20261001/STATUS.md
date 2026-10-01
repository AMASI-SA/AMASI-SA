# Salla current carrier synchronization

State: verified WIP source checkpoint; no merge or deployment.

Repository: AMASI-SA/AMASI-SA
Task branch: fix/salla-current-carrier-20261001
Production source baseline: 901568ccaaf510dc1f84d9c28f38d368d07dc64d
Baseline tree: eeb12440116fe3b77da9bbe5687f550aff424c82

Current Salla carrier observations update the canonical order shipping group
independently of Make's fill-empty policy. Sparse responses preserve current
facts; obsolete shipments, return shipments, cancellation of an older shipment,
and late concurrent intake cannot restore the old carrier or its label. Order
Engine detail/list projections use the canonical group. Shipment IDs reach the
DTO so cached labels and late printing responses cannot cross replacements.

P02 preparation reviews imported fees when verified current carrier facts
contradict them, are unresolved, cancelled, or match multiple order identities.
Posted replay and imported evidence are preserved. No tariff, posting contract,
financial control, or historical journal was changed.

Verified in scratch with isolated test dependencies:
- Backend affected suites: 195 passed, 565 subtests passed; exit 0.
- Frontend shipping/polling/printing: 28 passed in 3 suites; exit 0.
- git diff --check: exit 0.

Remaining before final candidate:
- Separate order/carrier and shipment metadata clocks; a newer sparse order
  envelope must not suppress a valid update for the same current shipment.
- Re-run affected checks and record exact checkpoint/PR SHA in Issue #1006.

Validation limits: Mongo mock tests exercise intake/CAS/read projection, not
real replica-set transaction serialization or P02 posting. No transactional
Mongo URI is available. Live changed-carrier webhook payload has not been
observed. Available order/webhook permissions cannot reveal a company identity
that Salla omits; this change adds no shipping permission or Shipments API calls.
Detail polling reads local Mezan data every 3 seconds; list polling stays at
10 seconds. No guaranteed zero-latency Salla event delivery is claimed.

Unrelated accounting integration PR #1229 and its release workflow are
preserved; eventual integration must retain its newer accounting writer.
Production changed: no. No /app changes, backfill, release intent, frontend
release artifact, lease, merge, or publication was performed.

Next safe action: close the independent-clock regression locally, then verify
and update this task branch/Draft PR; do not deploy this checkpoint.
