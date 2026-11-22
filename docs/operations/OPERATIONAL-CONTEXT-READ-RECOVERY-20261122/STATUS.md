# OPERATIONAL-CONTEXT-READ-RECOVERY-20261122

## Scope and state

**Evidence checkpoint: 2026-11-22 02:26:10 UTC.** Source A and intent-only B are
frozen and remotely preserved in [PR #2057](https://github.com/AMASI-SA/AMASI-SA/pull/2057).
The original adapter also passed an isolated rehearsal on the actual host.
The exact-B Readiness workflow passed all six required jobs, but the required
Operational backend job has now **failed**. Release #2057 is **blocked**, with
stock still running among the **12 triggered workflows**. No overall CI success
is claimed. The Accounting workflow was not triggered
by its path filter and is **not** counted as a pass. There has been **no merge,
lease, publication or live acceptance for #2057**. Production is unchanged by
this correction at this checkpoint.

This document and adjacent `release-evidence.json` are maintained only on
`codex/operational-context-release-evidence-20261122`, initially based on B.
**This evidence branch must never be deployed or merged into the frozen release.**
Root owns its durable remote checkpoint and all release actions. The previous
evidence checkpoint is `1ac7888c1563b7b1d624c9e77196d5c8f8493a85`
(tree `4d5d63b27de8bcfb9be50877230480b1fa0c53fc`). Its new recording commits are
outside the reviewed source/intent pair.

The implementation branch is
`codex/operational-context-read-recovery-20261122` in `AMASI-SA/AMASI-SA`.
Root separately completed M3 publication, observed **3/3 original live
verification probes pass** and a fresh inactive guard, and preserved that
[completed M3 evidence](https://github.com/AMASI-SA/AMASI-SA/blob/4ee3d64159cba4edd20133f81ec938cf33068ae8/docs/operations/OPERATIONAL-WORKSPACE-RELEASE-20261122/release-evidence.json)
at `4ee3d64159cba4edd20133f81ec938cf33068ae8` on
`codex/operational-workspace-release-evidence-20261122`. Those observations verify
M3's deployment identity, not this correction or recovery of inventory loading.

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

## Implementation scope

- `backend/operational_balance_routes.py`: existing GET `/context` reader boundary only.
- `backend/tests/test_operational_context_snapshot_reader.py`: isolated actual-router contracts.
- The original implementation status document, subsequently expanded here on the evidence branch.

Root additionally owns `.github/workflows/operational-balance-web.yml`: it now
selects the new regression module and requires at least **21 executed cases** in
the existing no-skips/completeness gate. Existing thresholds are unchanged.

No frontend, grants, financial calculations, mutation behavior, protocol/guard,
frozen source, generated outputs or host state is in this change's write scope.

## Immutable source and release identity

| Item | Exact value |
|---|---|
| Published base J/M3 | `02e8be41e6f1972806f0e095b8fdc38645a7f113` |
| Source A | `cde10efae4fb5b83f69b1c875f8c5c9ec6e6607c` |
| A tree | `08f64de11d97f7a07311bcfeac767ae1b8b49555` |
| Frozen B | `5758240bd83ed3018737ae59808f55415d80a689` |
| B tree | `277655692b1444382ac62ff77a9d38d299f5520b` |
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

## Governed build and actual-host rehearsal

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
checked against its file. The adjacent JSON retains concise hashes and results,
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

## Exact-B Readiness CI

[Readiness run 44975098798](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44975098798)
completed successfully at B `5758240bd83ed3018737ae59808f55415d80a689`.
The independent reviewers supplied these results directly; this evidence author
has not duplicated CI monitoring. Readiness passed independently of the later
Operational backend failure below; stock is still pending.

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

The adjacent JSON records the four metadata-bound output/evidence artifacts:
Host 20 **14735481628 / 14735113219**, and Host 24
**14735352084 / 14735352090**, with their sizes and reported SHA-256 digests.
Readiness success and clean-clone evidence do not establish publication or live
inventory acceptance.

## Required exact-B gate failure — release blocked

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
Root assigned a minimal test-seam correction to a separate worktree on
`codex/operational-context-read-lifetime-20261122`, based on pre-B source A
`cde10efae4fb5b83f69b1c875f8c5c9ec6e6607c`. Its authorized source scope is the
active-read-lifetime test and source status document only. A successor A/B and
replacement PR are planned; their IDs and final results are not yet available.
Root will retain #2057 until a replacement exists. This evidence author has
made no source, test or frozen-intent change. Frozen A/B must remain intact; if a corrected
source is needed, use a new source/freeze sequence from the pre-B source lineage
and rerun the required gates at its new exact frozen head. Do not merge or
publish the currently failing release.

## Verification

Tests use synthetic data through the actual ASGI router and a read-only database
double that honors the current reader's exclusion projection. Unsupported database
operations raise; no real API/database writes are performed. The mobile-owner
case replaces only the unrelated bank directory, preserving `assigned_banks`,
the native permission policy, actor resolution and route behavior.

Environment: existing isolated Python **3.12.14** environment at
`/workspace/scratch/78a3f53e49a4/payment-setup-venv`; no dependency changes.
Commands ran from the task worktree root, with bytecode and pytest cache disabled.

### Red on unchanged M3

Before modifying `backend/operational_balance_routes.py`:

```bash
PYTHONPATH=backend:backend/tests PYTHONDONTWRITEBYTECODE=1 \
  /workspace/scratch/78a3f53e49a4/payment-setup-venv/bin/python -B -m pytest \
  --noconftest -q backend/tests/test_operational_context_snapshot_reader.py \
  --tb=short -p no:cacheprovider
```

Result: **11 failed, 10 passed in 5.83 s**, exit **1**. The six inline/packed
draft/active/frozen cases and the mobile-owner case returned the correct current
context but still read all **three** synthetic historical audit pages. The two
missing/changed historical-page cases returned 503 instead of the intended
current-context response. Two boundary-seam cases also showed that M3 did not
use the selected current-state reader. This reproduces the unnecessary read
mechanism; it does not reproduce or prove the cause of the live timeout.

An initial invocation omitted `backend/tests` from `PYTHONPATH` and failed
collection; it was corrected before the semantic red run. The first mapping
implementation exposed local `HTTPException` import shadowing in five cases;
using a separate local exception alias fixed it without changing the existing
permission loop. Neither intermediate failure was treated as acceptance.

### Green and focused neighboring regressions

The exact red command after the route change: **21 passed in 6.28 s**, exit **0**.

```bash
PYTHONPATH=backend:backend/tests PYTHONDONTWRITEBYTECODE=1 \
  /workspace/scratch/78a3f53e49a4/payment-setup-venv/bin/python -B -m pytest \
  --noconftest -q \
  backend/tests/test_operational_context_snapshot_reader.py \
  backend/tests/test_operational_financial_position_state.py \
  backend/tests/test_operational_balance_financial_position_routes.py \
  backend/tests/test_operational_balance_supplier_workspace.py \
  --tb=short -p no:cacheprovider
```

Result: **71 passed in 13.23 s**, exit **0**. The 21 new cases are included in
that 71, not additional unique tests.

The new contracts prove:

- Complete successful JSON bytes match the unchanged full-reader reference for
  inline and packed current state in draft, active and frozen statuses.
- The absent-state draft response, nonempty opening count, restored issues,
  session digest, owner flags, native permission mask and assigned banks remain
  unchanged.
- Context reads zero historical pages and excludes top-level write histories
  from transfer; synthetic stored data remains unchanged.
- Missing or changed historical page bodies no longer block current context;
  the unchanged full store reader still rejects those histories with HTTP 503.
- Corrupt financial packets, issue packets and audit manifests preserve the
  exact original context 503 response bytes and do not return permission data.
- Anonymous, disabled, employee, owner-shaped mobile employee and revoked-page
  principals cannot read state. Identity revocation after the router gate is
  checked again before the state read.
- Unrelated HTTP 403 and HTTP 503 failures retain their original response.

`git diff --check` passed. An AST comparison against M3 also confirmed that the
initial actor scope, entire permission/response tail, and entire module outside
this GET context reader body remain unchanged.

### Reviewed source bytes

| File | SHA-256 |
|---|---|
| `backend/operational_balance_routes.py` | `092fd7915285a2f654400631b3f70001e8b9eb11d205565fa2dae3650b50f14b` |
| `backend/tests/test_operational_context_snapshot_reader.py` | `fa4f9d765197ad062a2c753f52ce2453abdd516fd4cc38cfe4c561131d2c697d` |

Independent source/test review by `catalog_payload_evidence` passed these exact
hashes. It reviewed the narrow exception mapping, unchanged current-corruption
response, actual-ASGI byte parity, query-budget/boundary controls and denied
principals, with no requested change. The reviewer did not rerun the tests.
Its independent downstream trace also confirmed that `GET /inventory` uses the
existing owner-bound physical-stock snapshot projection and does not read the
legacy operational state/audit collections; no broader inventory patch was made.

## Limits and incomplete work

The reader still loads the remaining current aggregate and decodes financial and
issue packets; this is not a claim that context memory or latency is now bounded
by a small header. Native stocktake, physical inventory, later catalogs, payment
writes and broader server pressure are unchanged. Current financial/header and
archive-manifest validation uses the established stricter report reader; only
historical page-body verification is intentionally deferred to full readers.

This focused verification is not live acceptance or final exact-head release CI.
The production request failure remains undiagnosed without matching response/log
evidence. Root has registered the new **21-case** file in the explicit operational
workflow selection and its executed-test gate; this author did not edit CI.

## Next safe action

1. Root must durably checkpoint this next update to the two evidence files on
   the evidence branch; checkpoint `1ac7888c1563b7b1d624c9e77196d5c8f8493a85`
   already preserves the previous record. This author has not staged, committed, updated refs or written to a
   remote. No Issue #1006 comment has been posted by this author.
2. Root must resolve the required Operational backend failure above without
   changing frozen A/B or weakening cancellation assertions. If a correction
   is required, create a successor source from the pre-B source lineage, rebuild
   and freeze a fresh intent, and inspect all required CI at that exact new head.
   Preserve the failed B evidence and the final stock outcome. Do not count
   path-filter non-runs or expected skips as passes. The local 71-case result
   and successful Readiness workflow do not replace the failed gate.
3. Only after the required gates pass, root may use the existing authorized
   normal-merge release workflow. Preserve A/B and prove any production merge
   has B's tree and the required source ancestry. Never deploy this evidence
   branch, alter frozen A/B, or reuse M3's closed lease.
4. Before any shared-host installation or release action, rerun the original
   `/app` guard status and require inactive; preserve unrelated files, reconcile
   the reviewed production commit and repeat applicable original adapter/guard
   checks for that exact deployment commit. Use a fresh lease for the eventual
   `prepare` / final `prepublish` / single publication sequence.
5. A later publication requires explicit platform success, the original
   `verify --url https://mezansalla.com` passing all three probes for this A and
   release ID, then a fresh inactive guard. Separately perform one normal
   authenticated inventory load to establish whether permission bootstrap and
   the subsequent stock request complete. Capture a bounded matching response
   or timing if it fails; do not infer recovery from release identity alone.

At this checkpoint the release is blocked by the required Operational backend
failure. Stock is still pending; any successor release also requires its exact-head
CI, applicable host checks, merge/host installation, publication, original live
identity verification and live inventory acceptance.
The deployment owner must update this evidence after those observed milestones.
