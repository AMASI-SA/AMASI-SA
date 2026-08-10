# Mezan GitHub Continuous Delivery

## Goal

Make GitHub the only source of code for routine Mezan releases. Normal releases must not require copying commands into the Emergent terminal.

## Production branch

`hotfix/prod-snap-meta-final`

This branch is currently the authoritative production code line and is intentionally kept separate from `main` until the historical branch divergence is resolved explicitly.

## Release flow

1. Create a feature/fix branch from the current production branch.
2. Open a pull request into `hotfix/prod-snap-meta-final`.
3. Let the domain-specific GitHub Actions run for the changed area.
4. The central `Mezan Production Release` workflow also verifies:
   - all backend Python sources compile on Python 3.11;
   - the frontend installs and builds successfully.
5. Merge only after CI is green.
6. Emergent Continuous Deployment watches `hotfix/prod-snap-meta-final` and redeploys the existing Mezan deployment after the merge.
7. GitHub then checks `https://mezansalla.com/health` and the public root URL for availability.

## One-time Emergent setup

The existing Mezan deployment must be connected to repository `AMASI-SA/AMASI-SA` with Continuous Deployment enabled for branch `hotfix/prod-snap-meta-final`.

Use **redeploy/update existing deployment**, never replace the deployment for routine releases. Production database, domain and existing production secrets must remain attached to the current deployment.

No deployment credentials, Salla credentials, ad-platform tokens, Qoyod credentials, database URLs, or other production secrets are stored in this workflow or committed to GitHub.

## Safety rules

- Do not deploy from `main` while it remains diverged from the production branch.
- Do not merge a feature PR into production before its applicable CI is green.
- Do not use Replace Deployment for routine code updates.
- Do not copy production secrets into repository files or workflow YAML.
- A failed post-release health check means the release is not considered healthy and must be investigated before additional production changes.

## Result

After the one-time Emergent Continuous Deployment connection is enabled, routine Mezan updates become:

`code change -> GitHub PR -> CI -> merge -> automatic Emergent redeploy -> production health check`
