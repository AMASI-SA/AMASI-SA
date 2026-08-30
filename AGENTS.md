# Production release protocol

These rules apply to every agent and every conversation that can deploy this
repository to the shared Emergent production project.

## Protocol v5 only

Release Guard v4 is not compatible with the Emergent Cloud Build artifact
boundary. Do not publish a v4 release. Protocol v5 makes the governed release
inside Cloud Build; ignored output left in a local `/app` session is never a
deployment input.

The production branch remains `hotfix/prod-snap-meta-final`, and GitHub
`origin/hotfix/prod-snap-meta-final` remains the reviewed source of truth.
Before changing `/app` or starting a release rehearsal, run:

```bash
cd /app
python scripts/production_release_guard.py status
```

Continue only when it reports `"active": false`. A v1-v4 lease must be closed
by its owner using the guard which created it. Never reuse an older lease or
identity with protocol v5.

## What is known about Emergent

The failed-v4 runtime logs prove a shared `/app` workspace, Backend cwd
`/app/backend`, Frontend cwd `/app/frontend`, Frontend runtime command
`yarn start` whose then-deployed package script used Vite development serving,
host Node v20.20.2, and host Yarn v1.22.22. The current UI exposes runtime logs,
not a complete Cloud Build transcript. It has not yet proved the historic Cloud
Build install/build argv or cwd, presence of `.git`, package snapshot order,
runtime package roots, or whether Frontend output is copied into the Backend
package. Do not present any of those as observed fact.

Serving the generated build requires a governed runtime entry point for
`frontend/build`. It must return `/build-meta.json` before SPA fallback with
JSON bytes and MIME; materializing the file or adding headers alone is not
sufficient. Protocol v5 maps `yarn start` to the governed runtime entry point;
that new mapping remains subject to the clean-clone route test and later
Production verification.

The v5 adapter prints sanitized environment evidence and its exact governed
commands. Preserve that output from the next Cloud Build so the unknowns can
be resolved without logging environment values or secrets.

## Reviewed two-commit handoff

Protocol v5 uses a non-self-referential two-commit contract:

1. source commit **A** contains all governed source changes;
2. a local governed A/B build of A freezes the reviewed
   `release/release-intent-v5.json`;
3. intent commit **B** adds or updates only that tracked intent file;
4. the intent's `source_git_sha` remains A, while the operational guard records
   B (or a tree-equivalent merge commit) separately as `deployment_git_sha`.

When Git metadata is available, A must be an ancestor of the deployment commit
and the content diff after A may change only the intent file. Merge ancestry
metadata may differ, but it may not introduce another governed source change.
This split avoids the impossible requirement for a tracked file to contain the
SHA of the commit which contains that same file. Do not squash or rebase A and
B after freezing the intent. If either SHA or tree changes, create a new source
commit, rebuild, and freeze a new intent-only commit.

The intent binds the full reviewed Frontend source manifest, deterministic
Frontend artifact tree and `build-meta.json`, reproducibility proof, critical
Backend hashes, source SHA, and deterministic runtime identity. The runtime
release ID has form `rg5-<sha256>` and is derived from canonical identity
content. It contains no UUID, actor, timestamp, or lease state. Operational
lease ownership and timestamps remain local coordination data and cannot
change the runtime identity.

## Cloud Build deployment adapter

The repository now configures this Emergent-facing Frontend package contract:

```bash
cd frontend
yarn build
```

`frontend/scripts/build-entry.cjs` is the only dispatcher. Outside GitHub
Actions it always enters the Emergent deployment adapter. Inside GitHub
Actions it permits the ordinary current-HEAD Vite build only after
`GITHUB_ACTIONS=true`, `GITHUB_SHA`, `GITHUB_WORKSPACE`, and the actual Git HEAD
all agree. Partial or mismatched GitHub markers fail closed. This keeps the
repository's existing CI consumers on the source under review without letting
Cloud Build bypass the tracked release intent.

The reviewed public client origin is
`REACT_APP_BACKEND_URL=https://mezansalla.com`; Local bootstrap and Cloud
materialization must use that exact value. The retained UI logs do not expose
the historic Cloud Build argv/cwd, so the next Cloud log must confirm this
configured entry point. Under the contract,
host Node 20 and host Yarn only dispatch the repository's Python deployment
adapter. They do not run Vite or build the governed release. The adapter uses
`scripts/frontend_release_toolchain.py` to run exactly:

```bash
python scripts/frontend_release_toolchain.py ensure

python scripts/frontend_release_toolchain.py exec -- \
  yarn --cwd frontend install --frozen-lockfile --non-interactive

python scripts/frontend_release_toolchain.py exec -- \
  yarn --cwd frontend build:release

python scripts/frontend_release_toolchain.py exec -- \
  python scripts/verify_frontend_build.py \
    --expected-git-sha "<intent-source-git-sha>" \
    --reviewed-intent-v5
```

Those commands use exactly Node v22.23.2 and Yarn v1.22.22. `build:release`
performs clean A/B builds and retains B only after the complete artifacts and
proof match. The adapter begins by removing inherited `frontend/node_modules`,
`frontend/build`, `frontend/.release/reproducible-build.json`, and
`backend/release_identity.json`; therefore no ignored or untracked local file
can satisfy the build.

After verification, the same adapter atomically materializes the ignored
outputs in the Cloud Build workspace:

- `frontend/build`, including `index.html` and valid `build-meta.json`;
- `frontend/.release/reproducible-build.json`;
- `backend/release_identity.json`.

These files must remain ignored and untracked. Do not force-add them. The
Backend identity embeds the verified Frontend artifact/proof contract and is
valid in an isolated Backend package without `.git` or a sibling Frontend
directory. Protocol v5 does not assume Frontend build files cross into the
Backend runtime package.

The adapter's package-boundary check validates isolated copies of the declared
Frontend and Backend package roots and emits file counts, tree digests, and
exact records for required members. It does not emit a complete package file
listing. This proves required candidate contents at adapter verification time;
it does not prove whether Emergent snapshots before or after that point. The
next Cloud log and the deployed probes remain required evidence of the actual
platform handoff.

## Lease and production verification

Do not create a lease during toolchain bootstrap, dependency installation, or
the clean-clone adapter rehearsal. Only after the v5 local rehearsal matches
the reviewed intent may the owner run `prepare`, followed immediately before a
publish by `prepublish`. `prepare` records the deterministic runtime identity
and the separate deployment commit; it does not generate a random runtime
release ID.

This repository change and its CI must not create a lease, click Preview,
publish, approve, execute, sync, backfill, or perform any financial,
advertising, budget, bid, or target-cost write.

After a separately authorized publish, success requires a newer explicit
`Deployment Succeeded` and:

```bash
python scripts/production_release_guard.py verify \
  --url https://mezansalla.com
```

All three consecutive probes must report the intended deterministic release
ID and source SHA, `verified_identity_available=true`,
`critical_file_hashes_match=true`, and `frontend_build_verified=true`.
`/build-meta.json` must return the exact governed JSON bytes with an
`application/json` content type in both canonical and cache-busted requests;
SPA HTML is a hard failure. Successful verification closes the matching lease
and `status` must then report `"active": false`.

Never use `git reset --hard` to satisfy this protocol. Preserve unrelated
Emergent files and never clear another conversation's lease.
