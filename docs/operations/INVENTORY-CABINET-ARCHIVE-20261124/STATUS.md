# INVENTORY-CABINET-ARCHIVE-20261124

## Current state

**HOST REHEARSAL COMPLETE / INDEPENDENT REVIEW PASS — both original adapters, final preservation and exact materialized identity passed. Original prepare/prepublish and production publication remain pending.** No live cabinet has been archived, deleted, disabled or restored by this follow-up. Production has not changed for this task.

Repository: `AMASI-SA/AMASI-SA`.
Task branch: `codex/inventory-cabinet-archive-20261124`.
Verified starting production commit: `c6d05c7814e27e2f22a8c82b5bfe4cf177c17fbe` (PR2118), a descendant of the completed numbering release `cb98e5660380d362e78f937d60abc7139cd1c462` (PR2113).

The latest production change touches operational cost/report code and its release intent; warehouse source files remain unchanged from the completed numbering release. The old release identities and evidence branch remain frozen and must not be reused for this new feature.

## User-directed outcome

The owner asked for short cabinet/column/row labels, chose one row, and said they would create the replacement cabinet before removal of the old cabinet if possible, or another suitable solution. The numbering-only change is already complete. The owner now reports that the new cabinet exists.

During this follow-up the owner asked about resetting the whole hierarchy, then clarified that it is linked to employee permissions and probably services/components. This is not authorization to reset those linked records. Preserve the warehouse and sections. The archive solution targets only the old cabinet, and must reject any relevant live cabinet/location references rather than remapping them without a specific decision.

Fresh live UI observation at `https://mezansalla.com/components-v2?workspace=warehouse` confirms:

- New cabinet **د2**, **دولاب اماسي الافتراضي**, permanent storage, **one row and six columns**; labels **211, 221, 231, 241, 251, 261**, all six reported empty after the occupancy read completed.
- Old cabinet **د1**, **دولاب المراجعه**, three by three; legacy labels **001–009**, all nine reported empty in the UI.
- Neither cabinet's real identifier, full barcode, or numbering has been changed by this follow-up.

UI emptiness is not proof that there are no product or active workflow references. The new server-side action must check its own fresh complete data before any state change.

A read-only check of the existing editor host's configured **local** database found no matching warehouse. It is not the production warehouse evidence and cannot establish absence of live references. No database records were changed and no further database discovery is needed for this task; use the new server-side validation on the actual owner-scoped request.

## Verified gap and chosen solution

Read-only inspection found no supported individual cabinet delete, archive, restore or metadata-update operation. The existing `warehouse-locations-v2/reset` deletes the owner's entire storage hierarchy and counters, including the new cabinet; it is outside this task and must never be used here. The existing single-location disable operation has no restoration UI and leaves the cabinet visible.

Implement **reversible cabinet archive and restore**. Archive keeps records and historical references, hides the cabinet from active choices, and disables its empty locations with restoration metadata. Restore retains the same identities and labels, and returns each location to its exact pre-archive empty/disabled state. No hard deletion, counter reset, number reuse, stock quantity change, financial write or automatic migration is in scope.

## Change contract

- Manager-scoped `POST /warehouse-locations-v2/cabinets/{cabinet_id}/archive` and `/restore`, without a request body.
- Manager-scoped `GET /warehouse-locations-v2/warehouses/{warehouse_id}/archived-cabinets`, returning `items` and `total`.
- Complete slot-set, tenant, permission, occupancy, active-reference and parent-state validation. Unknown, inconsistent or busy state fails closed.
- Cabinet, child-state metadata and audit event changes are atomic, using a restricted existing operational transaction capability. No financial/control writes are introduced.
- Fix directly relevant stale-write seams so an old screen or concurrent placement cannot refill archived slots. Retain diagnostic/historical reads, while active selectors omit archived locations.
- Manager UI provides exact-target confirmation, archive errors, an accessible archived-cabinets list and restoration. The new cabinet remains untouched.

## Ownership and verification plan

Root coordinates integration, docs, GitHub and any separately governed release. `numbering_candidate_build` owns backend code and focused tests. `cabinet_archive_frontend` owns the warehouse page, its styles and focused tests. `numbering_release_review` independently reviews referential integrity, persistence and concurrency. All work uses the same task branch; no agent may modify another writer's files or operate production.

Required evidence: focused public-behavior tests; real Mongo transaction, rollback and concurrency checks; frontend cancellation, errors, exact target and restore tests; combined diff review; applicable CI/build/release gates; live verification of only the intended old cabinet after publication. Preserve all unrelated source work and production release leases.

## Verified implementation

The warehouse page now provides an exact-target archive confirmation, archived-cabinet reader, restoration, server rejection messages, duplicate-submit prevention, stale-read protection and saved-write/failed-refresh handling. The list remains reachable after archiving the last active cabinet. Delegated access cannot expose manager lifecycle controls.

Changed frontend paths: `frontend/src/pages/WarehouseHierarchyWorkspace.jsx`, `frontend/src/pages/warehouseHierarchy.css`, and new `frontend/src/pages/WarehouseHierarchyWorkspace.archive.test.jsx`.

The frontend implementer ran both warehouse workspace suites with the existing installed dependencies: **26/26 tests passed, two suites, exit 0**, including the unchanged 14 numbering/creation/print tests. The initial regression failed because the archive control was absent. Root then ran both suites through the original `frontend_release_toolchain.py exec` on the required Node 22.23.2/Yarn 1.22.22: **26/26 passed, two suites, exit 0, 5.699 seconds**. `git diff --check` passed. The three frontend paths were durably checkpointed at `ea37eab77b7054ce98438b002958d9e94c87a6ec`.

Backend implementation and focused tests are complete. `warehouse_cabinet_lifecycle.py` adds the three routes and restricted `warehouse_lifecycle` transaction profile. Existing warehouse readers and directly relevant bundle, receipt, opening, stocktake and component paths exclude archived slots. Stale placement uses state/occupancy compare-and-set and checks the matched count. Current bundle pins are validated on save inside the shared owner transaction; frozen history remains readable.

The backend author ran the two new archive test files on an exclusively owned MongoDB 7.0.14 replica: **73 passed in 15.60 seconds, exit 0, no skips**. The raw log is retained as `backend-focused.log` beside this document. Its SHA-256 is `f69a5756be355512dcdcc39d3570254f17e781a24e90c46e44b16aa4af02812f`.

The real transaction tests cover archive/restore with preserved parents, employee assignments, new cabinet, identities and history; active references; scope and manager boundaries; complete slot sets; rollback of both operations on audit failure; repeated concurrent requests; both orders of the placement/archive race; refused receipt initialization; current bundle pins and readable frozen history; and malformed markers. Legacy `current_quantity`, conflicting section/room IDs and non-text or blank parent aliases fail closed before writing.

Independent review of the final backend, frontend and new `.github/workflows/warehouse-cabinet-archive.yml` reports **PASS, no remaining source blockers**. The reviewer read the raw 73-test log and matched these source SHA-256 values:

| Path | SHA-256 |
| --- | --- |
| `backend/warehouse_cabinet_lifecycle.py` | `1bc7d2d75a0b2b5cf1275506be5c1817e45269924c4d03770b606e8213c39d9c` |
| `backend/tests/test_warehouse_cabinet_archive.py` | `f39ccc99e251b6c1c68802c29bba0e056ca0681267af23eb495a07d2cce869d8` |
| `backend/tests/test_warehouse_cabinet_archive_mongo.py` | `2ea8b852c32b2bba0bc59e35b128dee96723c56b19c71007d5b4dfc2d1a92268` |

The new workflow checks the actual PR head, uses a checksum-pinned owned Mongo binary with no external URI fallback, and rejects skipped/failing archive tests. It also runs the two frontend suites on the required Node version. Existing gates are unchanged. All source writers have stopped. The original governed build and intent verification now pass. Final-head CI has no failures; its current state is recorded in checkpoint E2 below. There has been no lease, publish or live mutation for this follow-up.

## Next safe action

Execute only the independently reviewed immutable operator/envelope after its fresh original guard and host checks. D is merged, tree-equivalent to B, and all exact-D push gates passed. Complete the original release protocol from the reviewed immutable packet. Do not archive live data until publication and three-probe identity verification succeed. Then use the manager UI to archive only old d1 and verify the replacement d2 and warehouse/section structure. If a live reference blocks d1, preserve it and report the concrete blocker. Source review is not evidence of publication or absence of live links.

## Release checkpoint E1 — 2026-11-24 21:01 UTC

Reviewed source A: `b236f51bb6c52e6f5d14a136db92f7474e4f5873`; tree `0f86e247f16bdf6aef45561fb6c92be3d88d37b4`; prior governed production J: `c6d05c7814e27e2f22a8c82b5bfe4cf177c17fbe`. Remote task ref and local clean HEAD agree. The equivalent local checkpoint remains preserved on `codex/inventory-cabinet-archive-local-checkpoint-20261124`.

Root ran the archive and numbering Mongo tests, short-code and warehouse generation tests, existing bundle catalog tests, and product receipt tests on this exact clean A: **189 passed in 18.82 seconds, exit 0**. The JUnit XML confirms 189 cases with zero skips/errors/failures. Logs, XML, exact commands and the 27-path source snapshot are retained beside this document. The original `/app` release guard `status` command completed and displayed `active:false` before release bootstrap. No lease was created and no `/app` source or production data was changed by this task.

Next: original governed A/B artifact build, freeze an external intent against J/A, create intent-only B, final-head CI and review, normal merge, clean adapter rehearsal, then prepare/prepublish only for the authorized publish. Keep all later status updates on this separate evidence branch; source A is frozen and the deployment task branch may change only its release intent after A.

## Release checkpoint E2 — 2026-11-24

Intent-only B: `bdebc069ea5fa980cd16725e422ca70e79dadd17`; tree `74946da23e059283f49de2b04baf9860c8ee972d`. Remote and local A..B comparison contains exactly `release/release-intent-v5.json`. B has A as its sole parent. PR [#2120](https://github.com/AMASI-SA/AMASI-SA/pull/2120) is Ready. Production branch still points to J at the latest read; no D has been created.

The original governed build completed two identical clean passes, exit 0. Original A verification, external intent freeze, candidate-intent validation, and original B verification with `--reviewed-intent-v5` all passed. Runtime identity is `rg5-6cfb0e514bb3c65c229a9d224c329f24136bd0ce483bed1af3b74b05e848ca42`. Intent SHA-256 is `f27abedb9ff7ecf9cc8a3c92ec127628a6091842db522e81e1f79b40cdd2a57e`. The original build and B-verification logs are retained beside this document; exact artifact/meta/proof identities and all log hashes are recorded in `release-validation.json`.

Independent immutable-packet review passed: all 3,137 governed source records and 382 public artifact files plus metadata/proof match, critical backend hashes are unchanged, runtime identity was independently rederived, and B changes only intent. Original CI adapter rehearsals on host Node 20 and 24 both passed (jobs 138780195796 and 138780195566, run 45487964006), including clean deterministic builds and isolated package proofs. The new archive backend and frontend jobs passed. Connected-source, stock, accounting and Mongo gates passed; the latest root read has 53 successes, 10 conditional skips, and only Operational Balance Backend still running. Do not treat this checkpoint as all-green or production evidence.

Fresh read-only host inspection showed tracked-clean J, /app available space 422,457,344 bytes, and /tmp available space 236,544,008,192 bytes. Use an independently reviewed operator with owned staging under /tmp and measured space checks; preserve all foreign files/worktrees. The operator is still being prepared. Original guard status must be read again before host mutation/rehearsal. No /app source, service, database, lease, or live warehouse mutation has been made by this task.

Next: remaining CI, reviewed operator, normal merge and D/tree verification, clean original adapter rehearsal, original prepare/prepublish for authorized publication, explicit new Deployment Succeeded, original three-probe verification and closed lease, then archive only d1 through the manager UI if the live reference guards allow it.

## Release checkpoint E3 — merged, unpublished

Root independently read all 64 final B checks: **54 success and 10 conditional skips**, zero running, queued, neutral or failed. PR #2120 was Ready, mergeable and clean, with exact head B and base J; production ref was still J. Root used normal merge with expected B. Resulting D is `f199a471f8bc6e05b475bb9b65a12107cbdc946b`, tree `74946da23e059283f49de2b04baf9860c8ee972d`, parents J then B. API comparison confirms A is an ancestor and A..D changes exactly the intent file. The immutable source, runtime identity, artifact, metadata and proof remain those in E2. See `merge-D.json` and `ci-head-B-final.json` for the retained records.

Post-merge D CI is running. A fresh host read showed HEAD J, clean tracked files, 432 nonignored untracked entries, and regular platform runtime metadata. Free space was 421,769,216 bytes at /app and 237,201,190,912 bytes at /tmp. No /app source or services have changed, no lease has been created, and no production publication or cabinet mutation has occurred. The independently reviewed operator must use owned /tmp staging and recheck all host facts before action.

Next: complete exact-D gates, finish operator review and immutable envelope, retain that evidence remotely, then run the original clean adapter rehearsal and /app adapter under the original guard operation lock. Prepare/prepublish and publication remain later steps; do not archive d1 before successful publication and original three-probe verification.

## Release checkpoint E4 — all gates passed, operator frozen

Root independently fetched all **11 exact-D checks, each completed successfully**; production branch still equals D `f199a471f8bc6e05b475bb9b65a12107cbdc946b`. The independent CI reviewer confirms all three push workflows passed with no skipped, pending, neutral or failed checks, including both actual host clean-clone adapter jobs. The final B CI review (93,735 bytes, SHA-256 `fd335feb80744742f018a9faca4fb50fd4a71a34197e88ff5bc572cb6642e909`) is retained as `pr2120-exact-b-ci-review.json`.

The external operator `cabinet_archive_release_operator.py` is 35,827 bytes, SHA-256 `18a8cf2e09605947263999cd897ac680507c4fbbabffc0ffb4568f606ed6b703`; AST and independent full static review passed. The exact nine-field envelope `cabinet_archive_release_envelope.json` is 532 bytes, SHA-256 `c67cbf86ad270185505c7cb8653e741b968a07ed917c9e9ed7762bbc0d06f37b`; independent review passed. It pins A/J/B/D/tree/runtime, initial host J, and 432 nonignored entries. Only the platform names metadata may append while preserving inode/device/mode and the full original prefix; every other foreign fingerprint must remain exact. The helper rejects Python optimization, takes the original release operation lock after public inactive status, builds through the unmodified adapter in an owned /tmp clone, measures capacity, backs up and verifies the original generated paths, then FFs /app and uses the original adapter there. It does not prepare a lease, publish, restart services, recalculate or perform capacity cleanup. The full frontend/.release cleanup target was confirmed against original adapter code; no release control was modified.

The platform baseline is published version `eeecd3f` (100th publish), with visible “Deployment finished Nov 24, 11:41 PM” Riyadh. The live warehouse page still shows d1 with nine empty slots and unchanged parent hierarchy. No publication or cabinet mutation has been submitted by this task. Exact checks, reviews and host prerequisites are retained in `operator-review.json` and `ci-deployment-D-final.json`.

Next: download this commit's helper and envelope into a new owned /tmp directory, verify both SHA-256 values before execution, and run from /app. Any guard, lineage, foreign-file or capacity rejection stops the operator. Only a successful owned-clone and canonical /app original adapter receipt permits original prepare/prepublish, one authorized publish, explicit newer Deployment Succeeded and three verified probes. Live d1 archive remains after those gates.

## Release checkpoint E5 — operator started

Both E4 files were downloaded into owned `/tmp/mezan-cabinet-archive-operator-ezig9yqb` and independently SHA-256 checked before execution. The frozen helper is now running in root's own Terminal 1. Its stage is `/tmp/mezan-cabinet-archive-release-3yhk4dkk`; receipt is `receipt.json` within that directory. The observed original public guard result is `active:false`. Its current receipt reports RUNNING after clean expected-J and foreign-path enumeration checks; the initial full fingerprint snapshot was still in progress at this checkpoint. No lease, prepare, prepublish, publication or live data action has occurred.

The complete independent exact-D push CI interpretation is now retained as `pr2120-exact-d-push-ci-review.json` (49,836 bytes, SHA-256 `ae3c4ec285201b1dd2e746f91aac3b22e466690f8652003fe9c3bf877d2a5ef4`). All 11 checks and all three actual push workflows passed on exact D; there were no retries, manual dispatches, failures, skips or pending checks.

Resume by reading the existing receipt and process first. Never start a duplicate helper or treat a partial run as a successful rehearsal. Public guard status takes the operation lock and must not be called while this helper owns it. Use read-only receipt observation from the separate root-owned Terminal 4. Root will inspect complete original adapter, foreign-preservation and capacity evidence before any lease preparation.

## Release checkpoint E6 — first attempt preserved, live-service boundary identified

The first operator ended **exit 2 / BLOCKED:FOREIGN_ENTRY_CHANGED** after its owned clean-clone original adapter and all frontend/package/full-proof checks passed. All 60 recorded subprocess steps passed; the outer custom foreign-record comparison rejected the later live change. The exact complete 12,379-byte receipt is retained as `host-operator-v1-blocked-receipt.json`, SHA-256 `7816922a0e82f264107c9cf0f898788176581cf997d030fe45f4d47d82cfa780`, reconstructed from the browser editor copy and cryptographically checked. Its backups list is empty and it contains no source fast-forward or canonical /app adapter step. Host HEAD remains J and the original public guard freshly reports active:false. There is no lease, publication or live cabinet mutation.

Read-only metadata comparison identified size changes in the separate `.worktrees/p01-acceptance-state-20260919/mongo` diagnostic files and sibling `mongo.log`. The existing mongod process is PID96507/start106766690 with that exact dbpath and port27018; executable and command-line identities are recorded without command-line values. The log's old 19,284,522-byte prefix still matches exactly. Its actual open writable descriptor belongs to supervisord PID138/start106316697/fd30; mongod stdout/stderr are pipes. No database query, service stop/restart or foreign file write was performed. The comparison covered 193,825 records, 47 ignored roots and 432 nonignored entries; it does not prove unchanged live database bytes. Normal Git fetch/guard-lock metadata writes occurred.

The writer and independent reviewer agreed a narrow recovery contract: explicitly bind only that live dbpath to the observed process and real root identities, preserve its active service while allowing its own data descendants to change, and keep the sibling log append-only with unchanged inode/mode and original prefix. All other foreign files remain exact, and all incoming-source/adapter/staging write paths must be disjoint. Original A/J/B/D, CI, guard, adapter and artifact verification remain unchanged. This is an adjustment to the external helper's live-file preservation model, not a bypass of the original release controls. The original helper/envelope/receipt remain immutable. New separate files are being implemented and must be independently reviewed, remotely saved and hash-verified before a fresh run.

The actual first download directory is `/tmp/mezan-cabinet-archive-operator-ezig9yqb` (g). Earlier handwritten E5 continuation text contained a q; the current record corrects that transcription. Read `host-operator-v1-observation.json` for exact process/root/log evidence and scope. Do not restart v1, reuse the partial attempt as full rehearsal proof, or prepare a lease yet.

## Release checkpoint E7 — narrow recovery pair reviewed and frozen

The separate helper `cabinet_archive_release_operator_live_scope.py` is 43,560 bytes, SHA-256 `1c366e6add99c1560243e53660a34df8c4121ead56c1246656dfb9afc0a7f14f`. The separate envelope `cabinet_archive_release_envelope_live_scope.json` is 1,366 bytes, SHA-256 `b06bd5ecb519f4f485e959c0006ad6baf31e77dd911f6599382ef82c8113c9c5`. Root checked both hashes and the complete delta. Both independent preservation reviews and AST validation passed. The original v1 pair remains byte-for-byte unchanged. All nine original envelope fields are unchanged, with one exact `live_foreign_scope` object added; its literal matches the pinned process/root evidence in E6.

The scope exclusion uses the exact slash-delimited Mongo data subtree only. It cannot include sibling files or `mongo.log*` names. The independent log keeps its original prefix through bounded reads with O_NOFOLLOW and stable dev/inode/mode/ownership; truncation, replacement or rotation rejects the run. Non-live inventory/record sets and bytes remain exact. Both lexical and resolved path checks reject overlaps with source changes, adapter cleanup targets, Git metadata and owned stage writes. Root confirmed the live scope is under ignored `.worktrees`, not a nonignored tracked-candidate path, and the actual command has a separate `--dbpath` argument. No original release controls, A/B/D source, tests or CI gates changed.

The latest remote production ref still equals D. E7 is not execution or publication proof. Next: download only this commit's new pair into a new owned /tmp directory, verify exact hashes, run from /app and require fresh original inactive guard, current identities and complete clone/canonical adapter receipts. Preserve v1 and its owned stage. No lease may be prepared until the new canonical rehearsal completes successfully.

## Release checkpoint E8 — fresh scoped run started

Root downloaded the exact E7 pair into owned `/tmp/mezan-cabinet-archive-operator-live-1c_0ba3x`, verified both reviewed SHA-256 values, and executed it once from /app. Its separate owned stage is `/tmp/mezan-cabinet-archive-release-live-e7l6f9wi`. The current receipt reports RUNNING, the fresh original public guard reports active:false, expected tracked-source checks and foreign-inventory enumeration passed, and the initial fingerprint snapshot is in progress. The E7 live-process/root identity checks passed before the stage was created. No lease, publication or live cabinet action has been submitted.

The v1 stage and BLOCKED receipt remain preserved. Observe the existing second process through its `receipt.json`; do not start a duplicate, run a blocking public guard command while its lock is held, or reuse v1 as final rehearsal proof. Next is the successful second owned-clone and canonical /app original adapter receipt, followed by independent receipt review and original prepare/prepublish for one authorized publication.

## Release checkpoint E9 — backups and measured capacity passed

The second owned clean-clone original adapter passed. The first complete foreign-preservation check passed with 185,030 records and 185,028 identical records; only the two declared append-only files changed. The bound live database root identity stayed exact, and its descendants were not read or compared. The independent log retained its full original 19,298,866-byte prefix with SHA-256 `118dc34225f6df08454af22e0d66092ed9ee560f3071fbb85f70b903e8d23ed6`.

All four inherited generated-output backups are now COPY_VERIFIED: frontend/build (390 entries), frontend/.release (2), backend/release_identity.json (1), and frontend/node_modules (76,640). The fresh pre-fast-forward capacity check passed: required 661,082,112 bytes versus estimated available after original adapter cleanup 1,075,732,480 bytes. No capacity cleanup or foreign service changes were used. The next fresh foreign snapshot was running at this observation; the reviewed helper will proceed to FF and the canonical /app adapter only if its remaining checks pass. See `host-live-scope-midpoint.json`.

The run is still the single E8 process at `/tmp/mezan-cabinet-archive-release-live-e7l6f9wi`. Do not restart it or prepare a lease from this midpoint. Full canonical adapter completion, final preservation/identity checks, independent receipt review and original publication protocol remain pending. Production publication and live cabinet mutation have not occurred.


## Release checkpoint E10 — complete host rehearsal and independent review PASS

The single E8 scoped operator completed with visible `FINAL_STATUS=PASS` and `OPERATOR_EXIT=0`. Its complete `host-operator-v2-pass-receipt.json` is exactly 24,058 bytes, SHA-256 `9cac2d230262596f1c7ae2a067bde25803c4b7d9bacc5181aaae8c1aae8f3fc6`. The editor-copy transfer was decoded and checked against the host size/hash. All 95 recorded subprocess steps exited zero; both the owned clean-clone and canonical /app original adapters and their frontend/package/full-proof validation passed. No duplicate operator was launched.

All four generated-output backups remain COPY_VERIFIED. The before-FF capacity check required 661,082,112 bytes versus 1,075,732,480 available after original adapter cleanup; the before-canonical check required 658,661,376 versus 1,075,462,144. Final foreign preservation covers 185,030 records, 185,028 identical records, 432 nonignored paths and 47 ignored roots. Both declared append-only files retained their original prefixes; the live log was 19,307,746 bytes at the final observation. The pinned Mongo process/root remained bound; live database descendants were intentionally neither read nor compared. This is not a claim that a running database's bytes stayed frozen.

Independent final receipt review PASS checked the complete receipt against the unchanged E7 helper/envelope and all A/B/D/J/tree/RID identities. No lease, publication, rollback or recalculation occurred in the rehearsal. The earlier v1 BLOCKED receipt remains preserved and is not reclassified.

The original public guard subsequently returned `active:false`; fresh host HEAD is D on hotfix/prod-snap-meta-final with no tracked changes. At 22:24:29 UTC the three materialized files matched exact expected sizes/hashes: backend/release_identity.json 504,391 bytes / `ea81c0571b05bf9263f1fdb0f69ffd82385128fcbc5fb683dac383626983a97b`; frontend/build/build-meta.json 592,074 / `b2e828e01da0de58924dba940e13759ce0e26de36741b7c131d8456fafc36aa2`; frontend/.release/reproducible-build.json 1,521 / `bf0913d7c43a19332e0e786547e696ff17d68e77d6dacef7263f9d1011a83ff8`. See `host-post-rehearsal-identity.json`. A supplemental read-only check first used a mistaken proof filename and returned FileNotFound; it was corrected from the original adapter constant. No generated artifact or source was edited.

The observed platform baseline remains the 100th publish eeecd3f, last finished Nov24 11:41PM Riyadh. No publication has been submitted for PR2120 and no cabinet mutation has occurred. Next: original prepare with task actor, fresh original prepublish, one authorized platform publication, a newer explicit Deployment Succeeded, then original three-probe production verify and closed-lease status before guarded live d1 archive. Do not rerun completed tests or alter frozen source.
