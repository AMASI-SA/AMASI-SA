# Final Production candidate V2: frozen assembly inputs

This record supersedes the deployment-candidate identity in
`../FINAL-PRODUCTION-CANDIDATE-20260926/INPUTS.md` and PR #1160.
That earlier record remains historical provenance. The owner authorized only
candidate assembly, tests and a Draft PR. Merge, deployment, publication,
database changes, write-pause changes and financial activation remain unauthorized.

## Immutable inputs

| Input | Git SHA |
| --- | --- |
| New reviewed Production base J2 and rollback code target | `096edc75f398d6d48b5cc8e4189354af10342cbc` |
| Accepted prior Source A | `5dd49ed8a41f33bb77d24c139d49db0cba8c5203` |
| Prior Source A's base J1 | `aa0eac73128dfcded4a1aaaa1217cb6c9d16810c` |
| Frozen PR #1131 | `6f777f22c06fc9a7a96f131b7a8b3fd0aaf67a9c` |
| Frozen PR #1157 / G47 | `f2431282c3728a8d2fe618dbbe7a8616196f068d` |

Branch: `codex/mz2-g47-final-production-candidate-v2-20260927`.

J2 incorporates merged PR #1158. Its reviewed intent points to source
`ff4e7011e6ff411974927eef839fa58b53f8e3e4`; the content difference from
that source to J2 is only `release/release-intent-v5.json`.

## Exact source assembly

Apply `git diff --binary --full-index J1 A` onto J2. This accepted source
patch contains 214 paths, excludes release-intent, and has SHA-256
`5993b954e30bc6aafd7e6f7de0f27a06639a5702db12994ca82b06b6c04250a4`.
Its path set does not overlap any path changed by J1..J2. Every imported Git
blob must match accepted A exactly; all other paths must remain J2 except
this additional V2 input record. No fresh runtime, test or workflow edits
are introduced by V2 assembly.

The original input lineage remains in
`../FINAL-PRODUCTION-CANDIDATE-20260926/SOURCE-COMMITS.txt`.
The accounting/G47 PRs and original A/B are not rebased or amended.

## PR #1158 preservation

These raw Git-content SHA-256 values must match J2, A2 and final B2:

| Path | SHA-256 |
| --- | --- |
| `backend/integrations/qoyod_manual/send.py` | `8c8aaf2f6e5b3a47512d2a3ee812473254f9da94ce8ee1f0041aa0510e4d9c03` |
| `backend/qoyod_auto_unified/queue_select.py` | `5ffaea7a92a4f7d097740de41d6a4b058e9bd40f91018f6e3e8b03c5c55bb608` |
| `backend/tests/test_qoyod_manual_send_plan_b.py` | `a4193736ba9f4b2eaa88bd55898ad152a8200dfa6b7883a679f18395b8bd1fbc` |
| `backend/tests/test_qoyod_unified_backlog_20260822.py` | `babec8112c919302ea233afae7dc0d526fe3797fe69fb43e2403cd78f0b6bfeb` |

J2's intent must remain byte-identical through the entire J2..A2 history.
Its SHA-256 is `b0992d25ca48a4a6982da0395449ec2ffc290d3dcc0058efab2c8eff9e7fa2a0`.
Only B2 replaces that intent, using the exact artifact from the unchanged
protocol-v5 governed build and validation of A2 on J2. The old #1160 intent
must never be transplanted.

## Required verification and handoff

Before any release rehearsal, read the shared /app release guard status and
require active=false. Do not prepare a lease or change /app.

Commit A2 as one source commit above J2. Generate and validate the new
candidate intent using Release Readiness at exact A2. Commit B2 as one
intent-only commit above A2. Do not alter either commit after freezing.

At exact final B2, require executed PASS for MZ2 Accounting Module,
G47 Focused Integration, Fulfillment V2, Qoyod Payment Freshness,
Security Gate, CodeQL Python and JavaScript/TypeScript, frontend build,
Release Readiness, and Host Node 20 clean-clone adapter rehearsal.
Dispatch workflows on the dedicated branch if their PR checkout uses a
synthetic merge ref. Mandatory skipped checks do not count as passed.
The production-push-only manual-deployment notification remains inapplicable.

Record A2, B2, tree, PR, full diff, release identity, intent SHA-256,
Qoyod equality proof and CI evidence in Issue #1006. Final evidence is
external to the frozen source; stop for supervisor review.

## Scope and rollback

No new features, ACCOUNTING_SETUP_CENTER_UI, G48/G49/G50, financial policy
changes or weakened guards. Preview UAT remains waived for this cutover.
Code deployment and financial activation are separate future authorizations.

Rollback code target is J2 above. This assembly changes no running system
or database. Rejecting it requires no operational rollback. A later
authorized code rollback must use the existing release protocol and retain
data; it is not a financial reversal.
