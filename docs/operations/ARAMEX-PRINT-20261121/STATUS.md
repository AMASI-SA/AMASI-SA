# ARAMEX-PRINT-20261121: verified external-label reprint repair, release pending

## Current source milestone (2026-11-22)

The six red actual-route cases now pass after a narrow change to
`backend/order_engine/amasi_shipping_compat.py`: the strict Native reader may
return an existing verified external-carrier AWB for shipped, delivering or
delivered orders. It preserves the observed Salla status and never posts a
status or creates a shipment. Current-child/carrier/supersession/cancellation,
assembly and employee-authority fences remain in place. Normal local-courier
document generation remains rejected for these statuses; the existing separately
guarded historic-courier recovery is unchanged.

`test_external_carrier_reprint_status.py` now covers six positive Native routes
and three negative local-document cases. The existing mobile parity regression
also covers a fresh provider advancing beyond cached completion without a status
POST or canonical-order mutation. Cancelled/refunded/unknown cases still fail
closed. Shipping Print Boundary CI now explicitly runs both Native modules.

Local isolated-module verification, all exit0:
- external_carrier_reprint_status:9 passed;
- mobile_external_label_read_parity:20 passed;
- amasi_shipping_compat:58 passed,59 subtests passed;
- carrier_shipment_read_fallback:111 passed;
- shipping_label_current_guard:29 passed;
- salla_current_shipping:37 passed;
- courier_history_identity_recovery:114 passed;
- courier_history_recovery_races:18 passed.
Commands use Python -B -m pytest --noconftest --asyncio-mode=auto -q --tb=short,
one module per process. git diff --check passed. Total396 tests plus59 subtests.

PR2061 is Draft. Source444b29c4660a11e8570ed0fee682ffcd4cd14fe6 readiness
run45030291278 passed and produced candidate artifact14757168626. Independent
Git-manifest and deterministic-identity checks passed. No intent was frozen:
Shipping Print Boundary run45030291253 found24 legacy expectations of the old
status rejection (573 passed,1 skipped). These expectations are now updated to
the new external-AWB contract and extended to delivering/cancelled/refunded,
with observed status, no-provider-write and all non-label data assertions kept.
Fresh isolated legacy module:146 passed,146 skipped (Mongo replica set absent
locally); exact-head CI must execute both memory and real-Mongo variants.
Earlier source candidate is superseded; build a new source A and intent B.
Current local total542 passed plus59 subtests;146 Mongo skips remain for CI.

Release state: no frozen intent/new lease/publication yet. Production unchanged
by this follow-up. Root-case Native printing remains unverified until acceptance
on the new reviewed release. Next: push updated source checkpoint, require fresh
CI, new v5 A/B and exact-B host/ARM rehearsals, then authorized guarded publication
and original/control ADB tests. Preserve all previous frozen pairs.

## Earlier red checkpoint (preserved)

New branch `codex/aramex-intransit-label-reprint-20261122` starts directly from
reviewed Production `c302943e330bcdd534a4a5697b8ef813dfb7cb6e` (PR2059).
Previous task evidence checkpoint adcb4f38cb000c34fef0f4d7e9ea1b0fb6861035 and
all frozen A/B/M pairs remain unchanged. Latest authorization renews publication.

The parser repair is ALREADY deployed inside source
d396049f8fedf72c394169a451d45b227ad52376 / runtime
rg5-aceb392af7809d9099ba92b27d4bb3d802999148c91013a5bba1cca5ad5f6c8e.
Shipping module SHA256 db6401ac99121e6691a17ea0eccd6309d4dca430a6a7782f4b0009d10cf3398f
matches exactly. Do not republish that candidate. Owner evidence checkpoint
529bd8574031e5be30a2319e890796c74f561074 records original verify3 exit0,760
requests/probe and inactive lease. Own fresh original guard also reports inactive.
Both current canonical/cache-busted build-meta probes through the unchanged
guard fetch routine returned200/application-json/exact reviewed SHA256
abbc0f54ce6063852b165e10f4caa7f9b6520624b9412c2162f5569c30e0cb1e.

Actual ADB acceptance still fails: existing-label fetch completes but print
remains disabled. The UI freezes post-dispatch carrier reprints by requiring
the exact completed state. No relink/scope diagnosis follows. The strict native
carrier reader currently accepts only completed despite its existing outbound
status set including shipped/delivering/delivered for other callers.

Added six synthetic actual-route regressions: shipped/delivering/delivered x
refresh/complete-and-refresh with a current official external AWB and durable
assembly proof. Initial run:6 failed in16.58s, all HTTP409
order_status_not_completed. Command: python -B -m pytest --noconftest
--asyncio-mode=auto -q --tb=short backend/tests/test_external_carrier_reprint_status.py.
At this earlier checkpoint no production code had changed. It was not ready for
merge or publication; the current verified source milestone is recorded above.
Fresh rerun after adding pre-result side-effect assertions:6 failed in4.34s,
all at the intended HTTP200 expectation. Prior assertions passed: provider GET
only, unchanged canonical order, unchanged assembly/stage, no manual refresh.

Next: repair only existing external-carrier reprint eligibility. Preserve
assembly/employee permission/current-child/replacement/cancellation fences;
do not relax internal courier document issuance or change Salla statuses,
financial state, linking or scopes. Verify native UI compatibility and the
original/control in ADB after a new reviewed v5 release. Root-case printing is
unverified until actual acceptance. No task lease or new publication; no shared
workspace mutation by this follow-up. Checkpoint/Issue1006 must precede long work.

## Previous parser repair history

Current source branch: `codex/aramex-order-payload-release-v2-20261121`, directly
based on reviewed Production `6e41bf25b936e8c4047df75bb170610dfc71b7f7` (PR2039).
Shipping production/test files are byte-identical to the passed PR2047 candidate.
Its immutable A/B remains intact and is superseded solely because Production
advanced. Generate a fresh source A, intent-only B and runtime identity; never
reuse the old intent or lease. No task lease or publication; Production changed
by this follow-up:no. Next: push/open replacement Draft PR, close PR2047, require
current-base CI, new intent, both host rehearsals and independent ARM64/Linux
validation, then normal merge and authorized guarded publication. Actual target
and control ADB acceptance remains required. Preserve foreign leases/files.

## Superseded PR2047 evidence

Production advanced to `6e41bf25b936e8c4047df75bb170610dfc71b7f7` through unrelated
PR2039 while this candidate's checks ran. PR2047 now conflicts at the intent.
Do not merge or deploy its frozen B; keep A/B unchanged and create a new source
candidate from the new reviewed Production base. Shipping changes are unaffected.
The old B did pass both host20/24 configured clean-clone adapter rehearsals
(readiness44941805161 success) and complete independent ARM64/Linux validation:
adapter0, verifier0, exact identity match,86.92seconds,shared tracked unchanged.
ARM64 proof stage `/tmp/aramex-pr2047-0943-tw4c6c3m` is supplementary; source,
intent,CI artifact and this summary are remotely recoverable. No task lease,
merge,publication or Production change. Actual target acceptance remains pending.

Evidence branch `codex/aramex-order-payload-evidence-20261121` is NOT a
deployment candidate. The immutable release branch is
`codex/aramex-order-payload-recovery-20261121`:

- Reviewed base J: `a9d0aec2939a706dcc16f9948e7a81cfe019e66f`.
- Source A: `41057d6a044d4052205a48aad16bfb3922648e20`.
- Intent-only B: `0943ccb34b36b3a374cc6df0ec28c73219820e11`.
- Runtime id: `rg5-36b2364382c35fcc3f337577b32f97b466e6f59aed2cbf374c88315ee36b691f`.
- Source readiness run44940825251 succeeded. Candidate artifact14722110181,
  ZIP SHA256 `cbd1aac02225bf6b0518b5c4bdfa2907592a36180db47fc147d42feb50ca38d1`.
- Exact intent SHA256 `64bc6bbc357f7ba3a90cb9166fdb4c87931d0f398691acbc9ac164960e5baeef`.
- CI candidate validator passed. Local independent Git Backend/control manifests
  and deterministic runtime id matched. Windows complete filesystem validation
  stopped on NTFS mode0666; no validator was bypassed. Full Linux validation is
  required and is part of the exact-B rehearsals.
- Exact-B readiness run44941805161 and host20/24 clean-clone rehearsals running;
  isolated shared-host ARM64 configured adapter started at exact B. Original
  guard active:false and shared HEAD J/tracked clean were checked before start.
  No task lease, merge or publication. PR2047 is still Draft.
- Prior PR2046 owner records original verify3 success; own guard read independently
  confirmed active:false. No foreign lease was closed or reused.

Next: require full Linux intent/identity validation and all exact-B checks,
then normal merge preserving A/B, authorized guarded publication, original
verify3, and actual target/control ADB label/print acceptance. This follow-up
has not changed Production. The source-stage notes below are historical.

Current user contract: recover official carrier labels through the existing
order-read permission, as with SMSA/iMile. Do not change linking or request
additional scopes. The previous missing-scope diagnosis was not proved and
must not be treated as the required solution.

New task branch `codex/aramex-order-payload-recovery-20261121` is now based on
reviewed Production `a9d0aec2939a706dcc16f9948e7a81cfe019e66f` (PR2046).
Before updating this unfrozen source, verified checkpoint
`f78d69d885805243be5c0f736b64a651af6b1212` was pushed and preserved separately
on `codex/aramex-order-payload-beforebase-20261121`. The source/test files are
byte-identical after updating the base. No frozen task A/B was rewritten.
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
Result:9 failed,41 deselected at the initial regression-only checkpoint; the live
cause remains unverified. The source repairs below are still pending deployment.
Implemented bounded normalization of fresh `shipping.shipment`, singular
`shipment`, and the order-scoped `data.shipment` wrapper. A nested AWB can
inherit its enclosing carrier but not the carrier id as a shipment id. The
same ingestion precedence prevents an old nested AWB overriding top-level
selection/history. Strict reads reject contradictory parents/references and
canonical shipment mismatches. Fresh singular cancellation/return/replacement
is fenced before an older ready list can revive it. An additional regression
first failed (1 failure/60 passed), then passed after this fence was added.

Fresh verification after the source change, in isolated module processes:
- `test_carrier_shipment_read_fallback.py`:111 passed,including ten real native
  HTTP-route cases with optional shipping API denied and no provider/local write.
- `test_amasi_shipping_compat.py`:58 passed and59 subtests passed.
- `test_mobile_external_label_read_parity.py`:19 passed.
- `test_shipping_label_current_guard.py`:29 passed.
- `test_salla_current_shipping.py`:37 passed.
- Total:254 tests and59 subtests; all exit0. `git diff --check` passes.
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
Twelve additional safety cases cover cancelled/return children observed before
an older ready response, explicit order-scoped replacement, and string-zero ids.
The first observed cancellation case failed before its repair (1 failed,99
deselected with maxfail1). Validated negative evidence now blocks only the same
child; a distinct new active child remains usable when no canonical id is known.
The current Production branch advanced to a9d0aec2939a706dcc16f9948e7a81cfe019e66f
through unrelated PR2046. Its owner reports publication/verification in progress.
No shared workspace or foreign lease was changed. The unfrozen task source was
rebased onto that reviewed base before freezing a new intent; no task A/B exists.

Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/2047. Earlier checkpoint
`89a2f16a331cde00be8f9a6b749865078c559039` readiness44935916653 succeeded,
but does not verify subsequent source changes; require fresh current-head CI.
This proves synthetic/public/native contract behavior, not original-order
printing. The workspace probe found no integration matching the current store
and could not match the configured app link. It stopped before token decryption or
any Salla request; it cannot establish Production permissions or absence of an
order. See LIVE-ORDER-READ-CONTEXT.json. No production credentials were exported.

Exact next action: push this source on the current reviewed Production base and
run fresh candidate CI for PR2047. Obtain its governed source-A artifact,
freeze an intent-only B, require
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
