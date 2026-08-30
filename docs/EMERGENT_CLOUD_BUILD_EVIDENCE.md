# Emergent Cloud Build evidence record

This document separates observations from assumptions for protocol v5. Update
it only with sanitized evidence from an identified Emergent build or runtime;
never include environment values, credentials, tokens, cookies, or URLs which
contain secrets.

## Currently proven

The currently available Emergent UI exposes runtime logs only. Those logs
prove:

| Observation | Value |
| --- | --- |
| Runtime workspace shared by services | `/app` |
| Backend runtime cwd | `/app/backend` |
| Frontend runtime cwd | `/app/frontend` |
| Failed-v4 Frontend runtime command | `yarn start`, whose then-deployed package script mapped to Vite development serving |
| Host Node | v20.20.2 |
| Host Yarn | v1.22.22 |

No complete historical Cloud Build transcript or package manifest is retained
in the repository or exposed by the current UI view. Consequently, exact
historic build-only values are unavailable and are recorded below as unknown,
not reconstructed from package configuration or runtime behavior.

They do not prove that the same cwd or workspace layout exists during Cloud
Build. The observed Vite development runtime also explains why materializing
`frontend/build/build-meta.json` alone cannot satisfy the public route: the v5
runtime entry point must serve the governed `frontend/build` tree and must
resolve `/build-meta.json` before any SPA fallback. A headers file alone is not
evidence that the correct bytes are routed.

The two failed protocol-v4 publishes also prove that tracked Backend changes
reached the rebuilt source while locally generated ignored release files did
not. In the resulting runtime:

```text
verified_identity_available=false
release_id=null
git_sha=null
critical_file_hashes_match=false
frontend_build_verified=false
```

`/build-meta.json` returned SPA HTML rather than JSON. These observations are
the reason v5 creates all governed output inside Cloud Build and serves the
resulting build tree explicitly.

## Not yet observed

Do not fill this table by inference from runtime logs or from repository
configuration.

| Question | Current status | Required evidence |
| --- | --- | --- |
| Outer Frontend install command | Unknown | Complete Cloud Build command log |
| Outer Frontend build command | Configured as `yarn build`; execution not yet captured | Cloud log line from the next build |
| Cloud Build working directory | Unknown | Adapter evidence record plus platform log |
| Cloud Build host Node/Yarn | Runtime reports v20.20.2/1.22.22; build not independently observed | Adapter evidence record |
| `.git` present during build | Unknown | Adapter `git_directory_present` field |
| Backend and Frontend share a build workspace | Unknown | Platform packaging log or manifest; co-parented source roots alone are not proof |
| Frontend runtime snapshot root | Unknown | Platform packaging log or manifest |
| Backend runtime snapshot root | Unknown | Platform packaging log or manifest |
| Snapshot occurs after adapter materialization | Unknown | Ordered log/manifest showing generated files before snapshot |
| Frontend build files enter Backend package | Unknown and not required by v5 | Actual Backend package manifest or isolated deployed evidence |

The adapter's declared candidate package roots are `frontend` and `backend`;
the governed static root inside the Frontend candidate is `frontend/build`.
Those are contract inputs to its package-boundary verifier, not observations
of the platform's actual snapshot configuration.

## Expected adapter log records

At startup, `scripts/emergent_deployment_adapter.py build` emits a sanitized
JSON object with:

- resolved build cwd and repository, Frontend, and Backend roots;
- whether the declared source roots are co-parented, explicitly without claiming the platform snapshot is shared;
- whether `.git` is present;
- host Node, Yarn, and Python versions;
- operating system and architecture;
- the configured Emergent-facing package build entry point, explicitly marked
  as not yet observed from a retained Cloud transcript;
- the governed frozen-install command.

The adapter then invokes and logs the repository-owned toolchain commands. The
governed build must use exactly:

```text
Node v22.23.2
Yarn v1.22.22
yarn install --frozen-lockfile --non-interactive
yarn build:release
```

Host Node 20 is a dispatcher only. The adapter removes inherited dependencies,
build, proof, and identity before those commands, so the record cannot be
satisfied by ignored files from a local `/app` session.

The adapter gives Yarn a temporary neutral `HOME`, so user-owned `.yarnrc` and
`.npmrc` files cannot change the install. A cache outside the worktree remains
available through `XDG_CACHE_HOME`; standard proxy and TLS transport variables
may reach only the bootstrap/install subprocesses and are never embedded in
the Frontend client environment or printed by the evidence record.

On success, the final JSON record includes:

- protocol version 5;
- deterministic `rg5-…` release ID;
- reviewed `source_git_sha`;
- Frontend artifact tree SHA256;
- isolated Frontend and Backend package file counts/tree hashes;
- exact build-meta and identity file records;
- Backend isolated-verification result;
- explicit `application/json` HTTP contract for `build-meta.json`.

This record proves what existed when the adapter completed. Because snapshot
order is unobserved, it does not by itself prove that the platform copied those
files after the adapter exited or what its runtime server returned.

## Evidence to retain from the next Cloud Build

Record the build/publish identifier and UTC timestamps, then retain sanitized
lines which establish:

1. the exact outer install and build commands and their cwd;
2. the adapter startup evidence JSON;
3. the exact pinned toolchain versions and frozen install;
4. successful clean A/B build and reproducibility verification;
5. materialization of `frontend/build/build-meta.json`, the proof, and
   `backend/release_identity.json` before snapshot;
6. the adapter package-boundary proof;
7. any platform package-root or snapshot manifest lines;
8. the actual Frontend runtime command serving the governed build directory.

If the platform does not expose items 6 or 7, record that limitation rather
than inferring an answer. Production acceptance must then supply the missing
end-to-end evidence.

## Post-deploy acceptance

After a separately authorized deployment, retain the three consecutive
`production_release_guard.py verify` results. They must agree on the same
deterministic release ID and source SHA and report:

```text
verified_identity_available=true
critical_file_hashes_match=true
frontend_build_verified=true
```

Retain canonical and cache-busted `/build-meta.json` response status, content
type, byte hash, and JSON parse result. The content type must begin with
`application/json`; an SPA HTML response is a release failure. Finally retain
`production_release_guard.py status` showing `active=false`.

Do not create a lease or deploy merely to populate this document. Evidence is
collected only during an otherwise authorized production attempt.
