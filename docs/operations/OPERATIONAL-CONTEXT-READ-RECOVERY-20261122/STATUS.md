# OPERATIONAL-CONTEXT-READ-RECOVERY-20261122

## Current release — PR #2058 / A2 and B2

**Evidence checkpoint: 2026-11-22 02:42:48 UTC.** The current candidate is
[PR #2058](https://github.com/AMASI-SA/AMASI-SA/pull/2058), branch
`codex/operational-context-read-lifetime-20261122`, in `AMASI-SA/AMASI-SA`.
Source A2 and intent-only B2 are frozen and remotely preserved. The corrected
source-bootstrap two-pass build and freeze passed. Exact-B2 CI is **pending**:
12 workflows have triggered, with no failed check observed. Readiness and
Security have now passed; other required gates remain pending. The **fresh B2 host rehearsal passed**. No B2 merge, lease,
publication or live acceptance is claimed; Production is unchanged by this
correction at this checkpoint.

PR #2057 / B1 is a **superseded, blocked attempt**, archived below with both
failed backend jobs and its completed matrix. Root confirmed #2057 was closed **without merge**, with its frozen head
and branch preserved. **No B1 test, CI or host-rehearsal result satisfies a B2 release gate.**
The current 83-case local verification was executed against the corrected bytes
that match A2; it is separate from B1's failed CI.

This document and adjacent `release-evidence.json` are maintained only on
`codex/operational-context-release-evidence-20261122`, originally based on B1.
**This evidence branch must never be deployed or merged into a frozen release.**
The previous durable evidence checkpoint is
`aa1baf3d5bd2e84b809ab15b9d034e61c7494308`, tree
`ceeade7b8cc7684db1d7ef437f8f1dfe0817b6c2`. Root owns the next remote checkpoint
and all release actions. This author changes only these two evidence documents.

## Current immutable identity

| Item | Exact value |
|---|---|
| Published base J/M3 | `02e8be41e6f1972806f0e095b8fdc38645a7f113` |
| A2's sole parent / A1 | `cde10efae4fb5b83f69b1c875f8c5c9ec6e6607c` |
| Source A2 | `a8e655aabbeaef64486a1a80f13551dc97dd9259` |
| A2 tree | `fdadfb3ae9fdc92dfc4e4b8b584c949733a2b1f8` |
| Frozen B2, sole parent A2 | `0329084d343562fa5ff43df535422eeeacb0a327` |
| B2 tree | `18b335c1eb3d526c82ace35d83aca0d50ce4d1a5` |
| Runtime release | `rg5-9d69963c0ee7f5c299595495519175af5074f703f4e318b82b870b0407554a49` |
| Intent Git blob | `3f2382868016423828198ea08c5b2e4272177445` |
| Intent SHA-256 | `f6e7bc34d7b0aeec0b6222960b51a366d3269ea4947d64f56c4b2a4afc557291` |
| Frontend artifact tree SHA-256 | `65582eef5ae1c6aef9fc774c2ea10e5168c2fb396535c34b44be266526817526` |
| Build metadata SHA-256 | `de10ae11ad9968752853b59961ecfb551d5c201e4dcaa9f09ec4bf1c9a9bb75e` |
| Reproducibility proof SHA-256 | `34bc84792eb19e38f46546a24ea3f9b358231384a3f2eff9ab220fa218e1ecbe` |

Independent read-only Git inspection confirmed these parent/tree identities.
A1-to-A2 changes only the active-read-lifetime test and source status document;
A2-to-B2 changes only `release/release-intent-v5.json`. The full M3-to-A2 source
change contains five paths, and M3-to-B2 adds the intent as the sixth. The
external v2 candidate is byte-identical to B2's tracked intent: **1,466,981 bytes**.
Root reported the unchanged original candidate and retained-frontend validators
passed. Frozen B1 is not an ancestor carried into this new source sequence.
The source scope and runtime behavior remain the reviewed context correction.

## Implementation and integrity boundary

The observed inventory permission bootstrap failed twice after M3 publication.
The latest retained runtime logs show other slow requests and health upstream
timeouts, but no matching inventory/context timing. The live cause is therefore
unproved. Source review found that existing `GET /api/operational-balances/context`
loads complete historical audit pages even though its response only needs current
status, start time, openings count, current issues and separately checked access.

The implemented correction reuses `read_financial_position_state` only inside this
GET handler, leaving the original router gates, permission checks, session digest,
bank assignments, JSON response fields and all full mutation/audit readers intact.
Only its documented current-snapshot HTTP 503 is translated to the context
endpoint's pre-existing audit-integrity error payload; unrelated errors propagate.

Current snapshot/issue packets and audit-manifest metadata remain validated.
Historical audit page contents are intentionally not materialized for context;
the full store reader continues to validate them for audits and mutations.

## Source scope and reviewed bytes

| Source path in A2 | Scope / SHA-256 |
|---|---|
| `backend/operational_balance_routes.py` | Existing GET context reader boundary only; `092fd7915285a2f654400631b3f70001e8b9eb11d205565fa2dae3650b50f14b` |
| `backend/tests/test_operational_context_snapshot_reader.py` | 21 current-context contracts; `fa4f9d765197ad062a2c753f52ce2453abdd516fd4cc38cfe4c561131d2c697d` |
| `backend/tests/test_operational_balance_active_read_lifetime.py` | Minimal current-reader test interception; `73720520282521a5648675034b05a5f9040728d41691fe0f31fb691ae07f5e07` |
| `.github/workflows/operational-balance-web.yml` | New module registered with minimum 21, existing thresholds unchanged; `2982cf400c28f0c05d96e7c88bfc1c061c983fd6dbad6c4c01ec1ce57b4e485e` |
| `docs/operations/OPERATIONAL-CONTEXT-READ-RECOVERY-20261122/STATUS.md` | Source checkpoint and handoff record |

Each hash was read directly from immutable A2. Runtime route, new 21-case module
and workflow bytes are unchanged from the reviewed A1 correction. No frontend,
financial math, grants, shared full reader, write behavior or release protocol
source changed in the successor test correction.

## Current test correction and focused verification

The source writer reproduced the failure on unchanged A1: **1 failed, 11 passed
in 5.09 s**, exit 1. The `/context` case timed out waiting for the existing
reader fixture. A minimal test-only change imports the current-state module and
selects `read_financial_position_state` only for `/context`; the other 11 paths
retain their original `routes.read` interception. The test file changes by
**five insertions and one deletion**.

Independent read-only review at test SHA-256
`73720520282521a5648675034b05a5f9040728d41691fe0f31fb691ae07f5e07`
passed. AST comparison against A1 confirmed all 12 parameter values, every
assertion and try/finally block, the blocked-reader stub and the entire
principal/router/capability-free `object()` database/request-lifetime tail are
unchanged. The stopped event, HTTP 499, cancellation/drain and no-task-leak
checks remain intact. No tracked backend runtime, frontend, scripts or release
source changed; the route still has SHA-256
`092fd7915285a2f654400631b3f70001e8b9eb11d205565fa2dae3650b50f14b`.

The writer reported the same whole-module command green: **12 passed in 2.87 s**,
exit 0. The five-module focused run passed **83 cases in 14.86 s**, exit 0;
these comprise the 12 lifetime cases plus the preceding 71-case group, including
the 21 context cases. These overlapping reports are not added together.
The independent reviewer inspected the diff and AST, without rerunning tests.

```bash
PYTHONPATH=backend:backend/tests PYTHONDONTWRITEBYTECODE=1 \
  /workspace/scratch/78a3f53e49a4/payment-setup-venv/bin/python -B -m pytest \
  --noconftest -q \
  backend/tests/test_operational_balance_active_read_lifetime.py \
  backend/tests/test_operational_context_snapshot_reader.py \
  backend/tests/test_operational_financial_position_state.py \
  backend/tests/test_operational_balance_financial_position_routes.py \
  backend/tests/test_operational_balance_supplier_workspace.py \
  --tb=short -p no:cacheprovider
```

The original single-module red/green command uses that same prefix/suffix and
only `backend/tests/test_operational_balance_active_read_lifetime.py`.
These reviewed bytes are now in source A2 and frozen B2. The governed build
and freeze below passed, as did a fresh actual-host B2 rehearsal; final
exact-B2 CI remains required. The correction does not turn archived B1 into a passing release.

## Fresh A2 governed build and freeze

| Step on 2026-11-22 UTC | Start–finish | Duration | Result |
|---|---|---:|---|
| Pinned toolchain ensure | 02:33:03.846077–02:33:08.712319 | 4.866 s | Exit 0 |
| Frozen dependency install | 02:33:08.713502–02:33:30.232499 | 21.519 s | Exit 0 |
| Source-bootstrap two-pass build | 02:33:30.232929–02:33:51.031083 | 20.798 s | Exit 0 |
| Freeze | 02:33:51.033168–02:33:53.316142 | 2.283 s | Exit 0 |

Both clean passes have the intended A2, identical artifact and build-metadata
hashes, and pass 2 was retained. The pinned toolchain is Node **22.23.2**, Yarn
**1.22.22**, Vite **8.2.4**. The frozen manifests cover 1,378 frontend source
files, 1,289 backend runtime files and 322 release-control files. Those are
manifest entries, not test counts.

Evidence was read from `context-release-artifacts-v2-20261122/steps.json`,
`identity-summary.json`, its intent and associated logs. Every recorded stdout
and stderr byte count and SHA-256 was verified against the corresponding file.
The successful freeze emitted the expected missing ignored backend identity
diagnostic in the clean source worktree; its exit status was 0, and root's
original candidate and retained-frontend validation passed. This is a fresh
source build; archived B1 outputs were not used as this candidate's evidence.

### Fresh actual-host B2 rehearsal

Root independently rehearsed B2 using the original
`yarn --cwd frontend build` adapter in isolated stage
`/tmp/mezan-pr2058-release-20261122-0agkk3um`. It ran from
**2026-11-22T02:36:35.509600Z** to **02:37:33.617377Z**, **58.108 s**, exit **0**.
The generated identity exactly matched A2, B2's reviewed contract and release
`rg5-9d69963c0ee7f5c299595495519175af5074f703f4e318b82b870b0407554a49`;
the staged source remained tracked-clean at B2.

The fresh original guard was inactive and `/app` was tracked-clean at M3 before
this rehearsal. No `/app` source or generated output was changed and no lease
was created. Observed host Node was **24.19.0**, host Yarn **1.22.22**; the
unchanged adapter used governed Node **22.23.2**. Root supplied stdout
**203,976 bytes** / SHA-256
`b77e5e14228a50679c2189a5cb91fe4a5c012d1cb15db1d293ddf9e221f8df1d`,
stderr **10,336 bytes** / SHA-256
`de30be491db32f4420550d7c6ea2b5d34384f2322499dc2d54e8e99a09d762c0`,
and launch-wrapper SHA-256
`ed2d2c90c63400313f51f51c49b4c8ee293e89068f1d2aab6d56d550c6eff182`.
These are fresh B2 observations reported by the release owner, independent of
B1. They do not establish the later actual Cloud Build, live runtime or inventory
acceptance.

## Current exact-B2 gates

The independent reviewer reported 12 newly triggered workflows and zero failed
checks in the initial B2 snapshot. The following states refer only to the new candidate; pending rows are **not
completed passes**:

| Gate | Exact run / job | State at this checkpoint |
|---|---|---|
| Operational backend | [44976770986 / 137068308105](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44976770986/job/137068308105) | Pending |
| Stock | [44976770906 / 137068307860](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44976770906/job/137068307860) | Pending |
| Readiness | [44976771038](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44976771038) | Six required jobs and independent artifact binding review passed |

All refer to exact B2 `0329084d343562fa5ff43df535422eeeacb0a327`.
The independent reviewer confirmed this head retains explicit registration of
the 21-case module, its minimum-21 requirement, the unchanged global
no-skips/failures/errors assertion and the shipping follow-up step. No Accounting
path filter matches the six M3-to-B2 paths, so standalone Accounting is **not
triggered**, not a pass or skip; no earlier result is borrowed. The final B2
matrix, including actual skip reasons and executed-test evidence, remains due.

A subsequent exact-B2 reviewer milestone confirmed the operational frontend
**89 suites / 1,415 tests** (job **137068308305**), browser **26 scenarios in
14/12 groups** (**137068308183**) and worker **4 cases** (**137068308131**) passed.
Security backend **137068308245**, G47 UI **137068308045**, Mobile
**137068307913** and Qoyod **137068307822** also passed their selected stages.
These are fresh B2 results, separately recorded in JSON without adding overlapping
groups. A later reviewer observation at **02:39:31 UTC** confirmed Security
[44976771018](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44976771018)
succeeded, including full frontend job **137068308168** with **456 suites /
4,978 tests**. Salary run **44976770849**, job **137068307522**, also passed its
original proof assertions.

### Fresh B2 Readiness proof

The catalog reviewer independently confirmed Readiness run **44976771038** at
exact B2, with the final job snapshot **2026-11-22T02:39:25.684Z**:

| Required job | Exact job | Result |
|---|---|---|
| Frontend build | [137068308275](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44976771038/job/137068308275) | Success |
| Backend preflight | [137068308477](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44976771038/job/137068308477) | Success |
| Host20 dispatcher | [137068308407](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44976771038/job/137068308407) | Success |
| Host24 dispatcher | [137068308495](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44976771038/job/137068308495) | Success |
| Genuine Host20 adapter | [137068568557](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44976771038/job/137068568557) | Success |
| Genuine Host24 adapter | [137068568495](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44976771038/job/137068568495) | Success |

Push-only handoff **137069099749** was expectedly skipped and is not counted as
a pass. The genuine hosts observed Node **20.20.2 / 24.19.0** and governed
**22.23.2**, with Yarn **1.22.22**. Every adapter stage passed, including clean
clone without generated inputs, frozen installation, the no-Git actual package
adapter, complete identity/package verification and runtime JSON metadata.
Both emitted `built=true` and the exact new release identity. Their complete
reproducibility records and selected frontend binding fields matched immutable
B2; package proof objects matched reverification and each other.

Fresh B2 proof values are the current artifact/meta/proof hashes above, with
metadata **568,965 bytes** and proof **1,521 bytes**. Both isolated Backend
packages contained **1,290 files**, tree SHA-256
`ba5540c4be1121d2e6fd4b0c66f609a0c12303687bd44f4876fd932bac489a96`,
and identity **484,039 bytes** / SHA-256
`5ee322ac13c301e79b76dc9f94a4fbc4ce8bd3dc6fe0d63505d538e795b991c6`.
Isolation and health checks passed without Git or a sibling Frontend. Both
Frontend packages contained **1,757 files**, tree SHA-256
`3aaa35bbacd5e2c49c50500153fcb266a484bd1e58b84d00403f93cf3ccaa2cc`,
and passed isolated runtime and `application/json` metadata serving.

The reviewer matched artifact API metadata to upload logs and exact B2:
Host20 outputs/evidence **14735662870 / 14735398620**, Host24
**14735754148 / 14736465034**. Sizes and digests are retained in JSON; those
artifact archive bodies were not downloaded. These are CI proofs, not evidence
of a production deployment or the platform's snapshot ordering.

At the latest progress update (**02:42:00 UTC**), only Operational backend
**137068308105** and Stock **137068307860** remained active. All other triggered
workflows had completed successfully or with an expected skip: **9 successful,
1 expected skip and 2 running workflows**; REST showed **21 successful,
6 expected skips and 2 running checks**. CodeQL aggregate **137068849137** was
successful. G47 Mongo **137068308299** passed its four overlapping stages
(**238 + 88 subtests**, **331 + 127 subtests**, **58**, **436**); these counts are
not summed. Overall CI remains **pending**, with zero failures observed. The two
long jobs and their strict 21-case/shipping evidence are still required.

## Current-contract coverage and original mechanism reproduction

The current five-module 83-case run includes all 21 actual-ASGI context tests.
They use synthetic data and a read-only database double with the real exclusion
projection; no real API/database writes or production requests are made. They
check raw successful JSON equality for inline/packed draft, active and frozen
state; absent-state behavior; nonempty openings; current issues; session digest;
permission masks; owner flags and assigned banks. They also check zero historical
page reads, intact current packet/manifest rejection, exact old current-corruption
503 bytes, missing/changed historical-page behavior, denied/revoked principals,
and unrelated HTTP errors. Full writer/audit readers remain fail closed.

Original reproduction on unchanged M3 yielded **11 failed / 10 passed in 5.83 s**:
healthy context still loaded three synthetic audit pages, and historical-page
body failures blocked current context. That demonstrated the unnecessary read,
not the live failure's cause. The initial corrected reader module passed **21
cases in 6.28 s**, and its original neighboring run passed **71 in 13.23 s**.
These are historical mechanism results, not additional unique tests or B2 CI
acceptance. The fresh corrected 83-case result above includes those same 71
cases plus the 12 lifetime cases.

The original source/test independent review passed at the exact route and
21-case module hashes retained in A2. It reviewed narrow error mapping,
current-corruption compatibility, byte parity, read-budget/integrity boundaries
and denied principals without rerunning tests. Its separate downstream trace
confirmed GET `/inventory` uses the existing owner-bound physical-stock snapshot
projection, not legacy operational state/audit reads; no broader inventory
change was made. This evidence author's successor review is described above.

## Limitations and prior Production

The original inventory permission bootstrap failed twice after M3 publication.
No matching inventory/context timing was retained, so its live cause remains
unproved. The current reader still loads the remaining current aggregate and
decodes financial/issue packets; no small-header memory bound or production
latency improvement is established. Current header/packet/manifest checks remain;
historical page-body validation is intentionally deferred only for context.
Native stocktake, physical inventory, later catalogs, payment writes and broader
server pressure remain unchanged.

Root separately completed M3 publication and three original live identity probes,
then observed a fresh inactive guard. The
[completed M3 evidence](https://github.com/AMASI-SA/AMASI-SA/blob/4ee3d64159cba4edd20133f81ec938cf33068ae8/docs/operations/OPERATIONAL-WORKSPACE-RELEASE-20261122/release-evidence.json)
is preserved at `4ee3d64159cba4edd20133f81ec938cf33068ae8`. It establishes prior
M3 identity, not this new patch or inventory recovery. Current B2 has not been
merged or published at this checkpoint.

## Next safe action for B2

1. Root checkpoints only these two evidence files on the evidence branch before
   the long CI wait. Retain the confirmed closed-without-merge #2057 record and preserve both
   frozen source branches.
2. Complete and inspect all required exact-B2 CI, including both backend jobs,
   actual executed-test/no-skips evidence and shipping follow-up, retaining the
   fresh B2 host rehearsal and Readiness identity proofs above. B1 results cannot satisfy these
   gates. Do not weaken a failing assertion or edit frozen A2/B2.
3. After required gates pass, root may perform the authorized normal merge and
   prove its source ancestry and B2 tree equality. Before shared-host changes,
   require a fresh inactive original guard, preserve unrelated files and follow
   the unchanged adapter/install/guard protocol for that deployment commit.
4. Use a fresh lease for `prepare`, final `prepublish` and the eventual single
   publication. Never deploy this evidence branch or reuse an old lease.
5. After explicit platform success, require the original
   `verify --url https://mezansalla.com` to pass all three probes for A2 and its
   release ID, then confirm an inactive guard. Separately perform one normal
   authenticated inventory load. If bootstrap or stock loading still fails,
   retain bounded matching response/timing evidence; release identity alone is
   not proof of inventory recovery.

This evidence author has made no staging, commit, ref, remote, source or host
change, and has posted no Issue #1006 comment. Root owns release actions and the
next durable checkpoint.

## Archived attempt — PR #2057 / A1 and B1

The following record is historical, **blocked and superseded by #2058**. Its
successful build, host rehearsal and partial CI are preserved for traceability
and **do not satisfy current B2 gates**. Its final matrix contains two failures;
no merge, lease or publication occurred for that attempt. Root confirmed PR #2057 closed without merge; its frozen head and branch
remain preserved.

### Immutable source and release identity

| Item | Exact value |
|---|---|
| Published base J/M3 | `02e8be41e6f1972806f0e095b8fdc38645a7f113` |
| Archived source A1 | `cde10efae4fb5b83f69b1c875f8c5c9ec6e6607c` |
| A1 tree | `08f64de11d97f7a07311bcfeac767ae1b8b49555` |
| Archived frozen B1 | `5758240bd83ed3018737ae59808f55415d80a689` |
| B1 tree | `277655692b1444382ac62ff77a9d38d299f5520b` |
| Runtime release | `rg5-5dcb1c33768a3de12da4360e427a0c511205441d3d445098ddcdee7cf61a1fdb` |
| Intent Git blob | `fabaeac56443c42a85aacea12c575fdd28bd6766` |
| Intent SHA-256 | `a02a9d3a5fb64ba55cd09afeab25de19b488f6bbfffd7964ae51a934967f42cd` |
| Frontend artifact tree SHA-256 | `65582eef5ae1c6aef9fc774c2ea10e5168c2fb396535c34b44be266526817526` |
| Build metadata SHA-256 | `97fbd42dcc0a23791b8695c6bac558bef994cfbc5e915697cec673f9ea69bfdc` |
| Reproducibility proof SHA-256 | `6d7691f62239836263b0781c78e4434f008fe4e82a94989344009852fe07e0c6` |

Independent read-only Git inspection confirmed A's sole parent is M3, B's sole
parent is A, M3-to-A contains exactly the four implementation/workflow/status
paths above, and A-to-B changes only `release/release-intent-v5.json`.
The external candidate is byte-identical to B's tracked intent: **1,466,981
bytes**. Root reported the original candidate/source and frozen-B provenance
validators passed. The reviewed route and test hashes below match exact A.
No financial math, frontend, shared reader or release protocol source changed.

### Governed build and actual-host rehearsal

The retained local step records distinguish the rejected first attempt from the
accepted candidate. They are not evidence of a publication.

| Step | Recorded UTC interval | Duration | Exit/result |
|---|---|---:|---|
| Pinned toolchain ensure | 02:11:15.196066–02:11:20.131216 | 4.935 s | 0 |
| Frozen dependency install | 02:11:20.132272–02:11:40.232674 | 20.100 s | 0 |
| Initial build without source bootstrap | 02:11:40.233126–02:12:00.111584 | 19.878 s | 0; built prior source identity |
| Initial freeze | 02:12:00.112413–02:12:01.710196 | 1.598 s | **2; rejected stale metadata** |
| Corrected source-bootstrap two-pass build | 02:12:30.040253–02:12:49.209287 | 19.169 s | 0 |
| Corrected freeze | 02:12:49.211175–02:12:51.328977 | 2.118 s | 0 |
| Original adapter on actual host, isolated B | 02:15:52.228358–02:16:49.662978 | 57.435 s | 0 |

All intervals are on **2026-11-22**. The initial freeze refused metadata source
`e41695244bbe0bfde4052e074d623b8c60f16331` when A was required. Root corrected the
missing source-bootstrap selection and reran the unchanged governed builder.
**No wrong artifact was frozen or published.** Both corrected clean passes have
the intended A, identical artifact tree and build-metadata hashes; pass 2 was
retained. The pinned toolchain is Node **22.23.2**, Yarn **1.22.22** and Vite
**8.2.4**. The frozen manifests cover 1,378 frontend source files, 1,289 backend
runtime files and 322 release-control files; these are manifest entries, not test
counts. The successful freeze also emitted a diagnostic for the absent ignored
`backend/release_identity.json` in the source worktree; root's original
provenance validation and subsequent adapter-generated identity passed.

Local evidence was read from `context-release-artifacts-2057/steps.json`,
`bootstrap-steps.json`, `identity-summary.json`, the candidate intent and their
associated logs. Every recorded local stdout/stderr byte count and hash was
checked against its file. The archived JSON record retains concise hashes and results,
not the full manifests or raw logs.

Root observed the original `/app` guard **inactive** and tracked `/app` clean at
M3 before the host rehearsal. The rehearsal ran `yarn --cwd frontend build` in
an independent shared clone at exact B under
`/tmp/mezan-pr2057-release-20261122-dee_dv_x`; `/app` source and generated outputs
were not changed. Host Node was **24.19.0**, host Yarn **1.22.22**; the unchanged
adapter selected governed Node **22.23.2**. It exited 0 with the exact A/runtime
release identity and tracked-clean staged source. Host output hashes, observed
and supplied by root, are retained in the adjacent JSON. No lease was created.
This rehearsal does not establish the later Cloud Build or live runtime result.

### Exact-B Readiness CI

[Readiness run 44975098798](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975098798)
completed successfully at B `5758240bd83ed3018737ae59808f55415d80a689`.
The independent reviewers supplied these results directly; this evidence author
has not duplicated CI monitoring. Readiness passed independently of the later
Operational backend and Stock failures below.

| Job | Exact job | Result |
|---|---|---|
| Backend preflight | [137063105634](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975098798/job/137063105634) | Success |
| Host Node 20 dispatcher | [137063105399](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975098798/job/137063105399) | Success |
| Host Node 24 dispatcher | [137063105654](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975098798/job/137063105654) | Success |
| Frontend build | [137063105669](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975098798/job/137063105669) | Success |
| Genuine Host Node 20 clean-clone rehearsal | [137063355717](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975098798/job/137063355717) | Success |
| Genuine Host Node 24 clean-clone rehearsal | [137063355676](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975098798/job/137063355676) | Success |
| Push-only manual handoff | [137063933315](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975098798/job/137063933315) | Expected skip; not counted as a pass |

The genuine rehearsals observed host Node **20.20.2** and **24.19.0**,
respectively, with host Yarn **1.22.22**. Both adapters used governed Node
**22.23.2**, exercised the no-Git package boundary and JSON-serving checks, and
emitted the exact frozen release, artifact, metadata and proof hashes above.
The reviewer confirmed their artifact metadata binds to this run and exact B.

Both isolated Backend packages contained **1,290 files**, had tree SHA-256
`d5e813202bf8170822077d44ff7a4d566427e39c56805e4803fd2dfd06f73ed3`, and
passed health identity verification without Git or a sibling Frontend package.
The generated backend identity was **484,039 bytes**, SHA-256
`0de2cc5bb77a50e010bddeb14f7ef429709ea04695c74e699fe7b076131ec285`.
Both isolated Frontend packages contained **1,757 files**, had tree SHA-256
`7b8b943fb2ec9693c2737da945df9711351edcb16dd7650a26a647a8a291ba81`, and
passed runtime artifact verification and exact JSON build-metadata serving.
These are isolated package counts, not test or source-manifest counts.

The archived JSON record retains the four metadata-bound output/evidence artifacts:
Host 20 **14735481628 / 14735113219**, and Host 24
**14735352084 / 14735352090**, with their sizes and reported SHA-256 digests.
Readiness success and clean-clone evidence do not establish publication or live
inventory acceptance.

### Required exact-B gate failures — release blocked

The independent CI reviewer inspected the completed Operational backend job
[137063183273](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975098873/job/137063183273)
in [run 44975098873](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975098873),
at the same frozen B. Its reported pytest result was **1 failed, 6,236 passed,
plus 90 subtests in 560.51 s**. These counts belong to this one failed job and
must not be summed with the local 71-case run or other overlapping workflows.

The failing case is
`test_operational_balance_active_read_lifetime.py::test_departing_panel_cancels_saved_state_read_without_writers[/context]`.
It waits for the blocked-reader `entered` event. Its fixture still replaces
`operational_balance_routes.read`, while this route now imports
`operational_financial_position_state.read_financial_position_state` locally.
The existing interception therefore no longer reaches this route's saved-state
read. This identifies a concrete instrumentation mismatch; it does **not**
waive the cancellation contract or establish that the new reader's cancellation
behavior has passed. Any minimal test correction must target the actual reader
and retain the stopped/499 response, no-task-leak and capability-free database
assertions.

The reviewer supplied result artifact **14735672226**, **107,637 bytes**, SHA-256
`7d9b7ddcbbde82d0bf8e605d7b6992d8bc7c3e564fc4a7c830239652b102057d`.
The reviewer downloaded and parsed its JUnit archive, verified that hash and its
binding to exact B/tree, and confirmed **all 21 new context-snapshot cases
passed**. The suite contains **6,327 JUnit cases including 90 subtests**, with
**one failure, zero errors and zero skipped cases**; this is the same failed job,
not an additional test run. Its downstream shipping and executed-test/no-skips
steps did **not** execute because of that failure and have not been accepted.
The completed Stock job
[137063174832](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975098761/job/137063174832)
in [run 44975098761](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975098761)
reported **1 failed, 5,524 passed, plus 97 subtests in 608.68 s**. Its only failed
case is the same `/context` fixture above; its no-skips/failures guard also
rejected that failure. The reviewer retained its exact-head upload-log artifact
metadata: **14736220546**, **94,754 bytes**, SHA-256
`715bca3140b779ec9a789feb1d7836e373418577cb6d7b7e1cb00e1f4a594e2c`.
No independent download/parse of this Stock archive is claimed here. Operational
and Stock coverage overlap; their pass counts are not added together.

Root created the separately reviewed successor #2058 from the pre-B source
lineage; its current identity and pending gates are above. A1/B1 and their
failed-job evidence remain intact. B1 must not be merged or published. Its
supersession does not convert its failed checks into passes.

### Final B1 CI matrix

The independent CI reviewer observed the settled matrix at
**2026-11-22T02:27:20.843Z**, with every retrieved run/check bound to B1
`5758240bd83ed3018737ae59808f55415d80a689`:

| Reporting surface | Success | Failure | Expected skip | Total |
|---|---:|---:|---:|---:|
| Workflow runs | 9 | 2 | 1 | 12 |
| Jobs in those workflows | 20 | 2 | 6 | 28 |
| REST checks | 21 | 2 | 6 | 29 |

The additional successful REST check is aggregate CodeQL **137063527470**;
it is not an extra workflow/job execution. Expected skips are the push-only
Readiness handoff, the TikTok job, and two scope-filtered jobs in each Snapchat
workflow. Skips are not passes. The Accounting workflow was not triggered:
none of its 65 path patterns matched either the four source-change paths or the
five frozen-change paths. It is neither a pass nor a skip in these totals, and
no previous Accounting result was borrowed.

| Workflow | Exact run | Result |
|---|---|---|
| Security | [44975099038](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975099038) | Success |
| Readiness | [44975098798](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975098798) | Success |
| G47 | [44975098799](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975098799) | Success |
| CodeQL | [44975098747](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975098747) | Success |
| Snapchat Publication | [44975098784](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975098784) | Scope success; two expected skips |
| Snapchat Settings | [44975099095](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975099095) | Scope success; two expected skips |
| Mobile | [44975098787](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975098787) | Success |
| Salary | [44975098781](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975098781) | Success |
| Qoyod | [44975098822](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975098822) | Success |
| TikTok | [44975098912](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975098912) | Expected skip |
| Operational | [44975098873](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975098873) | Backend failure; frontend/browser/worker passed |
| Stock | [44975098761](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975098761) | Failure |

Completed, separate coverage includes Security's full frontend **456 suites /
4,978 tests**, the operational frontend **89 suites / 1,415 tests**, operational
browser groups of **14 and 12 scenarios**, and **4 worker cases**. These results
overlap in coverage and are not combined into a unique-test total. The two
failed backend jobs remain release blockers despite those completed checks.
