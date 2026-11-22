# PR2080 server publication evidence — all exact-B gates accepted; host handoff pending

This is an evidence-only branch. Never merge or deploy it. The product branch is
`codex/operational-balance-server-r3-20261122`; the user requested manual server
publication code after Android1.0.27/code30 was distributed.

## Immutable source and intent

- Production base N: 02cd99e5b2b68c95341c87590abcf572c885e697 (PR2076 merge).
- Source A: dd3a9ea29fce28e2fa87332cff4a6d46f776e0d3.
- A tree: ee385c9c71a19833e08b5e5d7470d94fe4b646f4; sole parent N.
- Intent-only B: 9a500a21013a08f750009be656362481986eb208.
- B tree: 2cbd2842ed2506642a1321a47e35f69a766fcf89; sole parent A.
- B changes only release/release-intent-v5.json. N..A intent history is empty.
- Intent1493695bytes, SHA2560e0c7a6167dae598450358db6a1399b080cb7c69e176d28c5c31a0585ed287dc.
- Runtime rg5-b2aa207d2f63107c64d9c0b66ce5cc567aa5ebb074753cf889227de4c448e008.
- Frontend artifact000b707f16f0f3447cfe30f8e0e5edafc58ef55698ff2444e3fcca67f5ffb4db.

The original candidate loader and Git-rederived manifests passed on clean A.
Independent review accepted the exact ZIP/intent/source/proof and two governed
builds. Original load_release_intent and verify_release_intent_git also passed
on canonical clean B. [Canonical validation](evidence/canonical-B-validation.json).
[Retained exact candidate](evidence/release-intent-candidate.json) is byte-identical
to B's intent; Readiness45070050540 artifact14773520519 ZIP SHA256 is
4da0cc40b81b6f48e7ced7ca11dd5e8a0dcd4b51caa65b6799ab15ae6750446c.

## Accepted local integration

214 distinct Backend cases verified using an owned temporary loopback Mongo
replica and actual worker children. First212pass/2fail diagnosed the test venv's
missing repository-declared python-dotenv1.2.2; after installing that pin, both
failed cases and3 lifecycle cases passed5/5 with unchanged source/test hashes.
Initial failure evidence is retained. The counts overlap and are not added as217.

Frontend101pass/7suites on Node22.23.2/Yarn1.22.22, with no failure or skip.
A delayed-refetch test first proved the owner-ledger success dialog remounted
closed; the minimal resource-ready effect fix made that test and all7suites pass.
Independent source/workflow reviews and root's exact source hash checks pass.
Every newer performance/customer/inventory path and gate is preserved.

## Source-A CI — retained failures, not relabeled green

Source A ended with9 successful workflows and3 failed workflows; jobs19success,
6 conditional skips and2cancelled. Frontend1660/103suites, browser/mobile/receiving,
canonicalworker4, Readiness and other completed checks passed.

G4745070050550 failed before creating jobs. Its public annotation confirms a
GitHub internal server error, correlation2447cc82-e285-46a5-889a-6cb329a8b420.
One targeted retry returned403 This workflow run cannot be retried; no alternate
retry or dispatch was attempted. Operational backend45070050599 and stock45070050575
were cancelled together at21:17:02UTC before B existed. The backend log reports
runner shutdown after partial progress. These incomplete jobs did not pass and
were not cancelled by the later B update. Raw terminal status and partial logs
are retained under evidence/ci-source-A.

The normal planned v5 intent-only B follows the accepted build candidate; no
source/workflow/test/assertion was changed to affect these failures. All final
release gates must execute afresh and PASS on B before host work or merge.

## Exact-B technical acceptance — complete

Root freshly re-read all B checks and workflow runs. Exact B has25successful
checks,6 justified conditional skips and no failure, cancellation, neutral or
pending result. All12 applicable workflows succeeded; the separate TikTok branch
workflow skipped. All were attempt1. No B retry or dispatch was performed.

- Operational45071567938: backend6564+90subtests; demand106 withzero skips,
  shipping16+18subtests; frontend1660/103suites; browser/mobile/receiving and
  canonical worker contracts PASS.
- Stock45071567946:5531+97subtests, zero skips/failures/errors.
- G4745071567954: both backend/frontend jobs PASS, real-Mongo G47 core331+127subtests
  plus Salla projection/native inventory/order-cost suites.
- Security45071567841 andCodeQL45071567839 PASS.
- Readiness45071567803: actual clean-clone adapters on Node20.20.2 and24.19.0 PASS.

Counts overlap across groups and must not be summed as unique cases.
The6 skipped checks are4 Snapchat jobs outside scope, a TikTok branch-specific
job and manual-redeploy notification for a push event; no required test job above
was skipped.

Independent four-artifact acceptance verified ZIP sizes/digests against exact-B
GitHub metadata, safe inner-tar extraction and sidecars, all389 generated files
(byte/path/mode/size identical acrosshosts), original Frontend/Backend/identity/
reproducibility validators, and package boundaries exactly matching both saved CI
proofs. Actual CI logs also show the original adapter and canonical/cache-busted
JSON metadata MIME/bytes checks on loopback. This is CI evidence, not Emergent.

Package proofs:1789 Frontend files and1313 Backend files. Generated bytes13242286
perhost. Artifact tree000b707f16f0f3447cfe30f8e0e5edafc58ef55698ff2444e3fcca67f5ffb4db;
build-meta9b79083c4ece9cccdc8e0ff28295070fa8cb74776d99ece2601465e5f8f2548e;
proofa159459c17824c50f3c8810cf11dd6825a9e576994131f037daadc397cd6d18f.
The original downloaded outputs/validators were not changed. A local source
archive mode refusal is retained; command-local tar.umask0022 reproduced normal
Git checkout modes before the independent validation.

[Technical acceptance](evidence/TECHNICAL-ACCEPTANCE-2080.json).
[Full exact-B CI](evidence/ci-B/terminal-summary.json).
[Four-artifact acceptance](evidence/artifacts-B/verification-summary.json).
All raw source-A infrastructure failures/cancellations remain preserved separately;
they are not relabeled green or attributed to a B update that happened later.

## Actual host handoff — waiting for owner evidence

Production Git was freshly read and still equals N02cd99e5. PR2080 remains Draft,
open and unmerged at exact B. Current source and intent are immutable.

PR2074 is complete/released at b46798fab681471a0fdf9450c30d6446a4f55ffa.
The latest canonical ledger still gives PR2076 the serial host handoff. It issued
pinned staging/prepare/publication code at evidence32661e1a8e6ff3a868eb3ffd010bc9d2f9a38dc2,
but the owner-result/explicit new UI success/original verify3/final inactive status
is not recorded there. A public health GET now observes the expected PR2076
runtime108024c00b7f367fd1d6b15874d7a73d55de2620 and all verified flags true.
That read does not establish its original verification, lease closure or released
handoff; no actual shared guard result was observed by this task.

The [informational owner command](HOST-STATUS-2080.py) calls the unchanged original
guard status with a30second bound and prints only selected identity/ownership
fields plus current branch/HEAD, tracked-clean status and remote N/B refs. It does
not create/remove a lease, stage sources/generated outputs, verify a foreign
release, merge or publish. The original status may take its ordinary advisory
operation lock. Syntax reviewed; it has not been executed on the host here.

The isolated rehearsal draft remains ENABLED=False with no predecessor completion
pin. Its known A/B/N/tree/runtime/intent are frozen; it stops before any host
access. Do not send this disabled draft as a working publish command or replace
its missing actual completion evidence with an invented receipt.

Next safe action: owner supplies actual original guard/current-host result and,
if complete, predecessor UI success/original verify3/closed-lease evidence. Read
fresh refs and canonical coordination. Once the prior handoff is released, pin
that actual receipt, finish the launcher review and run the isolated original
adapter on the actual host. Actual PASS precedes ordinary tree-equivalent merge,
then separate guarded staging/prepare/prepublish and owner publication/verify3.
No new permission is requested for actions already authorized; the missing input
is actual host and prior-release state.

Production changed by this task:NO. Shared host changes:NO. Own lease:NONE.
Publish/financial reversal/receipt approval/sync/backfill/production tests:NONE.
Automatic continuation remains paused. PR2072 and2078 are closed unmerged as
superseded; frozen branches/identities remain unchanged. PR2080 is the successor.
