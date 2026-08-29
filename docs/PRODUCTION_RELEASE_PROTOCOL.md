# Mezan production release protocol

Production is a shared Emergent workspace. Multiple conversations can safely
prepare code, but only one may own an active production release.

The current guard and embedded identity use protocol v3. A v3 release binds
the backend SHA and critical files to the exact built frontend `index.html`,
every meaningful public build file (including future service workers), the
complete Git HEAD `frontend/**` source tree, the governed client environment,
the dependency lock, and the exact Node/Yarn toolchain. A lease prepared by an older guard must be aborted by its owner
with that original guard before `/app` is updated. Never reuse a v1/v2 lease
or identity with the v3 guard.

## Prepare

After all intended commits are on `origin/hotfix/prod-snap-meta-final`, update
`/app` with a fast-forward pull. Then create a clean frontend build using the
repository's governed toolchain and frozen dependency graph:

```bash
cd /app/frontend
nvm install 22.23.2
nvm use 22.23.2
corepack enable
corepack prepare yarn@1.22.22 --activate
yarn install --frozen-lockfile --non-interactive
rm -rf build node_modules/.vite
yarn build
```

`yarn build` first proves that every tracked file below `frontend/` matches the
exact Git HEAD tree, then repeats the source and governed-environment proof
after Vite finishes. Any tracked, untracked non-ignored, mode, blob, or
mid-build drift fails closed. It writes deterministic
`frontend/build/build-meta.json` without a wall-clock timestamp and records
Node 22.23.2, Yarn 1.22.22, the complete source-tree digest, hashed values for
the allowlisted public client environment only, `index.html`, and every build
file. The build wrapper starts Vite with an explicit minimal child environment,
forces `NODE_ENV=production`, strips parent `NODE_ENV`, `VITE_USER_NODE_ENV`,
every `VITE_*` value, `NODE_OPTIONS`, and unrelated secrets, and passes only
the allowlisted `REACT_APP_BACKEND_URL` value when present. `envDir: false` and
`envPrefix: []` also disable `.env*` loading and implicit client exposure. The
metadata stores only the allowlisted value's presence and SHA256, plus proof
that the effective build environment was production and contained no
`VITE_USER_NODE_ENV` or other `VITE_*` keys.

The Mezan Release Readiness workflow performs the clean build twice from the
same checkout, frozen lock, toolchain, and governed environment, then requires
the two complete `build-meta.json` files to be byte-identical.

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
and publicly serves the exact prepared `build-meta.json`, canonical `/`,
`/index.html`, the `/snapchat-accounts` SPA shell, and every meaningful public
build file in both normal and cache-busted requests. Canonical HTML must carry
the governed no-cache/no-store/must-revalidate policy. Standard service-worker
paths are also fenced: a registered worker must match the artifact with a safe
JavaScript MIME/cache policy, while an unregistered path must be a 404 or the
exact current HTML shell, never orphan JavaScript. These probes include the
`Service-Worker: script` request header and prove current server/CDN behavior;
they do not claim to evict an incumbent registration from a previously visited
browser. Any legacy client-registration retirement is a separate controlled
rollout. Verification is pinned
to `https://mezansalla.com`; another origin is refused. Until then, no
financial or other irreversible production action is allowed. Any frontend
mismatch leaves the lease active.

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
