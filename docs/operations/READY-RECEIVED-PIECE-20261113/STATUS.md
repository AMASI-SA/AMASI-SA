# READY-RECEIVED-PIECE-20261113 — checkpoint (RED tests only)

- Task: physical supplier-received pieces stuck behind disabled Ready in a mixed order.
- Synthetic acceptance target reproduces verified case: Salla in progress; Mezan workflow reviewed; physical piece ready_for_assembly/received; zero services pending; direct virtual cards eligible.
- Private production response was reviewed in chat. Do not commit identifiers, customer details, or raw response in GitHub.
- Base: `b64121c265d8d6e158df0d9fb88debc52c95317d` on `hotfix/prod-snap-meta-final`.
- Branch: `codex/ready-received-piece-20261113`.
- Changed at this checkpoint: six targeted synthetic real-Mongo integration regressions in `backend/tests/test_review_local_assembly.py`.
- Expected RED: physical Ready search and POST deny a completed supplier receipt while workflow stays reviewed. Other negative tests must keep supplier, services, hold and stock fences.
- RED CI is unverified at this checkpoint; subsequent commits must record actual outcome and fix.
- Local Codex files were not mounted or pushed; this branch independently recreates the reported reproduction, not a verbatim review of the unseen patch.
- No Production operations, Merge, release intent or publish.
- Next: run isolated Mongo acceptance, inspect actual failure causes, implement one consistent search+write policy with atomic reviewed→in_progress and idempotency, rerun focused and full regression CI.
