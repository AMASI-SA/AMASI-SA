# Mezan production release protocol v5

Protocol v5 adapts the governed release to Emergent Cloud Build. The platform
rebuilds from tracked source and does not transfer generated, ignored files
from a local `/app` session. Consequently, the Cloud Build invoked by Emergent
must create the Frontend artifact, reproducibility proof, and Backend runtime
identity which enter the deployment packages.

Release Guard v4 is not compatible with this boundary and must not be
published again.

## Evidence status

The following facts are proven by the currently available Emergent runtime
logs:

| Fact | Proven value |
| --- | --- |
| Runtime workspace | Shared `/app` |
| Backend runtime cwd | `/app/backend` |
| Frontend runtime cwd | `/app/frontend` |
| Failed-v4 Frontend runtime command | `yarn start`, whose then-deployed package script mapped to Vite development serving |
| Host Node | v20.20.2 |
| Host Yarn | v1.22.22 |
| Log surface currently exposed by the UI | Runtime logs only |

The current logs do **not** establish the outer Cloud Build install command,
the Cloud Build cwd, whether `.git` exists during the build, the snapshot
order, the actual Backend and Frontend package roots, or whether Frontend build
files can be copied into the Backend package. Runtime cwd is not evidence of
build cwd. The observed Vite development server also does not serve the
governed `frontend/build` contract: v5 requires a governed runtime server which
resolves `/build-meta.json` before SPA fallback. Creating the file or adding a
headers rule alone cannot prove correct routing. The v5 adapter emits sanitized
evidence and its exact governed commands into the next Cloud Build log; see
[`EMERGENT_CLOUD_BUILD_EVIDENCE.md`](EMERGENT_CLOUD_BUILD_EVIDENCE.md).

The failed v4 deployments establish a separate negative fact: tracked Backend
protocol code arrived, but locally generated ignored files did not. In those
deployments `backend/release_identity.json`, `frontend/build/build-meta.json`,
and `frontend/.release/reproducible-build.json` were absent, and public
`/build-meta.json` resolved to the SPA shell instead of JSON. Protocol v5 never
uses those local outputs as inputs.

## Protocol entities

| Entity | Lifetime and purpose |
| --- | --- |
| Source commit A | Reviewed commit containing all governed source changes |
| `release/release-intent-v5.json` | Tracked, reviewed handoff binding A to the expected deterministic build and runtime identity |
| Intent/deployment commit B | Commit which changes only the intent file after A |
| Runtime identity | Deterministic, package-local identity generated in Local rehearsal and Cloud Build |
| Operational lease | Mutable local coordination record containing owner/times and a separate deployment commit SHA |
| Generated build/proof/identity | Ignored outputs recreated from tracked source and intent inside each build workspace |

The runtime identity and operational lease are deliberately different. The
runtime identity contains no actor, wall-clock time, UUID, or lease state. The
lease may record those operational fields without changing the deployed
release ID.

## Non-self-referential source and intent commits

A tracked intent cannot contain the SHA of the commit which contains that same
intent: changing the file changes the commit SHA. Protocol v5 therefore uses
two commits.

1. Commit **A** contains the complete reviewed source.
2. Build A twice with the governed toolchain and freeze its deterministic
   result into `release/release-intent-v5.json`.
3. Commit **B** adds or updates only `release/release-intent-v5.json`.
4. The intent and runtime identity retain `source_git_sha=A`.
5. The operational guard records B, or a tree-equivalent merge commit, as
   `deployment_git_sha`.

When `.git` is present, the guard requires A to be an ancestor of the
deployment commit and rejects any governed source change after A. The only
tracked content change allowed in `A..deployment_git_sha` is
`release/release-intent-v5.json`; merge-parent metadata may differ but may not
introduce additional content. When `.git` is absent, the adapter validates the
complete Frontend source membership, modes, Git blob IDs, byte counts and
SHA256 values from the intent, plus the exact critical Backend hashes. It does
not infer ancestry without Git; the adapter logs that limitation and fails if
any source byte inside the intent's governed scope drifts.

A squash merge, rebase, amend, or conflict resolution after freezing changes
the commit/tree relation and invalidates the handoff. Preserve A and the
intent-only B in merge history. If history or governed bytes change, create a
new source commit A, rebuild, and freeze a new B; never edit the embedded SHA or
hashes by hand.

The source SHA exposed by the v5 identity is A. The legacy health field
`git_sha` is an alias for `source_git_sha`; it is not the intent/deployment
commit. Operational diagnostics must use the separately named
`deployment_git_sha` when referring to B.

## Freezing the reviewed intent

Start from clean source commit A. The bootstrap switch is permitted only while
creating the reviewed intent, because an intent does not exist yet for A:

```bash
export REACT_APP_BACKEND_URL=https://mezansalla.com

python scripts/frontend_release_toolchain.py ensure

python scripts/frontend_release_toolchain.py exec -- \
  yarn --cwd frontend install --frozen-lockfile --non-interactive

MEZAN_RELEASE_BOOTSTRAP_INTENT=1 \
python scripts/frontend_release_toolchain.py exec -- \
  yarn --cwd frontend build:release

python scripts/frontend_release_toolchain.py exec -- \
  python scripts/verify_frontend_build.py \
    --expected-git-sha "$(git rev-parse HEAD)"

python scripts/emergent_deployment_adapter.py freeze-intent \
  --source-git-sha "$(git rev-parse HEAD)" \
  --branch hotfix/prod-snap-meta-final
```

Review the complete intent, then create B with only that tracked file changed.
Do not leave the bootstrap variable set for a rehearsal or Cloud Build. Its
only accepted enabled value is exactly `1`; all other non-empty values fail.

The intent binds:

- full lowercase source commit A SHA;
- the complete tracked Frontend source manifest and its canonical tree hashes;
- exact Node/Yarn versions and governed client environment proof;
- every meaningful Frontend build file, `index.html`, `build-meta.json`, and
  the complete artifact tree digest;
- the normalized reproducibility proof and proof-file digest;
- critical Backend package hashes;
- the complete deterministic runtime identity.

The release ID is `rg5-` followed by the SHA256 of canonical compact JSON for
the identity core. The same source, critical bytes, artifact, metadata, and
proof therefore produce the same identity locally and in Cloud Build.

`REACT_APP_BACKEND_URL` is public browser configuration, not a secret. Its
reviewed v5 value is `https://mezansalla.com`; both the bootstrap build and the
Cloud adapter bind its SHA256 into the Frontend evidence. An absent or changed
value produces a different build contract and is rejected.

## Emergent deployment adapter

The repository configures this Emergent-facing Frontend package contract:

```bash
cd frontend
yarn build
```

The retained UI logs do not expose the historic Cloud Build argv/cwd, so the
next Cloud log must confirm that the platform actually invokes this configured
entry point. The package script starts `frontend/scripts/build-entry.cjs`.
Outside GitHub Actions the dispatcher always starts
`scripts/emergent_deployment_adapter.py`; inside GitHub Actions it allows the
ordinary current-HEAD Vite build only after `GITHUB_ACTIONS`, `GITHUB_SHA`,
`GITHUB_WORKSPACE`, and the actual checkout agree. Partial markers fail closed.
Host Node 20 is allowed only to invoke the package script; it is not the
governed build toolchain. Before
reading the intent, the adapter removes inherited
`frontend/node_modules`, `frontend/build`,
`frontend/.release/reproducible-build.json`, temporary proof files, and
`backend/release_identity.json`.

Protocol v5 also maps the observed runtime entry point `yarn start` to a
governed server which validates `frontend/build/index.html` and
`frontend/build/build-meta.json` before serving the retained build. Local CI
starts that exact entry point and compares the HTTP metadata response with the
packaged file. This proves the repository runtime contract, not that Production
has already adopted it. The internal Vite static-server subcommand is not an
Emergent Preview environment action; this PR and CI never open or deploy
Emergent Preview.

The adapter then uses `scripts/frontend_release_toolchain.py` to execute these
commands exactly:

```bash
python scripts/frontend_release_toolchain.py ensure

python scripts/frontend_release_toolchain.py exec -- \
  yarn --cwd frontend install --frozen-lockfile --non-interactive

python scripts/frontend_release_toolchain.py exec -- \
  yarn --cwd frontend build:release

python scripts/frontend_release_toolchain.py exec -- \
  python scripts/verify_frontend_build.py \
    --expected-git-sha "<release-intent-source-git-sha>" \
    --reviewed-intent-v5
```

The repository toolchain pins Node v22.23.2 and Yarn v1.22.22. It verifies the
official Node archive checksum before installation and changes neither the
system toolchain nor shell profiles. The frozen install performed by the
adapter is the dependency graph used by the governed build. Any outer install
which Emergent may perform is not trusted, and its exact command remains
unobserved until a complete Cloud Build log is available.

The pinned Node archive SHA256 values are
`d60acfe00a2932254bb0ad20e01b0d74397a0875595de719654b214f4b03f307`
for `linux-x64` and
`fff4078c5def658577f92c88db7db3bc0072924bfb93fe52c1e744a54e94abb8`
for `linux-arm64`. Unsupported platforms, checksum/version drift, or corrupt
cache state fail closed before the governed command runs.

`build:release` performs clean build A and clean build B. Each pass validates
the full reviewed Frontend source before and after Vite; both complete metadata
documents and artifact trees must match. Failure removes the proof and all
partial release output. Success leaves deterministic build B in
`frontend/build` and atomically writes
`frontend/.release/reproducible-build.json`.

Vite receives an explicit minimal child environment with
`NODE_ENV=production`. The wrapper strips parent `NODE_ENV`, `NODE_OPTIONS`,
all `VITE_*` values, and unrelated secrets; only the allowlisted public client
configuration may be represented, by presence and SHA256 rather than a secret
value. `.env*` loading and implicit Vite-prefixed exposure remain disabled.

Only after the retained artifact and proof exactly equal the reviewed intent
does the adapter atomically write `backend/release_identity.json`. These three
paths are intentionally ignored:

```text
backend/release_identity.json
frontend/build/
frontend/.release/reproducible-build.json
```

They must exist in the build/package workspace after the adapter succeeds, but
must remain absent from Git. A local `/app` copy is never consulted or copied.

## Runtime identity

The Backend runtime identity embeds everything needed to verify itself inside
an isolated Backend package:

- `source_git_sha` and its backward-compatible `git_sha` health alias;
- deterministic `rg5-…` release ID;
- critical Backend file hashes;
- exact Frontend build identity, artifact tree and build-meta record;
- exact normalized Frontend reproducibility proof.

Backend health validation does not read `.git`, `frontend/build`, or the
ignored proof from a sibling workspace. It hashes allowlisted critical files
inside its own package and validates the canonical embedded identity. A valid
package reports:

```text
verified_identity_available=true
critical_file_hashes_match=true
frontend_build_verified=true
```

Here `frontend_build_verified` means that the deterministic Frontend build and
A/B proof embedded in the Backend identity are internally valid and bound to
the release ID. It is not, by itself, proof that the separate public Frontend
runtime received those bytes. Protocol-v5 acceptance therefore also requires
the guard's exact public byte and MIME verification; health alone is
insufficient.

This design does not require Frontend files to cross the Backend package
boundary. The build stage must, however, have both source roots available long
enough to materialize the Backend identity before the platform separates or
snapshots packages. Whether Emergent snapshots before or after that point is
not yet an observed fact and must be captured from the next Cloud Build.

## Package-boundary proof

After materialization, the adapter copies each declared runtime root into a
separate temporary package and verifies those isolated copies, not merely the
shared source workspace.

The Frontend candidate package must contain exact records for:

- `build/index.html`;
- `build/build-meta.json` as a JSON object distinct from the SPA shell;
- every governed public file and the complete artifact tree.
- the reviewed runtime entry files (`package.json`, `yarn.lock`,
  `vite.config.js`, and `scripts/start-governed-runtime.cjs`).

The isolated candidate executes the runtime artifact validator without
`node_modules`; the separate clean-clone HTTP check runs the exact `yarn start`
entry point after the pinned dependency installation.

The Backend candidate package must contain:

- `release_identity.json`;
- every critical Backend file with the bound SHA256;
- enough v5 validation code for an isolated import to return the exact release
  ID with `verified_identity_available=true`.

The proof records candidate runtime roots, file counts, package tree digests,
and exact records for required identity and metadata members; it is not a full
file listing. It also confirms that the isolated Backend package has neither
`.git` nor a sibling Frontend directory. The expected HTTP content type for the
metadata is `application/json`.

This is an adapter-time candidate package-membership proof. It must not be
described as a direct observation of Emergent's package snapshot or as proof
that verification ran before the snapshot. Actual platform handoff is accepted
only after Cloud Build evidence and production byte/MIME probes agree with it.

## Clean-clone rehearsal

The release workflow creates a fresh clone which initially contains none of:

```text
backend/release_identity.json
frontend/build/
frontend/.release/reproducible-build.json
```

It selects host Node v20.20.2 and host Yarn v1.22.22, then invokes the Frontend
package build entry point configured for Emergent. The retained UI does not
prove the historic Cloud Build invocation; the next adapter log must confirm
it. A passing rehearsal proves that the repository contract provisions Node
v22.23.2/Yarn v1.22.22, performs the frozen install and A/B build, validates the
proof, materializes identity, and passes its isolated package-boundary check.

The job then independently requires:

- non-empty `frontend/build/index.html`;
- valid JSON at `frontend/build/build-meta.json`;
- a valid matching reproducibility proof;
- `backend/release_identity.json` equal to the intent identity;
- matching source SHA, artifact tree, build metadata, critical hashes, and
  deterministic release ID;
- verified Backend identity from its package-local reader;
- a clean tracked worktree after all ignored outputs are generated.

CI exercises these checks but never creates a release lease and never deploys.
The JSON file check establishes content, not Emergent HTTP routing; MIME and SPA
fallback behavior remain mandatory post-deploy probes.

## Prepare, publish, and verify

Before a separately authorized production attempt, inspect the current lease:

```bash
cd /app
python scripts/production_release_guard.py status
```

Continue only when `active` is `false`. Never reuse a v1-v4 identity or lease.
Do not hold a lease while bootstrapping, installing, building, or running the
clean-clone rehearsal. After the v5 rehearsal matches the reviewed intent, the
owner may run:

```bash
python scripts/production_release_guard.py prepare --actor "<conversation>"
python scripts/production_release_guard.py prepublish
```

`prepare` binds the operational lease to the deterministic runtime identity,
source SHA, and separate deployment SHA. `prepublish` repeats the local,
remote, ancestry, intent, artifact, proof, identity, and critical-byte checks.
Neither command proves what Emergent later snapshots.

After one separately authorized **Re-publish changes**, wait for a newer
explicit `Deployment Succeeded`, then run:

```bash
python scripts/production_release_guard.py verify \
  --url https://mezansalla.com
```

Completion requires three consecutive checks of the exact intended identity:

```text
verified_identity_available=true
release_id=<expected rg5 identity>
source_git_sha=<expected source commit A>
git_sha=<same source commit A>
critical_file_hashes_match=true
frontend_build_verified=true
```

In both canonical and cache-busted requests, `/build-meta.json` must return
status 200, the exact governed bytes, valid JSON, and a content type beginning
with `application/json`. Returning `text/html` or the SPA shell is a hard
failure. All other governed public files, canonical HTML, cache headers and
service-worker retirement files remain subject to the existing exact-byte
checks.

Successful verification closes only the matching lease; `status` must then
report `"active": false`. Until all checks pass, no financial or irreversible
production action is permitted. A failed release may be aborted only by the
lease owner with the exact `deployment_git_sha` and deterministic release ID
required by the guard:

```bash
python scripts/production_release_guard.py abort \
  --expected-sha <deployment-git-sha> \
  --expected-release-id <rg5-release-id>
```

The protocol implementation PR itself must remain Draft and must not create a
lease, publish, use Preview, approve, execute, sync, backfill, or perform any
Budget/Bid/Target Cost or other production write.
