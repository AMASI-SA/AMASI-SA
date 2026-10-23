# Fulfillment mobile release rollback

Production has not been changed by this task. No database migration or operational data writes are included.

## Isolated checkpoints

- Design checkpoint: daa8345c8f15146cbba2a624036998c1ffc6b272
- Dependency-only checkpoint: 22957b5e2c14e92bc88a17b87069630709196f9e
- Design base: 571e1704fc823c681c61b2f31de1db4f5ea5f55a

Dependencies affect only backend/requirements.txt, scripts/release_backend_requirements.lock, frontend/package.json and frontend/yarn.lock. Reverse application of their exact diff passed git apply --reverse --check on 2026-10-23. This verifies source reversibility, not a deployed rollback rehearsal.

## Selective rollback

From the current production head, create a separate rollback branch and run git revert 22957b5e2c14e92bc88a17b87069630709196f9e to reverse dependencies only. Review conflicts against subsequent work; never restore whole files blindly or reset production. Keep both Python requirements files and the frontend package/lock pair consistent. Reverting this commit restores the old transitive dependency versions as well.

To reverse the design, revert its isolated checkpoint separately after checking its ancestry in the deployed release. Preserve unrelated production changes.

Rebuild with the pinned toolchain and a fresh source A / intent-only B through AGENTS.md protocol v5. Run the security and fulfillment checks and three production verification probes. Older dependencies carry known security advisories; a failed security gate must not be bypassed. Selective source rollback is therefore not guaranteed to be eligible for redeployment.

## Operational recovery before publication

Record the actual currently deployed source SHA, deployment SHA and retained successful platform deployment ID immediately before publishing. Confirm that the platform supports restoring that complete retained deployment, including backend and frontend. Do not assume branch base is the live deployment. If platform rollback is unavailable, record that limitation before publishing and prepare a governed corrective release.

No live rollback or successful deployment is claimed yet. Phone visual acceptance, backend dependency regression and governed build remain pending.
