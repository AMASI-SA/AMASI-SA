# Catalog memory recovery — publication and required verification complete

## Current release and continuation state

[PR #2055](https://github.com/AMASI-SA/AMASI-SA/pull/2055) was normally merged
at `2026-11-21T23:35:21Z` as
`0c5e5b98795b8d30289ec0e4d3c95738fd660af1` (M2). All required checks on exact
B2 carrying source A2 are accepted: **20 workflows = 19 successes and one
conditional TikTok skip; 43 check runs = 37 successes and six expected
conditional skips; zero active and zero failed**.

**Production publication and required original release verification are
complete.** The explicitly authorized publication produced the newer green
Emergent `Deployment finished` event. The unchanged original verifier finished
with exit `0`, `verified=true` and `checks=3` for exact A2/M2/R2; its matching
lease automatically closed and original status returned `active: false`,
`actor: null`. Initial authenticated dashboard sections rendered. Precise
load duration and sustained/per-request performance remain unmeasured; no
blanket incident resolution is claimed. The dated rollback result below is
historical evidence.

- Task: `CATALOG-MEMORY-RECOVERY-20261122`.
- Repository: `AMASI-SA/AMASI-SA`.
- Reviewed implementation branch: `codex/dashboard-catalog-memory-r2-20261122`.
- Documentation evidence branch: `codex/catalog-memory-release-evidence-20261122`.
- Evidence branch base: exact M2 `0c5e5b98795b8d30289ec0e4d3c95738fd660af1`.
- **NEVER DEPLOY THE EVIDENCE BRANCH.** Documentation changes after M2 are
  outside the frozen A2/B2 deployment contract. The deployment target is M2.

The machine-readable companion is `release-continuation-20261122.json`.
Historical failed-candidate and source checkpoints below are explicitly labeled.

## Active R2 source, intent and proof

| Contract | Exact identity |
|---|---|
| Source base | `10ee7b41204dd1d39c8b91daf9f0a9d899041ebb` |
| Source A2 | `8665e8acf1d7bb4878cd2fc0b912a20132f1e0c7` |
| A2 tree | `bd5f5d5a8cac347b7670b3e78b7e0b7a8f6b67b8` |
| A2 sole parent | `f13101cd78808ca0de9bb14352efd475a87eb8c4` |
| Intent B2 | `1ce536a61440da42f8f7ede9eb55d86cb29de5ec` |
| B2 tree | `128537a80bcd08f93744f042d6c5927801188a59` |
| Normal merge M2 / reviewed deployment | `0c5e5b98795b8d30289ec0e4d3c95738fd660af1` |
| M2 tree | `128537a80bcd08f93744f042d6c5927801188a59` |
| Intent Git blob | `639befdbcfadecc2c2bebbc310a843b578e9badd` |
| Intent raw SHA-256 | `0a8b06b82b9e00aa2b5df997e7336da9a250fd16e0d4b8671218de418ddfac5a` |
| Intent bytes | `1444324` |
| Runtime release ID | `rg5-4d0f595ee0711c73459e0cb18711c87657602d7886a885f0ccb1a3013e31dc05` |
| Frontend artifact tree | `770915f10cbbaa9b17e129ee8ba544c4c618dd539600de519f7f70b48c80540a` |
| Build-meta SHA-256 | `e5b04aab5c831ffa5e7b5d990289cf7da188812eb4d98d4119b030c74ccd2b61` |
| Reproducibility proof SHA-256 | `5d54d316731587985a3e997d09f06febd45fc23b150d6f0b085a89917f8dd518` |

Independent fresh remote reads verify A2's parent/tree and its exact two-file
delta from original A: the cancellation fixture and this task's source status
record. R2 branches before failed B; source base M's original intent bytes and
intent-free source history are preserved. The runtime remains byte-identical,
SHA-256 `0d9f4fb40beebbaf8aa27c6cb51d962913cfa2d8399afb82bea1a2e52ac42feb`.

Fresh remote B2 commit/comparison/intent reads verify sole parent A2, one commit
ahead and zero behind, with only `release/release-intent-v5.json` changed. The
intent binds the exact A2, base, runtime ID, artifact and new proof above.
No failed-candidate commit was amended, rebased or squashed.

Root reported original two-pass build, source verification and freeze exit `0`,
using Node `22.23.2` and Yarn `1.22.22` (governed Vite `8.2.4`). Both clean passes
have the identical artifact tree and build-meta hash; pass two is retained.
Build-meta is `549655` bytes; proof is `1521` bytes. Although the Frontend
artifact tree is unchanged, the A2-bound metadata, proof and release ID are new;
old candidate proof must not be reused. Original exact-B2 intent loading also
passed with exit `0`, matching Git parent/path/tree/raw-hash checks and a clean
checkout. Local preparation records are `catalog-r2-reviewed-intent-v5.json`,
`catalog-r2-source-build.log` and `catalog-r2-source-verify.json`.

## Final merge verification

Independent fresh remote commit/comparison reads and root's completed local
checks agree: M2 has parents
`10ee7b41204dd1d39c8b91daf9f0a9d899041ebb` and
`1ce536a61440da42f8f7ede9eb55d86cb29de5ec`; its tree equals B2 exactly.
B2..M2 has no content changes. A2 is an ancestor and A2..M2 changes only
`release/release-intent-v5.json`. Source and intent were preserved without
squashing or rebasing. The original `load_release_intent()` on exact M2 passed
with exit `0`, exact A2/runtime identity, and a clean checkout.

## R2 correction and final verification

The test-only correction identifies the original request-owned current-bundle
estimate query by exact owner, alias-ID scope (`rose`, `shawl`) and projection.
It does not identify a cursor by its ordinal among unrelated dashboard reads.
The original worker-drain, cursor-close and lease-release assertions remain
intact. A new negative-control test rejects direct alias/hydration reads and
altered owner, projection or scope. It does not extend the owned-reader
guarantee to the separate direct catalog reads.

Focused verification passed **217 tests**. An independent overlapping
**33-test** suite passed and the minimal semantic correction was independently
reviewed; these counts are not additive. The runtime and previously reviewed
gate implementation remain unchanged.
The corrected cancellation fixture SHA-256 is
`1938731b2cf811bd92060dc56091f1ba4d27dd1522c5a6250132b8fce40062ee`.

Exact-B2 readiness run
<https://github.com/AMASI-SA/AMASI-SA/actions/runs/44961507288>
completed successfully. All six required jobs passed; the manual-redeploy
notification job was conditionally skipped. Root reviewed both clean-clone logs:

- [Host 20 adapter job 137021426657](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44961507288/job/137021426657): success.
- [Host 24 adapter job 137021426603](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44961507288/job/137021426603): success.

The logs confirm materialization without Git metadata, the pinned governed
build, exact A2/release/artifact/build-meta/proof matches, verified isolated
Backend health/package behavior and verified isolated Frontend runtime artifacts.
These are CI proofs; no live Emergent publication or snapshot timing is inferred.

Exact-B2 runtime-adoption run
<https://github.com/AMASI-SA/AMASI-SA/actions/runs/44961507413>,
job `137021092980`, also completed successfully. Root read its logs: 40 reference
tests, 40 baseline tests and 40 runtime tests passed; all three arms produced
164 records satisfying exact approved JSON and the strict read contract.
Regression verification passed 436 tests plus 7 subtests, with 18 forced
mutation checkpoints. Final zero-skips/failures/errors validation succeeded.
Artifact `14730370162` is `1873204` bytes; its ZIP SHA-256 is
`dd3a35814420bbeb6e8c772981ff4bc6612a3b3ba5ce02e4c61e832f81ed8351`.

The exact-B2 [Stock run 44961507338](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44961507338),
job `137021160627`, completed successfully. Root verified **5503 tests passed
plus 97 subtests** in `447.42` seconds from the log. Exact-B2
[CodeQL run 44961507355](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44961507355)
and [G47 run 44961507353](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44961507353)
also succeeded.

[Balance run 44961507257](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44961507257)
and its Backend job `137021159710` completed successfully. Root verified
**6190 main tests plus 90 subtests** in `551.22` seconds, followed by **16
shipping regressions plus 18 subtests** in `0.60` seconds. Balance's
browser/mobile/visibility acceptance, Frontend and canonical-worker jobs also
succeeded. No required CI or merge step remains open.

The Balance JUnit artifact `14730401083` binds exact B2, its tree and run;
its SHA-256 is
`36bba2c454ec8800197f43ea8a2bd7d14e942f8943b1db7642590b08f05f4b30`.
The main Balance JUnit report has zero errors, failures or skips. The separate
shipping result of 16 tests and 18 subtests is supported by the successful job
log; retained shipping JUnit coverage is not claimed. Stock JUnit
artifact `14729268829` binds run `44961507338` and exact B2; its SHA-256 is
`eeefb2be975b300dc4fac22b8635ae1ff27a86573ac51a50a95577484add0a29`.
Its reports pass. These artifact checks supplement the job-log totals.

Final acceptance is on **exact B2 carrying source A2**: 20 workflows have 19
successes and one conditional TikTok skip; 43 check runs have 37 successes and
six expected conditional skips, with zero active or failed. Separate source-A2
runs may be superseded or cancelled by the B2 push and do not need to finish
independently. Historical PR #2054 checks are not used as substitutes.

## Authorized publication and completed live verification

This completed outcome follows WIP evidence commit
`a7d8f430264bffc7cda79eea3d6eaf956ec7a604`, which preserved the ongoing verifier,
and earlier evidence commit `ab6e9b4c2a0c0fda6c53d1ee5c2f54d0f2705a15`.
Host, UI, verifier and dashboard observations in this section were reported by
the publishing operator; the documentation reviewer did not operate production.

The user's explicit “نعم انشر” authorized one publication of exact reviewed M2.
The original host adapter passed with exact A2 and R2 release identity. M2 was
fast-forwarded into shared `/app`. Fresh staging was
`/tmp/mezan-pr2055-release-20261122-6d6qv902`; the three previous generated outputs
were preserved beneath its `previous-app-generated` directory before replacement.
The original prepare initially rejected the sole forbidden generated file
`scripts/__pycache__/production_release_guard.cpython-311.pyc`. That file was
preserved outside `/app`, with no governed source or intent change.

Original prepare then exited zero at `2026-11-21T23:57:28.621782Z`, actor
`chatgpt-pr2055-release-20261122-b90f3c3040a7`, binding exact M2, A2 and the
recorded R2 release. Original prepublish exited zero. Root clicked
**Publish changes exactly once**. The UI first displayed
`Deployment Started Nov 22, 02:57 AM` and then the newer green event:

- Actual completion wording: `Deployment finished Nov 22, 03:08 AM`.
- UI timezone: `Asia/Aden`.
- Event: `99555f3c-ea30-500a-bb7a-61431c964a4a`.
- Platform-reported version: `7cf99c2`.

The platform version is recorded as a UI label, not asserted to be the governed
source Git SHA. The actual green completion wording is preserved rather than
quoted as `Deployment Succeeded`. The subsequent original verification below
completed the required release acceptance.

The original command

```bash
python scripts/production_release_guard.py verify --url https://mezansalla.com
```

was run unchanged and completed successfully:

| Original verification result | Recorded value |
|---|---|
| Started | `2026-11-22T00:10:12.164401Z` |
| Finished | `2026-11-22T00:24:02.181628Z` |
| Elapsed | `830.017` seconds |
| Exit / result | `0`, `verified=true`, `checks=3` |
| Source A2 | `8665e8acf1d7bb4878cd2fc0b912a20132f1e0c7` |
| Source base M1 | `10ee7b41204dd1d39c8b91daf9f0a9d899041ebb` |
| Deployment M2 | `0c5e5b98795b8d30289ec0e4d3c95738fd660af1` |
| Runtime release | `rg5-4d0f595ee0711c73459e0cb18711c87657602d7886a885f0ccb1a3013e31dc05` |
| Frontend artifact tree | `770915f10cbbaa9b17e129ee8ba544c4c618dd539600de519f7f70b48c80540a` |
| Frontend requests | `710` per probe; `2130` across three probes |
| Raw verifier stdout SHA-256 | `6501e67c29bc5e6c824b09e1621076579ebe802e4569724b89ed688b8e6aa21f` |
| Original status afterward | `active: false`, `actor: null` |

The recorded boot-start observations were `2026-11-22T00:07:26.517771Z` and
`2026-11-22T00:07:33.179919Z`. The matching lease closed automatically on
successful verification. No manual lease abort was needed for this outcome.

Success of the unchanged original guard establishes the reviewed source and
runtime identity, critical Backend/source bindings, verified Frontend artifact
tree, and exact canonical/cache-busted build-meta JSON bytes with
`application/json`. These are conclusions from the original guard's contract,
not additional fields asserted to appear independently in its raw stdout.

The `830.017` seconds measure the full release verification, which made
`2130` Frontend requests across three probes. Ordinary dashboard load duration
was not measured. Publication, prepare and the completed three-check verifier
must not be repeated for this release.

### User-supplied release ID reconciliation

The user supplied
`rg5-ddbdc7c7bfa083d85224a0f0275318ca7dacfdd05d3f628e8f3dc05c278be895`.
Root and an independent reviewer identified it as the earlier
[PR #2046](https://github.com/AMASI-SA/AMASI-SA/pull/2046) release:

- Source: `eeef3913ae2e1b8fa4b58f3835478e1ed17b2988`.
- Source base: `66aa13967156e8e279b5297caf697c155f720fd1`.
- Intent commit: `4f5dd585d89b1aea2e98efbcb8efd7cce0559858`.

One bounded original `_health_payload` sample at
`2026-11-22T00:21:49.943625Z` took `0.344` seconds and returned
`health_ok=true`, exact source A2 and intended R2 release
`rg5-4d0f595ee0711c73459e0cb18711c87657602d7886a885f0ccb1a3013e31dc05`.
Its boot time was `2026-11-22T00:07:33.179919Z`.
`verified_identity_available`, `critical_file_hashes_match` and
`frontend_build_verified` were all true; `matches_user_supplied=false` and
`matches_expected=true`. The sanitized host-stage record is
`user-release-id-check.json`.

This was one health-identity observation, separate from the completed original
three-check verification. Its timing does not establish dashboard performance.
No inference is made about where the user obtained the older ID.

### Initial authenticated dashboard observation

One ordinary authenticated visit to `https://mezansalla.com` automatically
redirected to `https://mezansalla.com/dashboard-advanced`. The Today filter showed
22 November 2026. The period summary, top products and executive cards rendered.
The page was closed after this initial observation; no manual refresh or data
sync was performed. No application load error was observed. The only console
error concerned browser-extension metadata, not the application.

No precise load-duration measurement or fresh executive Backend request log was
obtained. View Logs exposed a static deployment snapshot ending `00:08:23Z`;
Monitoring showed a 24-hour aggregate that included older critical intervals.
Neither establishes a current load-test outcome. This initial render does not
establish sustained or per-request incident resolution.

## Historical PR #2053 rejection, lease abort and rollback

The preceding PR #2053 was normally merged as
`10ee7b41204dd1d39c8b91daf9f0a9d899041ebb`. Its final Emergent **Publish changes**
action was rejected by automatic approval review because the continuation
messages did not provide sufficient explicit authorization for that exact
production publication. Publication did not start and no newer
`Deployment Succeeded` was observed. This was an approval rejection, not a
failed or completed deployment.

The owner verified its own actor before aborting only its own PR #2053 lease:

- Actor: `chatgpt-pr2053-recovery-20261122-b90f3c3040a7`.
- Original guard abort: exit `0`, `aborted: true`.
- Source: `99c0b847014d68f921154d2b5b74101c4db9dbaf`.
- Deployment commit: `10ee7b41204dd1d39c8b91daf9f0a9d899041ebb`.
- Release: `rg5-d4eb0438ce8a4fd222298c40e6375f06cdfeb7e77ffdb35a5ff921bc8a0ab42d`.
- Original guard status after abort: `active: false`.

The original cache-busted health helper at
`2026-11-21T22:31:02.514248+00:00` reported the existing emergency rollback:

- `health_ok: true`, one request elapsed `0.744` seconds.
- Source: `db141c0438edf04aa77ccd1122bc9c5008cf39d6`.
- Release: `rg5-500857bae382758ba238083f1f7f417b23a7e2dfb298aa3e2e99e7f4baf56b45`.
- `verified_identity_available`, `critical_file_hashes_match`, and
  `frontend_build_verified` were all `true`.

That observation proves the reported rollback identity at that time. It is not
an authenticated dashboard timing, a memory measurement, or performance
acceptance. It must not be described as evidence that the slowdown was resolved.
At the earlier E2 handoff, shared `/app` still contained the PR #2053 merge and
its older artifacts, and no active lease was reported. That workspace state is
historical; the authorized R2 staging and current verification are recorded
above.

The earlier blocked-publication checkpoint is preserved at
<https://github.com/AMASI-SA/AMASI-SA/tree/codex/awaiting-history-recovery-evidence-20261122/docs/operations/AWAITING-HISTORY-PERFORMANCE-20261121>.
No Issue #1006 comment was posted by this continuation task.

## Superseded PR #2054 — required acceptance failed

PR #2054 is closed without merge. Its original branch and head B
`20e1bcc0e3b0a4912eab34f34819b4cde0e5bce4` remain unchanged as evidence. The
candidate is not accepted for deployment despite successful runtime and Host
readiness subsets.

- [Stock run 44959641752](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44959641752):
  **1 failed, 5501 passed, 97 subtests passed**.
- [Balance run 44959641872](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44959641872):
  **1 failed, 6188 passed, 90 subtests passed**.

Both failures were the same stale `estimate_catalog` ordinal target in
`test_every_owned_authority_phase_drains_motor_before_lease_release`. Product
read two had become direct selected-document hydration, while the original
request-owned estimate query occurred later. The tests correctly blocked
acceptance. R2 fixes semantic interception without weakening cancellation
assertions or modifying runtime calculations.

The following identities, successful checks and reviews describe only this
superseded candidate and remain historical evidence.

### Historical PR #2054 source and intent


| Contract | Exact identity |
|---|---|
| Source base | `10ee7b41204dd1d39c8b91daf9f0a9d899041ebb` |
| Source A | `f13101cd78808ca0de9bb14352efd475a87eb8c4` |
| A tree | `2b7e69a729d70f9404bcebfedde4b17977544327` |
| Intent B | `20e1bcc0e3b0a4912eab34f34819b4cde0e5bce4` |
| B tree | `3ff926b679c0ab69254a20061ee7b0aa41dc53c3` |
| Intent Git blob | `ed199ea77705138668553619c761e9fbbde03f81` |
| Intent SHA-256 | `8f0c27e54cdc4fa05e83ea723febefa24d487c9eff43bac7dcddc3739e8c0284` |
| Intent bytes | `1444324` |
| Deterministic runtime release ID | `rg5-784308cfa8388640c2ec226a2a0c50fc9dbd66ca52f1545c503262f247d02d22` |

Fresh remote Git commit, comparison and intent reads, independently checked
against the exact local Git blob, verified that B has sole parent A and that
A..B changes only `release/release-intent-v5.json`. The comparison is one commit
ahead, zero behind, with merge base A. Source A and frozen B have not been
amended, rebased, or squashed.

The original runtime identity validator passed with strict Backend filesystem
validation and `allow_derived_bytecode=False`. Source A's checkout remained
clean after read-only verification. The source manifests bind 1288 Backend
runtime files, 1345 Frontend source files, and 322 release-control files.

The original governed freeze completed with exit `0`. The one generated
operator-owned Python bytecode cache encountered during preparation was removed;
the successful invocation used `-B`. No governed source file was changed by
that correction. The final intent-only B was then independently checked.

## Historical PR #2054 Frontend and package evidence

- Governed toolchain: Node `22.23.2`, Yarn `1.22.22`, Vite `8.2.4`.
- Artifact tree: `770915f10cbbaa9b17e129ee8ba544c4c618dd539600de519f7f70b48c80540a`.
- Build-meta SHA-256: `ac9b7eca49a94281e379aef21db46962c44b0dd21820237589ad1b3cc2e7d6e7`.
- Build-meta size: `549655` bytes.
- Reproducibility proof SHA-256:
  `78edb4553e12524f5cba9f23b5cb70a22bf641b1fa3415359228e34a104d6730`.
- Both clean builds produced the same artifact and build-meta identities; pass
  two is retained. The intent and proof both bind source A.

Exact-B release-readiness run
<https://github.com/AMASI-SA/AMASI-SA/actions/runs/44959642014>
completed successfully at `2026-11-21T23:04:56Z`. Its six required jobs passed:
Backend preflight, Frontend build, Host Node 20.20.2 and 24.19.0 dispatcher
contracts, and both clean-clone adapter rehearsals.

- Host Node 20.20.2 clean-clone adapter job:
  <https://github.com/AMASI-SA/AMASI-SA/actions/runs/44959642014/job/137015881131>.
- Host Node 24.19.0 clean-clone adapter job:
  <https://github.com/AMASI-SA/AMASI-SA/actions/runs/44959642014/job/137015881195>.

Independent log review confirmed no Git metadata in each rehearsal checkout,
governed Node 22.23.2, both identical build passes, the exact proof, source and
release ID above, isolated Backend health/package verification, and isolated
Frontend runtime-artifact verification. These are CI package-boundary proofs;
they do not establish Emergent's live snapshot timing or a production handoff.
The manual-redeploy notification job was conditionally skipped.

## Historical PR #2054 runtime correctness and independent review

Exact-A runtime-adoption run
<https://github.com/AMASI-SA/AMASI-SA/actions/runs/44959201789>
completed successfully at `2026-11-21T22:59:36Z`:

- Immutable reference: 40 tests passed on MongoDB 8.0.12.
- Frozen Production read baseline: 40 tests passed.
- Actual source-A runtime: 40 tests passed.
- Source/cancellation/identity/regression tests: 436 passed plus 7 subtests.
- Final verification required 164 request records in each arm, exact approved
  response JSON, the strict audited read contract, and zero skipped tests,
  failures or errors. It passed; 18 forced mutation checkpoints were recorded.
- Evidence artifact ID: `14729250766`, size `1873108` bytes.

Exact-B runtime-adoption run
<https://github.com/AMASI-SA/AMASI-SA/actions/runs/44959641824>
also completed successfully at `2026-11-21T23:04:46Z`.

Independent implementation review checked global alias ambiguity, duplicate
winners, physical identity selection, original companion query scopes/limits,
financial authority, and the absence of a total-memory guarantee. Independent
gate review found no material loophole. The original financial JSON oracle,
financial-cohort enumeration and 164-record requirement remain unchanged.

The test-only mutation fence moved to completion of selected full-cost capture,
before companion reads, preserving the frozen fixture's original semantic
boundary. Six original numerical expectations remain unchanged under both
synchronous and cooperative fences. Separate tests still require retryable
503 on a selected identity change between alias read and hydration, followed
by a successful fresh request where the changed identity still resolves.

The final new gate suite passed 67 tests. Its negative controls cover scope,
projection, physical IDs, cursor caps, writes, forged empty cohorts, missing
nonempty/companion/unrelated reads, and financial value or JSON order changes.
The earlier combined read-contract run passed 215 tests. Valid lexical and
reversed Mongo result orders are accepted because the original query has no
sort; raw observed order remains in the evidence. Separate runtime tests retain
duplicate-alias winner/order parity.

Reviewed file SHA-256 values:

- Runtime: `0d9f4fb40beebbaf8aa27c6cb51d962913cfa2d8399afb82bea1a2e52ac42feb`.
- Gate plugin: `43b6ff8ed59352ab3e2d17928e091e52e9a2f934e23556c36d3af43f353a01ef`.
- New gate tests: `71385ba147eaf9d3ae0af34522b7dd2931be2a94dec0eb797b08ac78c638033a`.

The historical successful checks above did not satisfy release acceptance.
Stock and Balance failed as recorded in the supersession notice; PR #2054 was
closed without merge and its frozen A/B remain unchanged.

## Remaining performance limits

This change avoids full raw catalog hydration for unrelated products and avoids
all product-cost reads for an empty order cohort. The first pass still reads all
owner aliases and full variant values up to the existing 100000-product cap.
Owner-wide companion reads retain their previous scopes and limits. Selected
documents and large selected cohorts can still be large; this is not a total
memory bound. Other order, report and advertising reads are outside the patch.

The two catalog reads are not a snapshot transaction. Selected identity changes
are detected, but arbitrary concurrent catalog mutations are not claimed
equivalent to a single captured snapshot. Existing recorded order/bundle costs,
supplier authority, payment/status rules and financial writes remain governed by
their unchanged paths. Synthetic payload measurements are retained in the
historical checkpoint and evidence files; no production RSS or dashboard latency
improvement has been measured for this candidate.

## Release completion and safe follow-up

Publication and the required original release verification are complete. The
matching lease is closed. **Do not repeat Publish changes, prepare or the
completed three-check verifier for this release. Never deploy the evidence
branch.**

Ordinary follow-up is appropriate only for a concrete reported issue. Begin
with this evidence and the exact deployed identity. Keep the successful initial
authenticated dashboard render separate from precise load-duration or sustained
performance acceptance; no blanket incident resolution is claimed.

## Preserved source-A2 checkpoint

The source-A2 status document follows verbatim. Its prefreeze statements are
historical and superseded by the verified current facts above.

# Catalog memory recovery — source review checkpoint

## Identity and release state

- Task: `CATALOG-MEMORY-RECOVERY-20261122`.
- Branch: `codex/dashboard-catalog-memory-20261122`.
- Review: PR #2054 (draft at this checkpoint).
- Base: `10ee7b41204dd1d39c8b91daf9f0a9d899041ebb`, the normal PR #2053 merge.
- Recoverable WIP checkpoint: `eff3f35a773945aabd91c0faabc8ed86588a781f`.
- The local reviewed source extends that WIP; root will record its exact final
  source commit in PR #2054 and the canonical Issue #1006 ledger.
- Current `backend/dashboard_v2_routes.py` SHA-256:
  `0d9f4fb40beebbaf8aa27c6cb51d962913cfa2d8399afb82bea1a2e52ac42feb`.
- This is a separate follow-up to PR #2053. Its frozen A/B were not modified.
- No release intent has been frozen for this follow-up. No merge, production
  publication, production database write, or production environment change was
  performed by this implementation task.

## Completed behavior

Both product-cost builders return the unchanged empty result without any
database reads when the order cohort is empty, preserving JSON member order.
For nonempty cohorts, a first owner-scoped catalog read retains the original
first-100000 boundary and the complete alias information used by the canonical
resolver. It excludes unrelated raw product descriptions, images and option
payloads. Complete raw/normalized variant values remain in this first pass,
because their type, truthiness and precedence affect historical alias matching.

The canonical global index determines the exact physical documents selected by
order lines. A second owner-scoped read hydrates only those Mongo `_id` values
with the original full cost projection. The original alias maps are filtered
and their values replaced with hydrated records; they are not rebuilt from the
selected subset. This preserves last-winner ID/SKU/variant collisions, globally
ambiguous names, and first-winner name aliases even when physical rows share a
canonical identity.

A selected document removed or reidentified between the two reads produces an
explicit retryable `503 / catalog_changed_during_cost_read` with
`data_complete=false`. Compact alias rows never become cost inputs. A successful
fresh retry uses the current identity; a genuinely missing product remains
visibly incomplete. Price-only changes remain current catalog prices.

The original full list of Salla product IDs still scopes the companion profile,
binding and resource reads. Their limits and order remain unchanged, avoiding a
change to the legacy capped subsets, including nonempty orders without product
lines. The complete companion-read and financial
reducer AST in both builders is pinned to the base commit. Recorded bundle/order
costs, approved supplier authority, status rules, payment rules and financial
writers were not changed.

## Verification completed locally

The new regression file is registered in the existing dashboard card cadence
workflow, including its path filter. Tests use synthetic isolated databases.

Commands used the existing Python environment with
`PYTHONDONTWRITEBYTECODE=1`, `PYTHON_DOTENV_DISABLED=1` and
`PYTHONPATH=backend:backend/tests`:

```bash
python -m pytest -q -p no:cacheprovider \
  backend/tests/test_dashboard_catalog_cohort_reads.py \
  backend/tests/test_dashboard_v2_product_cost.py \
  backend/tests/test_dashboard_bundle_cost_authority.py

python -m pytest -q -p no:cacheprovider \
  backend/tests/test_dashboard_catalog_cohort_reads.py \
  backend/tests/test_dashboard_card_snapshots.py \
  backend/tests/test_supplier_invoice_report_integration.py

python -m pytest -q -p no:cacheprovider \
  backend/tests/test_dashboard_catalog_cohort_reads.py \
  backend/tests/test_dashboard_runtime_source_contract.py \
  backend/tests/test_dashboard_ads_costs.py
```

Results: the first two runs passed **61** and **73** tests respectively. After
adding explicit fresh-retry/name-race and no-product-line coverage, restoring
empty-result member order, and updating the exact observer-oracle source hash,
the last run passed **44** tests and **7** source-contract subtests.
The only warnings were existing Pydantic V1 validator deprecations.
`git diff --check` passed.

The baseline empty-cohort regression failed on the original full catalog read.
The corrected payload regression was also run against the immutable base
function bodies: all three old builder modes unnecessarily hydrated unrelated
large raw catalog records for a one-product order. New tests verify zero empty
reads, selected full hydration, owner scope, alias collisions/ambiguity,
malformed variant-source precedence, exact full response parity, a known
255-SAR mixed-alias result, unchanged companion queries, and safe hydration
races/retries. Existing bundle, supplier and dashboard-card tests preserve
historical financial authority.

Independent reproducible evidence is in `evidence/`. For the synthetic
1000-product catalog and one selected product, catalog BSON returned to the
client changed from **43,203,670 to 181,885 bytes** and full raw rows from
**1000 to 1**, with the same 75-SAR result. Empty cohorts changed from
43,203,670 catalog bytes and six catalog/companion finds to zero. All six
builder/mode cases preserved both canonical and insertion-order JSON bytes.
These measurements describe the fixture's catalog payload; they are not
production RSS, end-to-end latency or incident-resolution evidence.

## Pending review and exact next action

The runtime gate adapter now audits the exact alias/full projections, owner,
physical winning IDs, cursor limits and returned rows. It normalizes only the
extra selected-document read and the six removed cost reads for the seven known
empty frozen scenarios. Negative controls reject widened or duplicate IDs,
writes, missing nonempty/companion/unrelated reads, forged empty cohorts and
financial JSON or member-order changes. The independent post-catalog mutation
fence runs after full selected cost capture and before companion reads, matching
the original semantic boundary. Separate alias-to-hydration race tests continue
to require the retryable 503. None of the original 164 financial assertions or
output expectations is changed.

The final new catalog-audit suite passed **67 tests**. The preceding combined
run passed **215 tests**, including all 150 existing authority, Google and
payment-fee read contracts; the final additions verify valid Mongo index order
without imposing an unrequested sort. Twelve
mutation cases preserve all six frozen financial totals under synchronous and
cooperative fences. The new gate test is registered in the runtime-adoption CI
workflow. Independent final review and actual real-Mongo CI remain required.

Next: finish the independent gate review, capture the exact source commit, and
run exact-head CI, including all 164 immutable runtime assertions. Then freeze
a fresh source A and intent-only B under release protocol v5 and validate B.
This checkpoint is not deployment approval or evidence of a deployed fix.

## Material limits

- The first alias pass still reads all owner aliases and complete variant
  values up to the existing catalog cap. It is smaller, not constant-memory.
- Companion profile/binding/resource reads remain owner-scoped and capped as
  before; other report, advertising and order reads are outside this patch.
- Selected full documents can themselves be large, and a large selected cohort
  can still require substantial memory.
- The two reads are not a database snapshot transaction. Selected alias changes
  are detected; arbitrary concurrent mutations across the full catalog are not
  claimed equivalent to a single snapshot.
- No production data was used to size the benefit. Production slowdown remains
  an open incident until a separately authorized release and live verification.

## R2 prefreeze checkpoint

The immutable PR #2054 candidate is retained as failed acceptance evidence:
source A `f13101cd78808ca0de9bb14352efd475a87eb8c4` and intent-only B
`20e1bcc0e3b0a4912eab34f34819b4cde0e5bce4`. Its runtime-adoption and Host 20/24
readiness gates passed, but the exact-B required suites failed the same stale
`estimate_catalog` cancellation-fixture target:

- [Stock run 44959641752](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44959641752):
  1 failed, 5501 passed, 97 subtests passed.
- [Balance run 44959641872](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44959641872):
  1 failed, 6188 passed, 90 subtests passed.

The old fixture intercepted the second product read. That position now belongs
to direct selected-product hydration; the unchanged request-owned bundle
estimate read occurs later. The test-only correction selects the exact original
owner-scoped estimate query and projection instead of an ordinal. An explicit
negative-control test excludes direct alias/hydration reads and altered
owner/scope/projection. All original worker-drain, cursor-close and lease-release
assertions remain intact. This does not extend their ownership guarantee to
the separate direct catalog reads.

Focused verification passed 217 tests. An independent overlapping 33-test suite
also passed; these counts are not additive. Independent review confirmed the
minimal semantic retargeting. Runtime SHA-256 remains
`0d9f4fb40beebbaf8aa27c6cb51d962913cfa2d8399afb82bea1a2e52ac42feb`.

Next: create fresh R2 source A2 from original A **before B**, retaining source
base `10ee7b41204dd1d39c8b91daf9f0a9d899041ebb`'s exact intent bytes and no intent
changes in the source history. Do not amend or rebase the frozen failed A/B.
Freeze a new governed intent-only B2 and require all exact-commit CI, including
Stock and Balance, before release acceptance. A2/B2 identities and fresh freeze
and CI results are pending. Production publication remains blocked/not started;
no deployment or incident resolution is claimed.


