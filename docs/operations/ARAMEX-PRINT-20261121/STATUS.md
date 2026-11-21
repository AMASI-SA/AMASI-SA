# ARAMEX-PRINT-20261121: order-payload recovery WIP

Current user contract: recover official carrier labels through the existing
order-read permission, as with SMSA/iMile. Do not change linking or request
additional scopes. The previous missing-scope diagnosis was not proved and
must not be treated as the required solution.

New task branch `codex/aramex-order-payload-recovery-20261121` starts directly
from reviewed Production `66aa13967156e8e279b5297caf697c155f720fd1`.
The prior frozen release A/B/M and evidence checkpoint
`3a679012460c8ec072d1b7e754a6ab7686aae198` remain intact. That release was
published and original verify3 closed its lease, but original printing failed.
This follow-up has no lease or publication; Production changed by follow-up:no.

The existing shipping mapper recognizes `shipping.shipment` and a singular
top-level `shipment`. The print reader starts from `shipments` only, and its
order-scoped response normalizer also omits a singular `shipment` wrapper.
Added synthetic public-reader cases for these three shapes across Aramex,
SMSA and iMile, with optional standalone shipping APIs denied. All nine cases
fail before the source change with shipping_scope_required despite an official
label in the fresh order or order-scoped response. Command (exit1):
`python -B -m pytest --noconftest --asyncio-mode=auto -q backend/tests/test_carrier_shipment_read_fallback.py -k order_read_permission_supports_each_current_shipment_shape`;
PYTHONPATH includes backend and backend/tests; existing isolated test runtime.
Result:9 failed,41 deselected. No production source change yet; live cause still
unverified. This WIP regression checkpoint is not ready to deploy.
Implemented bounded normalization of fresh `shipping.shipment`, singular
`shipment`, and the order-scoped `data.shipment` wrapper. A nested AWB can
inherit its enclosing carrier but not the carrier id as a shipment id. The
same ingestion precedence prevents an old nested AWB overriding top-level
selection/history. Strict reads reject contradictory parents/references and
canonical shipment mismatches. Fresh singular cancellation/return/replacement
is fenced before an older ready list can revive it. An additional regression
first failed (1 failure/60 passed), then passed after this fence was added.

Fresh verification after the source change, in isolated module processes:
- `test_carrier_shipment_read_fallback.py`:99 passed,including ten real native
  HTTP-route cases with optional shipping API denied and no provider/local write.
- `test_amasi_shipping_compat.py`:58 passed and59 subtests passed.
- `test_mobile_external_label_read_parity.py`:19 passed.
- `test_shipping_label_current_guard.py`:29 passed.
- `test_salla_current_shipping.py`:37 passed.
- Total:242 tests and59 subtests; all exit0. `git diff --check` passes.
Commands use `python -B -m pytest --noconftest --asyncio-mode=auto -q --tb=short`.
Production source changed only `backend/order_engine/amasi_shipping_compat.py`.
Existing valid list responses and optional granted shipping fallback remain.
Additional review caught six failing null-placeholder cases; normalization now
preserves a valid singular/direct row when a sibling alias is null. Nine further
label-pointer/invoice cases failed before their repair. Official aliases already
recognized by ingestion (`shipping_label_url`, `awb_url`, `waybill_url`) are now
usable for printing. Explicitly typed invoices are excluded even beside a real
AWB or inside a label array. Six invoice variants and two diagnostics privacy
cases now pass. Read logs contain fixed source/shape/status flags only, never
provider/customer bodies, ids, URLs, tokens or exception text. The unsupported
reconnect instruction was removed; existing error codes remain compatible.

Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/2047. Earlier checkpoint
`89a2f16a331cde00be8f9a6b749865078c559039` readiness44935916653 succeeded,
but does not verify subsequent source changes; require fresh current-head CI.
This proves synthetic/public/native contract behavior, not original-order
printing. The workspace probe found no integration matching the current store
and could not match the configured app link. It stopped before token decryption or
any Salla request; it cannot establish Production permissions or absence of an
order. See LIVE-ORDER-READ-CONTEXT.json. No production credentials were exported.

Exact next action: push the source checkpoint and run fresh candidate CI for
PR2047. Obtain its governed source-A artifact, freeze an intent-only B, require
both host clean-clone rehearsals and applicable checks, then current-base normal
merge and a separately authorized guarded publication. Test original target and
control through ADB on the actual release. Do not request additional scopes or
claim the reported case fixed before real device acceptance. All v5 gates apply.
No customer fixtures, OAuth/config/token changes, provider POST, shipment
creation, substitute PDF, status/financial write or sync are authorized here.
Issue #1006 task block records the exact remote checkpoint and subsequent state.

## Historical release preparation (superseded by current status above)

Task: ARAMEX-PRINT-20261121. Repository: AMASI-SA/AMASI-SA.
Release branch: `codex/aramex-label-verified-release-20261121`.
Reviewed base J: `9f47a47ff16bfa02a8132bd83fe44db3bdb86da0`.
Preparation checkpoint `8f06a0cd8cd4a04249d291cbdfb63f0cca0d436e`
is retained on `codex/aramex-current-label-release-20261121` (superseded #2043).
Production advanced before its PR opened. Its merge history was correctly
rejected by the candidate validator because a source-only history cannot
contain an intent-changing merge. This corrected source is freshly based
directly on reviewed J with only the four task files reapplied; no frozen
foreign or task A/B commits were rewritten. The new PR records its exact A.
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
`test_shipping_refresh_latency.py` independently passes **26 tests** using an
isolated loopback Mongo replica set; no production database is used.
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

Push the corrected source checkpoint and open its Draft PR. Update the task
block in Issue #1006 comment 6238987123 without modifying surrounding entries.
#2040 and #2043 are closed as superseded. Require fresh corrected-head checks; obtain its
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
