# Mezan production release protocol

Production is a shared Emergent workspace. Multiple conversations can safely
prepare code, but only one may own an active production release.

The current guard and embedded identity use protocol v4. A v4 release binds
the backend SHA and critical files to the exact built frontend `index.html`,
every meaningful public build file (including future service workers), the
complete Git HEAD `frontend/**` source tree, the governed client environment,
the dependency lock, and the exact Node/Yarn toolchain. Before updating `/app`,
run `python scripts/production_release_guard.py status` and continue only when
it reports `"active": false`. A lease prepared by an older guard must be
closed by its owner with that original guard before `/app` is updated. Never
reuse a v1/v2/v3 lease or identity with the v4 guard.

## Prepare

After all intended commits are on `origin/hotfix/prod-snap-meta-final`, update
`/app` with a fast-forward pull. Then create a clean frontend build using the
repository's governed toolchain and frozen dependency graph:

```bash
cd /app

python scripts/frontend_release_toolchain.py ensure

python scripts/frontend_release_toolchain.py exec -- \
  bash -lc 'cd frontend && yarn install --frozen-lockfile --non-interactive'

python scripts/frontend_release_toolchain.py exec -- \
  bash -lc 'cd frontend && yarn build:release'

python scripts/frontend_release_toolchain.py exec -- \
  python scripts/verify_frontend_build.py \
    --expected-git-sha "$(git rev-parse HEAD)"
```

The bootstrap installs Node v22.23.2 and activates Yarn 1.22.22 only inside
the user-owned release-toolchain cache. It does not replace the system Node or
Yarn, modify shell profiles, or persistently change `PATH`. The potentially
long bootstrap, frozen install, two-pass build, and artifact verification must
all finish before `prepare`; do not create or hold a release lease during
these steps.

The cache lives under
`${XDG_CACHE_HOME:-$HOME/.cache}/mezan-release-toolchains/`. The bootstrap
accepts only the exact official Node v22.23.2 Linux archives pinned in the
repository: `linux-x64` SHA256
`d60acfe00a2932254bb0ad20e01b0d74397a0875595de719654b214f4b03f307`
and `linux-arm64` SHA256
`fff4078c5def658577f92c88db7db3bc0072924bfb93fe52c1e744a54e94abb8`.
Unsupported platforms, checksum/version drift, or corrupt cache state fail
closed before a release command runs; corrupt cache state is replaced through
a verified atomic installation.

`yarn build:release` is the governed release build. It removes only the previous governed proof,
performs two clean builds (A, then B), requires the two complete
`build-meta.json` files to be byte-identical, and leaves B in
`frontend/build`. Each pass proves that every tracked file below `frontend/`
matches the exact Git HEAD tree before and after Vite. Any tracked, untracked
non-ignored, mode, blob, or mid-build drift fails closed. A mismatch or either
failed pass removes the proof, so `prepare` and `prepublish` refuse the release.
The successful build atomically writes the ignored, path-specific
`frontend/.release/reproducible-build.json`; its normalized content and its own
SHA256/byte count are bound into the release lease. Do not copy a proof from
another checkout or build.

`yarn build` remains the ordinary single-pass Vite build for feature workflows
and local development. It deliberately does not create a reproducibility proof,
so a normal build cannot be prepared or published by Release Guard. The five
explicitly governed production/public-artifact workflows and the manual
production path use `yarn build:release` with exact Node 22.23.2, Yarn 1.22.22,
and the frozen lockfile. The package engine range keeps other Node 22 feature
workflows compatible; the release wrapper and proof still enforce the exact
release toolchain fail-closed.

Each retained B writes deterministic
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

The Mezan Release Readiness workflow runs this same two-pass release build,
validates the external reproducibility proof against retained B, and validates
the full public artifact tree. The runtime health endpoint never depends on
`.git` or the ignored proof file: it revalidates the packaged `build-meta.json`
and artifact bytes and exposes the proof copy embedded by the guard.

Only after the bootstrap, frozen install, governed build, and explicit
`verify_frontend_build.py` check all succeed, run:

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

`prepublish` re-reads the Git/source/artifact/proof pair and leaves the lease
active on every mismatch. The current Emergent flow still requires a separate
manual button after this command; the guard cannot lock or content-address that
external button's workspace. Therefore an A→B→A mutation, or any mutation
after the final `prepublish` read and before the platform snapshots the
workspace, is an explicit residual TOCTOU limitation. Keep this release Draft
until reviewed, publish immediately after a successful check, and treat
post-deployment `verify` as the mandatory closure. Do not claim `prepublish`
alone proves what Emergent deployed.

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
the governed no-cache/no-store/must-revalidate policy. Both standard
service-worker paths (`/sw.js` and `/service-worker.js`) are required public
files with identical, exact retirement-worker bytes. The workers install,
claim clients when possible, unregister themselves, install no `fetch` handler,
and do not delete origin-wide caches. Both canonical and cache-busted probes
must return the exact bytes with JavaScript MIME,
`no-cache, no-store, must-revalidate, max-age=0`, zero/absent `Age`, and no
unsafe Cloudflare cache state. The probes include the `Service-Worker: script`
request header. This proves the retirement payload is publicly available; a
previously visited browser completes retirement only when it next updates the
registration. Verification is pinned
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
