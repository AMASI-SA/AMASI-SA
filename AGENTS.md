# Production release protocol

These rules apply to every agent and every conversation that can deploy this
repository to the shared Emergent production project.

## Single source of truth

- Production branch: `hotfix/prod-snap-meta-final`.
- GitHub `origin/hotfix/prod-snap-meta-final` is the only source of truth.
- Emergent publishes the current `/app` workspace; it does not implicitly pull
  GitHub. Never infer that clicking **Re-publish** includes recent commits.

## Mandatory release gate

The guard uses release protocol v4. Before updating `/app` or starting the
potentially long frontend install and build, inspect the current lease:

```bash
cd /app
python scripts/production_release_guard.py status
```

Continue only when it reports `"active": false`. A lease prepared by an older
guard must be closed with that original guard before updating the workspace;
never reuse a v1/v2/v3 lease or identity with v4.

After fast-forwarding `/app` to the intended Production SHA, install the
repository-owned local toolchain, perform the frozen install and governed
build, and validate the retained artifact before creating a lease:

```bash
python scripts/frontend_release_toolchain.py ensure

python scripts/frontend_release_toolchain.py exec -- \
  bash -lc 'cd frontend && yarn install --frozen-lockfile --non-interactive'

python scripts/frontend_release_toolchain.py exec -- \
  bash -lc 'cd frontend && yarn build:release'

python scripts/frontend_release_toolchain.py exec -- \
  python scripts/verify_frontend_build.py \
    --expected-git-sha "$(git rev-parse HEAD)"
```

Only after all four commands succeed, run:

```bash
python scripts/production_release_guard.py prepare --actor "<conversation>"
```

Do not hold a release lease during toolchain bootstrap, dependency install, or
the reproducibility build. `prepare` must exit successfully. It enforces all
of the following:

1. only one production release lease exists across conversations;
2. the checked-out branch is the production branch;
3. tracked files are clean;
4. local `HEAD` exactly matches `origin/hotfix/prod-snap-meta-final` after a
   fresh fetch;
5. the exact Git SHA and governed frontend artifact are embedded in the
   package for post-deploy proof.

Immediately before clicking **Re-publish changes**, run this second gate. It
refetches GitHub and refuses if another conversation pushed or changed files
after preparation:

```bash
python scripts/production_release_guard.py prepublish
```

If a lease already exists, do not start another deployment and do not clear
another conversation's lease. Inspect it with:

```bash
python scripts/production_release_guard.py status
```

## Completion proof

`Deployment Started`, `Publishing...`, an enabled publish button, or a new
Emergent publish number is not success. Completion requires both:

1. Emergent visibly reports a newer `Deployment Succeeded`; and
2. this command succeeds three consecutive times internally and reports the
   exact prepared release identity and critical-file hashes above. Requests
   may reach different healthy replicas, so boot timestamps may differ, but
   every timestamp must be timezone-aware and within the five-minute clock
   skew allowed around preparation and verification:

```bash
python scripts/production_release_guard.py verify --url https://mezansalla.com
```

Do not perform any production financial write, advertising write, bulk send,
or other irreversible action until both checks pass. A failed deployment can
be released explicitly only by the owner of the exact prepared SHA and release
ID shown by `prepare` or `status`:

```bash
python scripts/production_release_guard.py abort \
  --expected-sha <full-sha> \
  --expected-release-id <release-uuid>
```

Never use `git reset --hard` to satisfy this protocol. Preserve unrelated or
untracked Emergent files.
