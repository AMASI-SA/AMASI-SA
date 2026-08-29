# Mezan production release protocol

Production is a shared Emergent workspace. Multiple conversations can safely
prepare code, but only one may own an active production release.

The current guard and embedded identity use protocol v3. A v3 release binds
the backend SHA and critical files to the exact built frontend `index.html`,
every public `assets/**` byte, the dependency lock, and the observed Node/Yarn
toolchain. A lease prepared by an older guard must be aborted by its owner
with that original guard before `/app` is updated. Never reuse a v1/v2 lease
or identity with the v3 guard.

## Prepare

After all intended commits are on `origin/hotfix/prod-snap-meta-final`, update
`/app` with a fast-forward pull. Then create a clean frontend build using the
repository's governed toolchain and frozen dependency graph:

```bash
cd /app/frontend
corepack enable
corepack prepare yarn@1.22.22 --activate
yarn install --frozen-lockfile --non-interactive
rm -rf build node_modules/.vite
yarn build
```

`yarn build` writes deterministic `frontend/build/build-meta.json` without a
wall-clock timestamp. It records the exact Node 22.x patch observed at build
time, Yarn 1.22.22, the source/lock hashes, `index.html`, and every build file.
The major Node contract is 22.x because no authoritative production patch has
yet been documented; the exact observed patch remains auditable per artifact.

Only after that build succeeds, run:

```bash
cd /app
python scripts/production_release_guard.py prepare --actor "conversation-name"
```

Do not publish if this refuses. The generated
`backend/release_identity.json` is intentionally untracked and is packaged
with the exact workspace being deployed.

Run the final race check immediately before using Emergent:

```bash
python scripts/production_release_guard.py prepublish
```

## Publish and verify

Use Emergent's **Re-publish changes** once. Wait for a newer explicit
`Deployment Succeeded`, then run:

```bash
cd /app
python scripts/production_release_guard.py verify --url https://mezansalla.com
```

Only `"verified": true` after three consecutive checks proves that production
restarted, is healthy, is running the prepared Git SHA and critical files,
and publicly serves the exact prepared `build-meta.json`, `index.html`, and
all `assets/**` bytes. Until then, no financial or other irreversible
production action is allowed. Any frontend mismatch leaves the lease active.

## Failed release

Inspect the owner and SHA:

```bash
python scripts/production_release_guard.py status
```

The owner may release a failed lease only by providing both the exact full SHA
and release ID returned by `prepare` or `status`:

```bash
python scripts/production_release_guard.py abort \
  --expected-sha <full-sha> \
  --expected-release-id <release-uuid>
```

Never clear the lease merely because the publish button became enabled.
