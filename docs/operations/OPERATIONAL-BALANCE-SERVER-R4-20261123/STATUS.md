# PR2082 — source accepted; exact intent B frozen; final B gates in progress

Evidence-only branch: `codex/operational-balance-server-r4-evidence-20261123`.
Never merge or deploy this branch. Product branch:
`codex/operational-balance-server-r4-20261123`.

## Frozen identity

| Item | Exact value |
|---|---|
| Current reviewed Production N | 44122bcc986bd1dff1532b9ad62c0268e899a179 |
| Source A | aec203382b387c3812509084e77c2922aa860877 |
| A tree | f133f04fd5759c57f5cce195e35ae03ba53ef6da |
| Intent-only B | 0bd6a3fadd33d6354ebdc8dec814bcb80b0d7218 |
| B tree | 4fa54935f420e2a546c2237c0fb7b0ef9ef8367d |
| Intent SHA256 | 59da5ee6a86f3bfa5a1b8446c6aacb8e180144bc15ccaca3e414e1ec23b151ab |

A is a direct child of N and leaves inherited intent and full N..A intent history
unchanged. B is a direct child of A, changes only release/release-intent-v5.json
and freezes the independently accepted1502688candidate bytes unchanged. Original
load_release_intent and verify_release_intent_git PASS on canonical clean B.
Never amend/rebase/squash this pair. PR2082 remains Draft/open/unmerged.

Runtime:rg5-12e0f132b3ee688815cd2ec401bd34c43706d193492edacc7be6c5ca6e6ade8a.
Frontend artifact:27b7717df8a2dcc2b46868da4d103b44d33e2ad07c879da6563ecaef7e545b9a.
Build-meta:c13f1ac0a7d28dbefca1fd234314521482bc3b34def126d719dc24672bd39368.
Reproducibility proof:d25651371324610544e3f2de69f0bbaba41c0d9ae1f71c60e3dc4ac2b2d2469a.

## Source and local verification — complete

All12 applicable exact-A workflows succeeded, attempt1;23successful checks and
6conditional skips. No failure/cancellation/neutral/pending result remains.
Source-A skips are4out-of-scope Snapchat jobs, the adapter job gated on intent B
and the manual publication notification. No source-A host adapter is called PASS.

Operational45166179571: Backend6615cases +90subtests, demand106, zero skips,
failures or errors. Frontend1675/104suites, zero failed/pending/todo/runtime errors.
Downloaded backend artifact14815656670 and frontend artifact14814512950 have exact
GitHub ZIP digests and A/tree provenance. The three omitted local module groups
all executed on real-Mongo CI (29/25/40cases); the exact local CommandListener
case also passed. Local environment limitations are thus covered by actual CI,
not silently relabeled. Source-A Stock/G47/Security/CodeQL and other gates pass.

Local Backend237unique cases passed/14suites, zero fail/error/skip;1explicit
real-Mongo-only case was deselected locally, and3whole modules omitted from that
corrected subset. Python3.12.14 locally versus3.11 in CI. The initial interrupted
exit130 run and focused wire-listener failure remain preserved. No source/test
assertion was weakened or modified for these environment limitations.

Local Frontend339/16suites passed, zero failure/skip/todo/runtime error, on original
governed Node22.23.2/Yarn1.22.22. Root independently checked XML/Jest totals and
source identity. Frontend and all Backend source hashes remained unchanged.

Independent reviews and root rechecks preserve all74non-overlap Mezan86 Backend/
Frontend paths exactly from N. Five overlapping Backend runtime files retain
current refund/provider/atomic/notification scope. All6feature Frontend paths
are exact prior reviewed A. Workflow union retains every current gate. No new
integration defect was found.

The complete local/source-A verification records are retained losslessly in
local-and-source-A-verification.zip, with exact per-file manifest. Reproducible
dependency install logs are excluded; installed pins/results remain documented.
The initial failures and their commands are retained unchanged. Candidate JSON
and independent manifest/build evidence remain committed separately.

## Exact-B acceptance — running

Readiness45168928015 runs original clean-clone adapters on host20.20.2 and24.19.0
(jobs137673514315/137673514279). Both jobs have passed and their4artifacts are
downloaded with exact GitHub metadata/digest checks. Independent full artifact/
package/identity/route evidence acceptance PASS; root reread the proof and freshly
matched all4GitHub metadata records. All389generated files match across hosts,
and original package proofs match CI exactly (1796Frontend/1326Backend files).
Other exact-B workflows, including full Operational/Stock gates, must finish.
No source or workflow change occurred when B was created.

## Actual host handoff

Owner supplied inactive original guard/clean shared N. That does not claim
PR2081 publication/verify3 completion. COORDINATION-REVIEW.md explains the current
isolated rehearsal scope without a stale2076-specific receipt requirement or a
new approval gate. The helper requires fresh original inactive guard, exact
shared N/branch/tracked-clean state and current remote N/B before/after the
original adapter; it never touches foreign leases or shared source/outputs.

HOST-REHEARSE-2082.py now has all frozen pins, but remains ENABLED=False pending
final B acceptance. An independent review found missing SIGTERM/SIGHUP cleanup
in the previous disabled draft. Root added controlled interruption and bounded
cleanup of only its owned process group, including remaining descendants.
Controlled SIGTERM/SIGHUP tests PASS4/4, including TERM-ignoring leaders and
descendants plus repeated signals, bounded cleanup and retained failure receipt.
Independent final-pin/helper review found no remaining concrete blocker. No actual
/app invocation is claimed. No live files or leases were modified by that fix.

Next: accept remaining exact-B gates/all4artifacts and final helper review;
checkpoint the enabled/pinned helper and issue the owner command. Actual host
PASS precedes this task's ordinary tree-equivalent merge, followed by separate
guarded staging/prepare/prepublish. Final completion requires new Deployment
Succeeded, original verify3 and inactive status. Preserve all temporary evidence
and unrelated files; never clear another task's lease or use reset --hard.

Production changed by this task:NO. Own lease:NONE. Shared host changes:NO.
Publication:NO. Automatic continuation remains paused. No live business test
writes or paid chat. Old2072/2078/2080 are closed superseded without rewriting
their frozen branches/evidence. Current durable handoff is PR2082 because the
shared Issue1006 comment is full/stale; its foreign sections are untouched.
