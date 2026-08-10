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

Therefore a GitHub merge does **not** deploy `mezansalla.com`.

## Production is the required acceptance target

The user does **not** require manual Preview review for routine releases. Production is the required user-facing target.

Emergent still requires the intended code to exist inside the **same existing project workspace/Preview environment** because `Re-deploy changes` publishes from that project state. Preview is therefore a technical staging/source state, not a mandatory user acceptance step.

For routine releases, agents should not stop and ask the user to inspect Preview unless Preview verification is materially needed because the change is risky, ambiguous, or production verification cannot safely detect failure.

## Required release flow

For every normal Mezan change:

1. Apply the intended change in the **existing Emergent project workspace/Preview state** without relying on terminal deployment commands.
2. Save/record the exact reviewed change in GitHub on a controlled feature/fix branch and run the applicable CI.
3. Open/validate a PR into `hotfix/prod-snap-meta-final` and merge only when CI is green and GitHub matches the project change.
4. In the **same existing Emergent project**, open **Manage Publishing / Redeploy**.
5. Use **Overview → Re-deploy changes**.
6. Verify `mezansalla.com` after redeploy.

Manual visual Preview verification is optional for normal low-risk changes. It remains available as a safety gate when needed, but it is not the user's required destination.

The user accepts and expects the required **Re-deploy changes** click. The goal is to eliminate routine terminal commands, not to bypass Emergent's supported in-project redeploy step.

## Production safety

- Use in-place **Re-deploy changes** for routine releases.
- Do **not** use Replace Deployment for routine changes.
- Keep the existing production database, custom domain, and unchanged production secrets attached to the same deployment.
- Never copy production secrets into GitHub, chat, terminal instructions, workflow YAML, or committed files.
- Do not claim a GitHub merge is deployed until the Emergent redeploy and production verification have completed.
- Do not push an unknown/stale Emergent workspace directly over the production branch.

## Roles of the systems

- **Existing Emergent project workspace/Preview state**: technical source that `Re-deploy changes` can publish for this project.
- **GitHub**: review, CI, audit history, controlled production code reference, and rollback reference.
- **Emergent Production (`mezansalla.com`)**: required acceptance target; updated by the supported in-project redeploy flow.

## Release documentation

Read `docs/MEZAN_GITHUB_CD.md` for the detailed release contract and `.github/workflows/mezan-production-release.yml` for release-readiness CI behavior.

Any future agent that discovers a newly supported official Emergent GitHub pull/deploy API may propose changing this contract, but must not assume such automation exists without verifying it first.
