# PR2080 server publication evidence — intent frozen; exact-B CI running

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

## Current exact-B CI and host state

Exact-B CI is running: Readiness45071567803, Operational45071567938,
Stock45071567946 and G4745071567954. Both actual clean-clone host Node20.20.2
and24.19.0 adapters are required, alongside all applicable complete test/security
jobs. No B CI approval or host rehearsal is claimed at this checkpoint.

PR2074 is complete/released at b46798fab681471a0fdf9450c30d6446a4f55ffa.
The21:14UTC canonical ledger still gives PR2076 the serial host handoff. It has
issued pinned staging/prepare/publication code but owner execution and original
live verify3/new UI success/final inactive status are pending. Evidence32661e1a8e6ff3a868eb3ffd010bc9d2f9a38dc2
records that foreign state. Do not replay its rehearsal or clear its lease.

Next safe action: finish and independently accept complete exact-B CI/artifacts;
read fresh Production and prior handoff; only after actual predecessor completion
and a fresh original inactive guard may the owner run this task's pinned isolated
host rehearsal. Rehearsal is not staging or publication. Actual PASS precedes an
ordinary tree-equivalent merge and separately reviewed guarded staging.

Production changed by this task:NO. Shared host changes:NO. Own lease:NONE.
Publish/financial reversal/receipt approval/sync/backfill/production tests:NONE.
Automatic continuation remains paused. PR2078 is closed unmerged as superseded;
old source/intent histories remain preserved. PR2080 is the canonical successor.
