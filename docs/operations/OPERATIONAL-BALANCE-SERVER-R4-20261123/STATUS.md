# PR2082 — final technical acceptance complete; owner host rehearsal ready

The owner requested manual server publication code after the operational mobile
release. Current R4 preserves the newer merged Mezan86 source and adds eligible
manual movement reversal, full scoped running balances and optional mobile bank
references. All source and final release gates passed. The exact enabled owner
command is ready; actual host rehearsal and publication have not happened yet.

Evidence-only branch: `codex/operational-balance-server-r4-evidence-20261123`.
Never merge or deploy this branch. Product branch:
`codex/operational-balance-server-r4-20261123`. Current task PR2082 is Draft/open/
unmerged. It is the durable continuation fallback because the shared Issue1006
comment is full/stale; no foreign section or task status is altered.

## Frozen release identity

| Item | Exact value |
|---|---|
| Reviewed current Production N | 44122bcc986bd1dff1532b9ad62c0268e899a179 |
| Source A | aec203382b387c3812509084e77c2922aa860877 |
| A tree | f133f04fd5759c57f5cce195e35ae03ba53ef6da |
| Intent-only B / PR head | 0bd6a3fadd33d6354ebdc8dec814bcb80b0d7218 |
| B tree | 4fa54935f420e2a546c2237c0fb7b0ef9ef8367d |
| Intent SHA256 | 59da5ee6a86f3bfa5a1b8446c6aacb8e180144bc15ccaca3e414e1ec23b151ab |

A's sole parent is N; inherited intent and full N..A intent history are unchanged.
B's sole parent is A; it changes only release/release-intent-v5.json and freezes
the exact independently accepted 1,502,688 bytes. Original candidate loader and
Git/source manifest checks pass; original load_release_intent and
verify_release_intent_git pass at clean canonical B. Never rewrite this pair.

Runtime: rg5-12e0f132b3ee688815cd2ec401bd34c43706d193492edacc7be6c5ca6e6ade8a.
Frontend artifact: 27b7717df8a2dcc2b46868da4d103b44d33e2ad07c879da6563ecaef7e545b9a.
Build-meta: c13f1ac0a7d28dbefca1fd234314521482bc3b34def126d719dc24672bd39368.
Reproducibility proof: d25651371324610544e3f2de69f0bbaba41c0d9ae1f71c60e3dc4ac2b2d2469a.

## Behavior and preserved work

Reversal appends a linked inverse and retains the original, separate permission,
own/manual/eligible movement checks, reason/review/confirmation and stable retry
identity. Reversed supplier payments cannot be allocated or pass allocation CAS
revalidation. Account history is scoped and computed before filtering/pagination.
Bank references remain optional in existing mobile paths. Preserve current
customer/inventory behavior, bounded demand workers, first-visible-read latency
work and the delayed-refetch ledger result dialog repair.

Independent reviews plus root rechecks preserve all 74 non-overlap current Backend/
Frontend paths exactly from N. The 5 overlapping Backend files retain current
refund/provider/atomic/notification boundaries. All 6 feature Frontend paths are
exact prior reviewed source. The sole workflow conflict keeps both sets of gates;
no current assertion/selector/condition was removed or relaxed.

## Final verification — accepted

- Exact A: 12 workflows success, 23 checks success, 6 justified conditional skips,
  all attempt 1. No failed/cancelled/neutral/pending result.
- Exact B: 12 applicable workflows success plus 1 unrelated TikTok branch workflow
  skipped; 25 checks success and 6 justified conditional skips, all attempt 1. No
  failed/cancelled/neutral/pending result. No required test group was skipped.
- B Operational 45168927861: 6,615 Backend cases  + 90 subtests, demand 106,
  Frontend 1,675 / 104 suites; zero failed/error/skipped cases in required groups.
- B Stock 45168927735: 5,531 cases  + 97 subtests; zero failed/error/skipped cases.
  G47 45168927739, Security 45168927688, CodeQL 45168927696 and other applicable
  final workflows passed. Counts overlap and are not a unique aggregate.
- Local Backend 237 unique cases / 14 suites and Frontend 339 / 16 suites passed. Initial
  unavailable-Mongo failures/exit 130 and the wire-listener diagnostic remain
  retained. The 3 omitted local modules and exact CommandListener case all passed
  on real-Mongo CI. No source/assertion was changed for those local limits.
- Source candidate ZIP 14815305161 from Readiness 45166179398 matched digest/size;
  original validators and independent 3,067 Git-blob audit matched all 1,410 Frontend /
  1,325 Backend / 328 control manifest records and two actual governed builds.
- Exact-B Readiness 45168928015: both actual clean-clone adapters on host Node 20.20.2
  and 24.19.0 passed. All 4 downloaded artifacts passed independent original
  artifact/identity/health/proof/package validation; root independently matched
  fresh GitHub metadata and all 4 ZIP digests. All 389 generated files match across
  hosts. Recomputed package boundaries equal saved CI: 1,796 Frontend / 1,326 Backend
  files. Canonical/cache-busted build-meta bytes/JSON MIME under both Accept
  headers passed in original CI loopback. This is not actual Emergent proof.

Complete local/source-A records and initial failures remain losslessly archived
with per-file hashes. The final-B archive retains raw original XML/Jest/benchmark
records, full original CI/validator outputs and artifact audit/request/metadata,
plus all controlled helper evidence. Raw generated tar/download ZIP/source-copy
trees are reproducible and excluded. Original evidence bytes are not rewritten.
See TECHNICAL-ACCEPTANCE-2082.json and the archive manifests.

## Enabled concrete owner command

`HOST-REHEARSE-2082.py` is now ENABLED=True with all frozen identities above.
Its exact SHA256 is:

`3a41c78f78f19509129a2f8e0f15a903b7505d45b16fea89b9dd54c8621ec2f8`

The immutable raw-file launcher supplied in chat first verifies that digest.
The helper freshly requires the original guard exactly inactive; shared branch,
HEAD N and tracked-clean state; and remote Production N/candidate B. It reports
actual /tmp and /app space and requires 10 GiB in /tmp without deleting files. It
clones into a unique retained /tmp directory, proves N/A/B trees and intent-only
ancestry, then runs the unchanged original package adapter through yarn build.
It validates exact runtime/artifact/package proofs and repeats shared guard/ref
checks before accepting. It never stages /app source/outputs, creates/closes a
lease, merges or publishes. The original status may use its normal advisory lock.

Independent review caught missing SIGTERM/SIGHUP cleanup in the earlier disabled
draft. The final helper cleans only its own process group with bounded waits,
including a descendant that ignores TERM. All 4 controlled process cases passed,
including repeated TERM/HUP/INT signals, retained failure receipt and no running
descendants. Initial finding is retained. Final activation changed only the
ENABLED flag plus docstring/comments; normalized executable AST is identical to
the reviewed/tested helper. Root and independent final activation checks PASS.
Neither root nor reviewers invoked main or touched actual /app.

## Host handoff and exact next steps

The owner supplied real inactive original guard and clean shared N. This does
not assert prior PR2081 publication/verify3 completion. COORDINATION-REVIEW.md
records why current isolated rehearsal uses fresh original guards and exact
current refs without a stale 2076-specific receipt requirement or new approval.
No foreign automation, lease, release status or files are changed.

1. Owner runs the single pinned helper once. Keep its REHEARSAL_DIRECTORY,
   adapter.log and result.json. Return REHEARSAL_RESULT/RETAINED_RESULT, or STOP.
2. On actual PASS, root freshly reads Production/candidate refs and retains the
   actual host evidence. Do not repeat the successful rehearsal. Perform this
   task's already-authorized ordinary merge only if D has ordered parents N/B
   and tree exactly B; validate original source/intent relation on D.
3. Prepare a separate exact-D staging command. Fresh original inactive guard,
   shared clean N, remote D/B and retained rehearsal proof must match. Preserve
   unrelated tracked/untracked files and verified backups; FF-only stage D and
   the 3 owned generated outputs. Never reset --hard or clear a foreign lease.
4. Original prepare/prepublish only after actual rehearsal passes, immediately
   before one owner publication. Require new explicit Deployment Succeeded,
   original three-probe verify against https://mezansalla.com with full identity,
   hashes, source, artifact and JSON-MIME checks, then final inactive status.

STOP or a changed ref/active guard is not authorization to clean/rewrite/clear
anything. Retain evidence and diagnose the actual condition. No new permission
is sought for already-authorized preparation; the next input is actual host
execution evidence requested by the manual publication workflow.

Production changed by this task: NO. Actual host rehearsal: NOT RUN. Own lease: NONE.
Shared source/outputs changed: NO. Merge: NO. Publication: NO. Automatic continuation
remains paused. No live financial/approval/sync/backfill test writes or paid chat.
Old 2072 / 2078 / 2080 remain closed superseded, with frozen branches/evidence intact.
