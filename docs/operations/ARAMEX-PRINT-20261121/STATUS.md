# Current carrier shipment read recovery

Task: ARAMEX-PRINT-20261121. Repository: AMASI-SA/AMASI-SA.
Release branch: `codex/aramex-current-label-release-20261121`.
Reviewed base J: `5019e957cd1bb578f7b7375099547a470e51cb05`.
The owner authorized GitHub writes, normal merge and publication.
State: corrected source checkpoint; exact source/intent SHAs and CI are recorded
in the task PR and canonical Issue #1006 task block after checkpoint creation.
Production changed by this task: **no**. No shared source mutation or lease.

## Scope and preserved constraints

The native reproduction stopped before Android printing because the application
reported no outbound shipment. An independent Salla read found an international
carrier shipment with tracking and a null label. The owner confirmed printing
through Salla's authenticated dashboard. The precise original Production read
failure and canonical identity remain unverified; the preview database does not
contain the original order and cannot establish its Production state.

`backend/order_engine/amasi_shipping_compat.py` uses an already-known current
shipment ID to read details when lists cannot identify it. The saved ID is only
a routing hint: fresh details must prove exact shipment and order identity.
Present parent/reference contradictions, cancellation, returns and supersession
cannot authorize a label. Fresh scoped list evidence of cancellation or another
active shipment blocks this new fallback before the hinted details are read.
Foreign/unidentified global rows and inactive/superseded historical rows cannot
veto independently proved current details. Existing carrier, freshness,
request and canonical snapshot fences remain in force.

Failed shipment reads now distinguish denied scope or unavailable Salla reads
from an authoritative empty modern list. An independently obtained valid label
still succeeds. No provider POST, shipment creation, status write, replacement
PDF, commercial invoice, authenticated dashboard-page fallback, mobile source,
credential change or financial write is added.

Changed files: the carrier adapter, its synthetic public/native regression
module, Shipping Print Boundary CI classification and isolated regression step,
and this status document. Unrelated local/shared files are preserved.

## Verification and superseded candidate

The initial adapter regression failed before the source edit. The added safety
regression also failed on the first frozen candidate: a conflicting active
shipment list could be filtered away and an old hinted ID revived. After the
new fence, all **41** tests in `test_carrier_shipment_read_fallback.py` pass,
including both native HTTP routes and nine cancellation/replacement scenarios.
`test_amasi_shipping_compat.py` independently passes **58 tests and 59 subtests**.
Commands use `python -m pytest --noconftest --asyncio-mode=auto -q` with
`PYTHONPATH=backend;backend/tests` on Windows. `git diff --check` passes.

The earlier bounded patch passed 390 tests and 64 subtests across twelve
isolated module processes (one deliberately skipped memory rollback case has
a passing real-Mongo counterpart). Its exact source A CI passed 25 jobs with
six conditional skips. That evidence does not replace the new exact-head CI.

Superseded immutable candidate (never merge or deploy):
- Branch `codex/aramex-current-label-rootfix-20261121`, Draft PR #2040.
- Source A `a1d2ca761f0956a4214aa773e22e829bf74b8c09`.
- Intent-only B `bc267e64a2305a8b47528960e34684d9ce95949a`.
- Evidence-only checkpoint `366b877fd861a270fed2d5e4437ca20b8bce331b` on
  `codex/aramex-current-label-evidence-20261121`.
These commits remain untouched. The corrected source starts from reviewed J
and requires its own new governed build, intent-only commit and rehearsals.

## Exact next safe actions

Push this corrected source checkpoint and open its Draft PR. Update the task
block in Issue #1006 comment 6238987123 without modifying surrounding entries.
Close #2040 as superseded. Require fresh corrected-head checks; obtain its
successful Linux governed candidate, validate the complete Git manifests and
deterministic identity, and freeze exact intent bytes in a new intent-only B.
Do not add generated ignored build/proof/runtime files to Git. Wait for all
applicable exact-B checks and both Host20/24 clean-clone adapter rehearsals.

Before any shared `/app` mutation or rehearsal, run the original guard status
and require active:false. Reconcile a fresh origin production tip, use a normal
merge preserving A/B ancestry, run the configured adapter, then prepare and
immediately prepublish before the authorized publication. Require a newer
explicit deployment success, original three-probe verification and inactive
lease. Finally reproduce the actual original-order fetch and print via ADB.
Do not claim the reported order is fixed until that device acceptance succeeds.
