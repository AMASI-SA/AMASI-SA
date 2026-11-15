# Ready refresh recovery — publication evidence checkpoint

This branch is an evidence ledger only. **Do not merge it as application source.**

- Application PR: https://github.com/AMASI-SA/AMASI-SA/pull/1955
- Application branch: `codex/ready-refresh-recovery-release-20261116`
- Evidence branch: `codex/ready1955-publication-evidence-20261116`
- Source A3: `1accaa42e132ca1232ce86b3122971b48169ca81`
- Source tree: `be16191c9c2498a942fa7974455c6c24e6fac005`
- Sole parent/current production Git base J2: `2589f440106a7c5d463ee03eac3617d113319673`
- Intent-only B: not created.
- Production changed by this task: **no**.

## Preserved proof

The exact CI-produced candidate intent is retained under `evidence/candidate-release-intent-v5.json`, alongside the artifact provenance receipt, root validation and independent acceptance. Both official candidate parsers accepted exact A3/J2 in a fresh tracked-source clone; Git manifests matched. The six runtime/test/workflow blobs remain unchanged from the independently reviewed implementation.

Candidate SHA256: `0d90b88def88e0c84d595376d050e80f3e18493bf9ca71284382af1af8754899`.

Deterministic runtime: `rg5-255b2b13ed893324729b31c21409fa1be3be98d8c536485f712424d70bab4b4e`.

At the 2026-11-15 23:42 UTC coordination checkpoint, 12 of 13 A3 workflows had succeeded. Review Completion Resumable run `43775290295` was still executing the existing local approval/provider suite; its new real-Mongo recovery and Android gates had passed. This is not full A3 acceptance. No B, merge, lease, original rehearsal or publish has been performed by this task.

## Independent incident boundary

The separately reported received garment has valid receipt evidence and an already active preparation workflow; its Ready guard is an unresolved source/approval representation comparison. Historical witnesses reconstruct the approved raw source exactly, but the current proof function still rejects the current source. This patch and these release steps must not clear that flag, assert the incident is repaired, or change receipts/services/Ready/provider status. Private order/customer evidence is excluded from this branch.

## Coordination and next action

PR #1951 owns its separate original-workspace release. It has reached original HEAD J2 but stopped before adapter execution because of pre-existing bytecode; its owner is preparing a bounded resume. The latest observed live version remains `3e5bbfb` (100th publish). A historic inactive guard does not establish that owner has finished. Preserve its operation and wait for an explicit completed handoff before overlapping `/app` work or merge/publication.

Next: accept complete A3 exact-head CI, freeze only the candidate bytes as B on the application branch, independently validate B and run its Host20/24 clean-clone gates. Before merge and original work, recheck production base and the prior owner's handoff. Never reuse A2's failed ancestry or an old intent/lease. Keep this evidence branch separate. Issue #1006 is full; PR #1955 and this task record are the continuation anchors.
