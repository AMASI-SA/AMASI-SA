# Mezan release flow with GitHub and Emergent

## Confirmed platform constraint

For the existing Mezan project, Emergent does **not** provide GitHub-to-project Continuous Deployment.

There is no supported GitHub pull/sync, branch watcher, webhook, or automatic redeploy path for this existing project. `Save to GitHub` is one-way from Emergent to GitHub. A push or merge to GitHub does not update Emergent Preview and does not redeploy `mezansalla.com`.

## Production branch

`hotfix/prod-snap-meta-final`

This remains the authoritative reviewed production code line in GitHub while `main` remains historically diverged.

## Roles of each system

- **Emergent Preview**: deployment source for this existing project.
- **GitHub**: code review, CI, audit trail, release history, and rollback reference.
- **Existing Emergent Production deployment**: must be redeployed in place; do not replace it for routine releases.

## Supported no-terminal release flow

1. Apply the intended code change in the **existing Emergent Preview environment**.
2. Verify the affected feature in Preview.
3. Save the reviewed Preview change to GitHub on a controlled feature/fix branch.
4. Open a PR into `hotfix/prod-snap-meta-final`.
5. Run the applicable domain-specific CI plus the central `Mezan Release Readiness` checks.
6. Merge only when CI is green and the merged code matches the verified Preview change.
7. In the same existing Emergent project open **Manage Publishing / Redeploy**.
8. Use **Overview → Re-deploy changes**.
9. Verify `mezansalla.com` after the redeploy.

This removes the need to copy deployment commands into the terminal. The remaining platform-required action is the in-project redeploy click because Emergent does not expose a GitHub-triggered redeploy for this project.

## GitHub-first changes

If a change is authored in GitHub first, it is **not deployable by merge alone**. Before production, the exact reviewed change must still be applied to the existing Emergent Preview environment and verified there. Do not assume GitHub and Preview are synchronized.

## Safety rules

- Do not treat a GitHub merge as a production deployment.
- Do not use `Save to GitHub` to push an unknown/stale Preview state over the production branch.
- Do not deploy from `main` while it remains diverged from the production branch.
- Do not use Replace Deployment for routine releases.
- Do not move or recreate the production database for routine releases.
- Keep existing production domain and unchanged secrets attached to the same deployment.
- Never commit Salla, Qoyod, ad-platform, database, or other production secrets to GitHub.
- A release is complete only after Preview verification, in-place redeploy, and production verification.

## CI behavior

The central `.github/workflows/mezan-production-release.yml` workflow verifies backend compilation and frontend production build. On a push to the production branch it records an explicit handoff notice that **manual Emergent redeploy is still required**. It intentionally does not claim that Production was updated merely because GitHub CI passed.

## Target operating model

For the current Emergent project:

`Emergent Preview change -> Preview verification -> Save to GitHub -> PR/CI -> merge -> in-place Re-deploy changes -> production verification`

If Emergent later exposes a supported GitHub pull/sync or deploy API for existing projects, this contract can be revisited and automated further.
