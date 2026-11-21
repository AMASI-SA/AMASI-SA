# ARAMEX-PRINT-20261121: PR2048 frozen A/B verification

LATEST USER REQUEST: release the deployment reservation. Fresh unchanged
original guard status confirmed `active:false`, with no actor/source/deployment
or release id. Our prior unused reservation was already aborted successfully;
no additional abort or foreign lease removal was needed. The stale canonical
prepared-reservation entry must be replaced by this remotely verified state.
Reservation-release request is complete; Aramex live acceptance remains pending.

CURRENT PUBLICATION COORDINATION: user confirmed another session is publishing.
This task NEVER clicked Republish/Publish changes for PR2048. UI unexpectedly
showed newer version0ef4d22; public health20:36UTC was old bank source
db141c0438edf04aa77ccd1122bc9c5008cf39d6 / release
rg5-500857bae382758ba238083f1f7f417b23a7e2dfb298aa3e2e99e7f4baf56b45,
boot20:35:24UTC, three identity flags true; build-meta returned403 text/plain.
That is not the Aramex release. Do not attribute it to this task or report a fix.
To avoid concurrent publishers, the owner checked actor/A/M/id, then used the
unchanged original abort with exact M/id to close ONLY our unused prepared
lease (exit0,aborted:true). No foreign lease was cleared. No own lease remains;
global state may subsequently belong to the other publisher.
Aramex fix remains normally merged at M and passed all CI/ARM/shared checks.
Expected shipping file SHA256 is
`db6401ac99121e6691a17ea0eccd6309d4dca430a6a7782f4b0009d10cf3398f`;
see SHIPPING-FIX-IDENTITY.json. The next reviewed release must retain this fix.
Next: read-only monitoring while the other publisher finishes; verify its actual
source includes the fix, adopt an already matching verified release rather than
publish again, and test original/control via ADB. If another own publication is
still necessary, recheck reviewed origin/source/intent and original inactive
guard first; never reuse the aborted lease. Actual original print is unverified.
The preparation/release notes below are historical.

HISTORICAL PREPARATION (subsequently aborted): own protocol-v5 lease PREPARED, actor
`codex-aramex-pr2048-authorized`, source A/deployment M/runtime id below. Original
prepare exited0 with exact A/M/id/protocol5. Do not clear or reuse this lease.
Reviewed helper c958ee3dff1cbd5518951df2cca1950d4efb014a blob SHA256
`33e0fd9f800e3f30e8d310156053382bf9e49537f265ee902a5adad5ddd023f5`
was fetched as bytes only and executed from /tmp. It checked inactive guard,
fast-forwarded shared /app to M, preserved previous outputs under stage
`previous-generated`, installed only the three governed outputs, and passed
the shared verifier0/exact identity check with clean tracked M. No unrelated
files were removed; evidence branch was never checked out in /app.
Live runtime changed by this follow-up:NO so far. Next: open the republish
confirmation, run original `python -B scripts/production_release_guard.py
prepublish` immediately before the already authorized final Publish changes
click. Require ready_to_publish:true and exact A/M/id, click once, observe a
new explicit success, then unchanged original verify3 and ADB target/control
label/print acceptance. Update evidence and lease state after those outcomes.

Independent current-B ARM64 rehearsal PASSED after foreign verify completed:
original guard active:false, complete Linux source validator passed, unchanged
configured `cd frontend && yarn build` adapter0, separate verifier0, exact
runtime identity matched,82.63seconds,shared tracked unchanged. Stage pointer
`/tmp/aramex-pr2048-stage.txt` currently selects
`/tmp/aramex-pr2048-4c5c-7ifxp_h0`. Source/intent/deployment IDs remain below.
Merged-M readiness44945492343 also succeeded. No own lease or runtime publication.
Next: run the reviewed materialize-pr2048.py helper from /tmp using its exact
evidence-commit bytes; it checks fresh inactive guard and origin M, preserves
prior governed outputs, fast-forwards only M, copies the three verified outputs,
and verifies the shared contract. It creates no lease/publish. Only after its
success prepare the unique owner lease, then prepublish immediately before the
authorized final publish. Preserve all unrelated files and any foreign lease.

PR2048 MERGED normally at `88af38f01cf979acbca59b95a95e661828f2306f`
(20:17:30UTC). Fresh pre-merge Production J and all39 exact-B checks were
verified. M parents are J/B, M tree equals B, A is an ancestor, and A-to-M changes
only the intent. See MERGE-PROOF-2048.json. Frozen A/B was not rewritten.
Production changed: GitHub branch YES; live runtime NO. No shared checkout,
artifacts or foreign lease were changed by this merge. The original foreign
verify operates on its frozen lease and public HTTP probes, independently of
the GitHub branch. It continues while the reviewed merge is preserved remotely.
Independent ARM64 rehearsal remains mandatory before shared materialization,
prepare/prepublish and publication; all actual protocol gates remain in place.

Current evidence branch `codex/aramex-order-payload-evidence-v2-20261121` must
NOT be deployed. Immutable release branch `codex/aramex-order-payload-release-v2-20261121`:

- J `6e41bf25b936e8c4047df75bb170610dfc71b7f7`.
- A `bc01a705f5bbcb58be8dab5eef35ace8516cface`.
- Intent-only B `4c5c778a9ee76d230f8fc85908e085860656622f`.
- Runtime `rg5-827ca29c8410a99680fce32c0e1b953e7870dfef0051994a24c365590abf8715`.
- Source readiness44942609937 passed; artifact14722013947 ZIP SHA256
  `69b188af180411836c8ca63d203a2219a18f36ae7dd572cf9d998e2e323b7934`.
- Intent SHA256 `1cb1ca09cf311dbb901da7adb21c074dc1b90c261596e555eba81c10894a0a40`.
- CI candidate full validator passed; independent local Git manifests and
  deterministic id matched. Exact-B readiness44942990880 PASSED, including both
  host20/24 clean-clone adapter rehearsals. All39 current-head checks completed
  with zero failure/pending; PR2048 is ready for review and mergeable/CLEAN.
  Original guard at20:03UTC still reports the foreign PR2039 lease active.
- Fresh independent ARM64/Linux rehearsal stopped at its initial guard check
  before any fetch/clone/build: active:true, actor codex-pr2039-owner-authorized,
  source e08d0e489eded39961d374e4eb7bfcc0544b6e90, deployment J. Shared HEAD J.
  Stop result in `/tmp/aramex-pr2048-4c5c-jwr80pae`; no adapter output or shared
  tracked mutation. Do not clear/reuse the foreign lease or start another
  rehearsal until fresh original guard status reports active:false.
- No task lease or publication. PR2048 ready; PR2047 superseded/closed, its A/B
  preserved. The earlier inactive observation preceded the foreign prepare;
  the in-job fresh check correctly stopped the race.
- Foreign original verify process observed running at20:09UTC. Its operation
  lock makes concurrent original status wait; our bounded read timed out45s,
  without changing or clearing the lease. Monitor process completion, then use
  original status before any rehearsal or shared mutation.
- Supplementary Mongo read-only context probe on workspace J found zero target
  and zero control canonical rows; this workspace is not established as the
  Production order context. No token access, Salla call or DB/shared tracked
  write. LIVE-ORDER-READ-CONTEXT.json records sanitized role counts only.
- Fresh ADB: emulator online, authenticated Mezan screen visible. No new-code
  original target acceptance yet. Existing linking/scopes/tokens unchanged.

Next: wait for PR2039 owner to complete verification and fresh original guard
active:false, then rerun independent full Linux validator/adapter at exact B
(M tree-equivalent), recheck origin Production M, materialize only M with the
verified artifacts, prepare/prepublish and authorized publication. Original
verify3 and actual target/control ADB label/print acceptance are mandatory.
The follow-up changed only GitHub Production so far; live runtime unchanged.
The following source-stage and old-candidate notes are historical.

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
