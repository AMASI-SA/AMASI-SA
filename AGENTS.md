# Mezan Agent Operating Contract

This file is binding context for any agent, parallel conversation, or future development session working on the Mezan production code line.

## Production code line

- Repository: `AMASI-SA/AMASI-SA`
- Current production branch: `hotfix/prod-snap-meta-final`
- Do not assume `main` is deployable while the historical branch divergence remains unresolved.

## Mandatory deployment rule: no terminal for routine releases

Routine Mezan development and publishing must **not depend on copying or running deployment commands in the Emergent terminal**.

Emergent support confirmed the existing Mezan project has **no GitHub-to-Emergent Continuous Deployment**:

- no Pull from GitHub;
- no branch synchronization;
- no GitHub webhook that updates Preview;
- no automatic redeploy after a GitHub push or merge;
- `Save to GitHub` is one-way from Emergent to GitHub.

Therefore a GitHub merge does **not** update Emergent Preview and does **not** deploy `mezansalla.com`.

## Required release flow

For every normal Mezan change:

1. Apply the intended change in the **existing Emergent Preview environment**.
2. Verify the affected behavior in Preview.
3. Save the exact reviewed Preview change to GitHub on a controlled feature/fix branch.
4. Open/validate a PR into `hotfix/prod-snap-meta-final` and run the applicable CI.
5. Merge only when CI is green and GitHub matches the verified Preview change.
6. In the **same existing Emergent project**, open **Manage Publishing / Redeploy**.
7. Use **Overview → Re-deploy changes**.
8. Verify `mezansalla.com` after redeploy.

The user accepts and expects the required **Re-deploy changes** click. The goal is to eliminate routine terminal commands, not to bypass Emergent's supported redeploy step.

## Production safety

- Use in-place **Re-deploy changes** for routine releases.
- Do **not** use Replace Deployment for routine changes.
- Keep the existing production database, custom domain, and unchanged production secrets attached to the same deployment.
- Never copy production secrets into GitHub, chat, terminal instructions, workflow YAML, or committed files.
- Do not claim a GitHub merge is deployed until the Emergent redeploy and production verification have completed.
- Do not push an unknown/stale Emergent workspace directly over the production branch.

## Roles of the systems

- **Emergent Preview**: source that can actually be redeployed for this existing project.
- **GitHub**: review, CI, audit history, controlled production code reference, and rollback reference.
- **Emergent Production**: updated only by the supported in-project redeploy flow.

## Release documentation

Read `docs/MEZAN_GITHUB_CD.md` for the detailed release contract and `.github/workflows/mezan-production-release.yml` for release-readiness CI behavior.

Any future agent that discovers a newly supported official Emergent GitHub pull/deploy API may propose changing this contract, but must not assume such automation exists without verifying it first.
