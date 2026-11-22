# OPERATIONAL-WORKSPACE-RELEASE-20261122

## Current state — frozen UI candidate, not published

The user explicitly requested publication of the complete latest operational balances / Mezan2 workspace. The reviewed successor is [PR #2056](https://github.com/AMASI-SA/AMASI-SA/pull/2056). Source A2 and intent-only B are frozen. **Remaining exact B CI, merge, prepare/prepublish, publication and original live verification remain pending in this checkpoint. The actual-host adapter and final B Node20/24 readiness rehearsals have passed. Production has not changed through this UI task.**

This is a documentation-only continuation branch: `codex/operational-workspace-release-evidence-20261122`, based on B `e5bbba04e884451c132f18708c51b7b4e6800dc9`. **Never deploy this branch.** Its extra documentation is outside the frozen A2/B source contract. The adjacent `release-evidence.json` contains the exact manifests, hashes, source paths, test commands and state fields needed to continue.

Recorded at `2026-11-22T01:08:08.867921+00:00`. Host facts are explicitly dated below; pending fields must be replaced only with actual observations.

## Immutable source and provenance

| Role | Exact commit / tree |
|---|---|
| Reviewed production base J / prior performance merge | `0c5e5b98795b8d30289ec0e4d3c95738fd660af1` / `128537a80bcd08f93744f042d6c5927801188a59` |
| Original UI source on PR #2049 | `32be99e430f835c8c5221d9411a2fabfe1dd00f7` / `32795d8caa61b13110b1c2eed9136a197cd4ed4b` |
| First successor source A | `cb2583082b0ca8929ee911eb87b1b5f9fe9b6660` / `41a1a46eb1fd3cf2bb4ad8ca7c741b39ac8f3eb0` |
| Final source A2 | `e41695244bbe0bfde4052e074d623b8c60f16331` / `cfc496366ce85ee2b36d8a03342e3e9c58b52b56` |
| Intent-only B | `e5bbba04e884451c132f18708c51b7b4e6800dc9` / `f1ad30399453b8e7d5919dfc2f6e44108458db4f` |

Ancestry is J → A → A2 → B. A has the sole parent J. A2 changes only `.github/workflows/mz2-accounting-module.yml` and the inherited task status document. B has the sole parent A2 and changes only `release/release-intent-v5.json`. No frozen commit was amended, squashed or rebased.

[Original PR #2049](https://github.com/AMASI-SA/AMASI-SA/pull/2049) remains the UI provenance, with its branch/history preserved. Its delta was integrated above the already verified production performance release. A direct old-branch checkout would lose those fixes. A normal merge of the stale branch was evaluated without moving a remote ref, but its inherited intent history does not satisfy the original full-history release contract. The successor preserves the reviewed content with the valid sole-parent source sequence instead.

The source changes 78 paths relative to J: the 70 original candidate paths plus eight integration paths. Exact paths are recorded in JSON. The screenshot-matching workspace keeps the direct global balances entry and brings specialist services, reports, reviews and notices into the unified finance shell, with existing scoped APIs and access boundaries.

## Frozen release identity and build evidence

- Runtime release: `rg5-08a665da9e84ab1684f181a7b87701fecc4ac2f74c935ac5ccd32e1a1bd25142`.
- Source: `e41695244bbe0bfde4052e074d623b8c60f16331`; source base: `0c5e5b98795b8d30289ec0e4d3c95738fd660af1`; current deployment candidate: `e5bbba04e884451c132f18708c51b7b4e6800dc9`.
- [Tracked B intent](https://github.com/AMASI-SA/AMASI-SA/blob/e5bbba04e884451c132f18708c51b7b4e6800dc9/release/release-intent-v5.json): **1,466,981 bytes**, SHA-256 `995b3073ebdc4af30af64d51debcf27905260a06a59607437c6eb460b3cc893d`. External `operational-ui-intent-v5.json` and freeze stdout match those exact bytes.
- Frontend source: **1,378 files**, Git tree `2de557e3c3cacd864433ca93c1b1ddea2579eb43`, manifest/tree SHA-256 `861c71ebe02cacff216594610712bafe37c49a0fcd55603b85124cffa6c813d0`.
- Frontend artifact tree: `65582eef5ae1c6aef9fc774c2ea10e5168c2fb396535c34b44be266526817526`.
- `build-meta.json`: **568,965 bytes**, SHA-256 `6ebebcc91ef77f8a3510c2f43fb69b93884680a3d6b43239fd315407221473b5`.
- Reproducibility proof: **1,521 bytes**, SHA-256 `90dfd20f66d9735c6abbe0e71dc7d95e89a20a7a3365689f5c5f3a695410564f`. Both clean build passes match the same complete artifact tree and metadata; pass 2 is retained.
- Governed toolchain: **Node 22.23.2, Yarn 1.22.22, Vite 8.2.4**.
- Backend runtime manifest: **1,289 files**, manifest SHA-256 `39f905419aa3144557e82aac0087b0799ae646189a99a21af2db01249d3014ac`; relative to J, one added and two modified runtime files, no deletions.
- Release-control manifest: **322 files**, manifest SHA-256 `4f00e3e9f1595cd1558decb6db009ff71026e4a603f3574a1c0d517d72ebc016`; one modified file, no additions/deletions.

These values were derived from the exact tracked B intent, external frozen intent and generated proof in the clean source worktree. The original source verification report's complete frontend-build and reproducibility records equal the intent. The complete governed manifests remain authoritative in the pinned tracked intent; the JSON record preserves their scopes, digests, counts and critical backend hashes. Generated output remains ignored/untracked.

## Verification completed

### Entire frontend regression

On exact A `cb2583082b0ca8929ee911eb87b1b5f9fe9b6660`, the independent governed full run passed **456/456 suites and 4,978/4,978 tests**, with **zero failed, pending/skipped, todo or runtime-error tests/suites**, exit **0**. A2 changes only the workflow dependency list and status document, so the tested frontend and runtime bytes are unchanged.

Command: `CI=true PYTHONDONTWRITEBYTECODE=1 python -B scripts/frontend_release_toolchain.py exec -- yarn --cwd frontend test --watchAll=false --runInBand --json --outputFile=/workspace/scratch/b90f3c3040a7/operational-ui-full-frontend-cb258308.jest.json --cacheDirectory=/tmp/operational-ui-full-jest-20261122`.

The run started `2026-11-22T00:54:40.833126Z` and finished `2026-11-22T00:57:26.558416Z`; Jest reported **159.919 s**, wrapper **165.725 s**. HEAD stayed exact A and the frontend/toolchain diff from A remained empty.

| External evidence | SHA-256 |
|---|---|
| Raw frontend log | `661609188fc70e2028960a4b808422a08c412fb09b2c460f1054a0b99caf6bbe` |
| Complete Jest JSON | `d1a2cba9c97c8facb54c0770f7b9f82ef42e3bf287f47fe5caabbe9ebf611380` |
| Command/result summary | `d9899181fc20ff576c1f1180c0e8b95d084ac08d49108dcaa79720e480cb1735` |

The raw files are retained outside the source worktree. Their exact sizes, paths, complete counts and command are in JSON. This full run also covers the prior routing failure that depended on neighboring suite order. The test harness uses the installed router and its CommonJS DOM export; runtime routing/authorization and all prior App/PermissionRoute assertions remain intact.

### Exact A2 accounting CI

[Accounting run 44969247080](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44969247080), bound to A2 `e41695244bbe0bfde4052e074d623b8c60f16331`, completed **success**. [Backend job 137045171035](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44969247080/job/137045171035) passed **93 tests in 2.83 s**; frontend job `137045170767` also succeeded. The run head/conclusion and backend setup/install/test lines were read directly from GitHub.

Observed Python is **3.11.18**. The focused install uses `fastapi==0.140.0`, `pydantic==2.13.4`, `pymongo==4.6.3`, `bcrypt==4.1.3`, `PyJWT==2.13.0`, `cryptography==50.0.0`, `httpx==0.28.1`, `openpyxl==3.1.5`, `python-multipart==0.0.20`, `pytest==9.0.3` and the workflow's unversioned `pytest-asyncio` requirement. No unobserved installed version is asserted. The exact test command and URLs are in JSON.

Historical failures are preserved: the original focused workflow omitted `cryptography`; after that canonical pin was restored, A run `44968745105` / job `137043615611` exposed missing `httpx` in five router contracts (**5 failed, 88 passed**). A2 adds the existing sibling `httpx==0.28.1` pin. Exact A2 CI now proves all 93 contracts pass. Earlier local success in a broader dependency environment was not treated as proof of that focused CI environment.

### Frozen B CI observed so far

Root reviewed [exact B readiness run 44969551332](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44969551332), which completed **success**. The genuine Node20 job `137046443548` and Node24 job `137046443493` ran their rehearsal steps and passed. Frontend build job `137046090363` matched the reviewed artifact tree.

The full B accounting workflow, including real isolated Mongo, also succeeded; backend job `137046089791` passed **93 tests in 2.47 s**. The operational frontend passed **89 suites / 1,415 tests**, and the two browser scenario groups passed **14 and 12 scenarios**.

**Overall exact B CI is still pending.** Operational backend, stock/G47, final security and CodeQL remained outstanding in this observation, with no failure reported. These successful gates and source-A2 evidence are not a claim that every required B check is complete.

### Independent review

Immutable A review confirmed its original full-history validator, the 70 original UI paths plus eight integration paths, and preservation of all 30 published recovery paths. Independent provider-filter/navigation review found no material blocker and passed **61 tests**. Exactly one supported provider reuses the `balances.statements` grant object, mode and fragment; duplicate/unsupported providers and extra selectors are denied. Backend provider identifiers match, and API scope/write authorization is unchanged. The owner-only fee link is tested through the actual internal tools page.

Focused writer red/green tests also cover owner-only route-catalog declarations, reports using a directory-only context read and their own bound snapshots, statement provider filters, and hiding unavailable ledger-return links. The six-suite integration run passed **192 tests**. Immutable source hashes and review scope are recorded in JSON; these are review/test observations, not live deployment acceptance.

The final independent exact-B review also **passed** the original `_assert_candidate_source_transition(A2, J)`, `load_release_intent` and `verify_release_intent_git` validators, all exit 0. Full-history J..A2 intent changes are empty. The loader validated 1,378 frontend, 1,289 backend runtime and 322 release-control files plus six critical hashes. On untouched clean A2 artifacts, original `read_frontend_build_identity(require_git_source=True)` and `read_frontend_reproducibility_proof` returned records exactly equal to frozen B. These checks were read-only and did not rerun application tests.

## Production and preservation

The last verified production remains J `0c5e5b98795b8d30289ec0e4d3c95738fd660af1`, source `8665e8acf1d7bb4878cd2fc0b912a20132f1e0c7`, release `rg5-4d0f595ee0711c73459e0cb18711c87657602d7886a885f0ccb1a3013e31dc05`. Its prior original live verification passed three probes and closed the lease at `2026-11-22T00:24:02.181628Z`; [that completed evidence](https://github.com/AMASI-SA/AMASI-SA/blob/01d65a5c53ba6de488d1286a257d136886d3a1d7/docs/operations/CATALOG-MEMORY-RECOVERY-20261122/release-continuation-20261122.json) must not be mistaken for verification of this UI candidate.

All **30** production paths changed since the old UI fork `6e41bf25b936e8c4047df75bb170610dfc71b7f7` remain byte-identical from J to source A2. This count includes the inherited intent; B replaces that one file to bind the new UI. The remaining 29 paths stay identical to J. Their exact blob IDs, byte sizes and SHA-256 hashes are retained in JSON. In particular, `backend/dashboard_v2_routes.py` retains `0d9f4fb40beebbaf8aa27c6cb51d962913cfa2d8399afb82bea1a2e52ac42feb`.

Root's fresh actual Emergent terminal observation at **`2026-11-22T01:04:23.943349+00:00`** found the original guard **`active:false`**, `/app` HEAD equal to J, tracked worktree clean, and **417 untracked files preserved**. No source installation or lease had occurred at that observation. A new exact-B clone is ready at `/tmp/mezan-pr2056-release-20261122-ix4_840i/repo`; the stage retains `pre-rehearsal-status.json`. The subsequent actual-host adapter completed successfully as recorded below.

At that observation, `/app` had 23,760 KiB free; the separate `/tmp` volume had approximately 260 GB free. The adapter runs outside `/app`, with any later generated-output handoff bounded and prior outputs preserved. Do not reuse a stage from a previous release or remove unrelated files.

### Actual-host clean adapter completed

Root ran the original governed adapter against exact B in the new `/tmp` clone. It started **`2026-11-22T01:05:33.508493+00:00`** and finished **`2026-11-22T01:06:37.831046+00:00`**, elapsed **64.323 s**, exit **0**. Generated runtime identity exactly equals the frozen B intent; source A2, release ID, artifact tree, metadata and reproducibility proof all match the reviewed values.

The stage retains `adapter-result.json`. Stdout SHA-256 is `f724d6702631e8661319480873db7052810e67caa6e2feef42990c533f7739db`; stderr SHA-256 is `497f5fa853881c7a72d27a780637c269420a9bfd59aa53472be569975c40eefd`. Root confirmed no `/app` source installation, merge, lease or publication had occurred at this checkpoint.

## Next safe action

1. Refresh the PR, exact B checks, production ref and actual shared-host guard. Complete the remaining required B CI. The original actual-host clean-clone adapter has already passed; retain its evidence and repeat only if a concrete source/artifact drift or prerequisite failure requires it.
2. Once accepted, normally merge PR #2056. Verify the merge tree equals B, A2 remains an ancestor and only the intent differs after A2. Preserve the frozen A2/B history.
3. Preserve unrelated `/app` files and prior generated output. Install only the exact new deployment tree/artifacts, then use the original matching `prepare` and `prepublish` guard.
4. Continue the user's existing explicit UI publication authorization with one **Publish changes** action. Require a newer explicit successful completion, then the original three-probe verification for the intended source/release, including exact canonical/cache-busted build metadata and backend bindings. Confirm the matching lease closes inactive.
5. Update these two documentation files with actual merge, host, publication and live-verification results. Never deploy this documentation branch.

No customer values, credentials or secret environment values are included. No financial approval, write, sync or backfill is validation. This checkpoint makes no claim that the new UI is deployed, that all final B checks have passed, or that sustained/per-request performance has been measured.
