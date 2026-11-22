# OPERATIONAL-WORKSPACE-RELEASE-20261122

## Current state — publication and identity verification complete; inventory failed

The user explicitly requested publication of the complete latest operational balances / Mezan2 workspace. [PR #2056](https://github.com/AMASI-SA/AMASI-SA/pull/2056) has passed all final exact-B CI and was normally merged as `02e8be41e6f1972806f0e095b8fdc38645a7f113`. **The original actual-host adapter and Node20/24 readiness rehearsals have passed. Shared-host installation and original prepare/prepublish passed for the exact release. The admin surface was recovered and Publish changes was clicked exactly once after a final successful prepublish check. The same event shows green Deployment finished with new platform version ccb1df5. The unchanged original release verification passed all three probes, and both final and fresh original guard status report active:false. Inventory failed before catalog rendering on the initial navigation and one read-only retry, so functional acceptance remains incomplete. Its narrow read-side correction is being implemented separately while frozen M3 remains unchanged.**

This is a documentation-only continuation branch: `codex/operational-workspace-release-evidence-20261122`, based on B `e5bbba04e884451c132f18708c51b7b4e6800dc9`. **Never deploy this branch.** Its extra documentation is outside the frozen A2/B source contract. The adjacent `release-evidence.json` contains the exact manifests, hashes, source paths, test commands and state fields needed to continue.

Recorded at `2026-11-22T02:00:11.994251+00:00`. Host facts are explicitly dated below; pending fields must be replaced only with actual observations.

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

### Final exact B CI — all required gates complete

At **2026-11-22T01:16:06.782Z**, the independent CI monitor confirmed every result below against exact B `e5bbba04e884451c132f18708c51b7b4e6800dc9`, with source A2 `e41695244bbe0bfde4052e074d623b8c60f16331`:

| Inventory | Success | Expected skip | Failed / pending |
|---|---:|---:|---:|
| 23 workflows | 22 | 1 | 0 / 0 |
| 48 workflow jobs | 42 | 6 | 0 / 0 |
| 49 REST check-runs, independently read by root | 43 | 6 | 0 / 0 |

The REST inventory includes the additional successful aggregate CodeQL check `137046768336`, explaining the extra check beyond the 48 jobs. There are no failed, cancelled or pending jobs. The six expected skips are four Snapchat jobs excluded by their successful scope checks, the TikTok job restricted to its unrelated named branch, and the manual deployment handoff restricted to push events. No operational acceptance gate or clean-clone rehearsal was skipped.

| Final B acceptance | Executed result |
|---|---|
| [Stock eligibility](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44969551330/job/137046164736) | **5,525 passed + 97 subtests**, 616.35 s; executed-tests/no-skips gate passed. |
| [Operational backend](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44969551408/job/137046162699) | **6,216 passed + 90 subtests**; separate shipping summary/calculator **16 passed + 18 subtests**; missing/skipped-test gate passed. The separate shipping count is from the successful executed log, not an asserted combined JUnit report. |
| [Full frontend, exact B](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44969551296/job/137046090255) | **456 suites / 4,978 tests passed**; dependency, CSP and production-build verification passed. |
| [Operational frontend](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44969551408/job/137046162830) | **89 suites / 1,415 tests passed**; zero-skipped/incomplete gate passed. This selection overlaps the full frontend suite. |
| [Browser/mobile](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44969551408/job/137046162585) | Actual **14 financial-position + 12 simple-balances scenarios** passed with source identity and retained evidence. |
| [Accounting](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44969551219) | All three jobs passed, including real isolated Mongo; focused backend **93 passed in 2.47 s**. |
| [G47 real Mongo](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44969551325/job/137046169998) | All acceptance stages passed, including recorded stages of **238, 331, 58 and 436 tests**; these stage counts are not a unique-test total. |
| [Canonical worker exclusion](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44969551408/job/137046162812) | **4 passed**. |
| [Security](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44969551296) / [CodeQL](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44969551271) | Both Security jobs and both CodeQL languages passed. |

[Release readiness run 44969551332](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44969551332) completed success. The genuine [Node20.20.2 rehearsal](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44969551332/job/137046443548) and [Node24.19.0 rehearsal](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44969551332/job/137046443493) ran every stage: clean tracked-source clone, absent generated inputs, outer frozen install, checkout without Git metadata, actual package adapter, artifact/proof/identity/package verification, and runtime build metadata served as JSON. Both emitted the exact reviewed release ID and artifact tree.

The adjacent JSON preserves the complete **23-run / 48-job matrix**, all six skip reasons, and ten retained artifacts: stock `14733127672`; operational backend `14733725551`; operational frontend `14733465265`; browser evidence `14733395420` / `14732729461`; Node20 evidence/outputs `14733321067` / `14733415669`; Node24 evidence/outputs `14733475302` / `14732644751`; canonical worker evidence `14733186885`. The stock artifact ZIP SHA-256 is `f29d1daf67a76958d2cbbeb9a19fdb1589606ad2ad5db783a2ac2f51fe6b573e`. The independent reviewer subsequently supplied all nine other artifact metadata digests and exact-B metadata bindings, retained in JSON. The stock binding is proved by exact-B checkout and its upload log; no additional archive download is claimed.

The 26 browser scenarios use synthetic data, including ApprovedFinance supplier entry. They do not constitute an exhaustive visual walkthrough of every unified reports, home or ledger page. Earlier A/A2 checks remain supporting history; final B acceptance is independently complete.

### Independent review

Immutable A review confirmed its original full-history validator, the 70 original UI paths plus eight integration paths, and preservation of all 30 published recovery paths. Independent provider-filter/navigation review found no material blocker and passed **61 tests**. Exactly one supported provider reuses the `balances.statements` grant object, mode and fragment; duplicate/unsupported providers and extra selectors are denied. Backend provider identifiers match, and API scope/write authorization is unchanged. The owner-only fee link is tested through the actual internal tools page.

Focused writer red/green tests also cover owner-only route-catalog declarations, reports using a directory-only context read and their own bound snapshots, statement provider filters, and hiding unavailable ledger-return links. The six-suite integration run passed **192 tests**. Immutable source hashes and review scope are recorded in JSON; these are review/test observations, not live deployment acceptance.

The final independent exact-B review also **passed** the original `_assert_candidate_source_transition(A2, J)`, `load_release_intent` and `verify_release_intent_git` validators, all exit 0. Full-history J..A2 intent changes are empty. The loader validated 1,378 frontend, 1,289 backend runtime and 322 release-control files plus six critical hashes. On untouched clean A2 artifacts, original `read_frontend_build_identity(require_git_source=True)` and `read_frontend_reproducibility_proof` returned records exactly equal to frozen B. These checks were read-only and did not rerun application tests.

## Verified normal merge

Root marked PR #2056 ready and normally merged it. Final GitHub PR metadata confirms exact **`merged_at: 2026-11-22T01:20:12Z`**, superseding the earlier approximate 01:20 UTC observation. Merge **`02e8be41e6f1972806f0e095b8fdc38645a7f113`** has exactly first parent J `0c5e5b98795b8d30289ec0e4d3c95738fd660af1` and second parent B `e5bbba04e884451c132f18708c51b7b4e6800dc9`.

Its tree is **`f1ad30399453b8e7d5919dfc2f6e44108458db4f`**, exactly B's tree. B → M3 has no content differences; A2 is an ancestor; A2 → M3 changes only `release/release-intent-v5.json`, blob `ff345c482b1049a09dc6c27c7f796fba263efa3f`. Root confirmed the REST commit, production ref at M3 and local Git. Independent review confirmed the same facts and passed the unchanged original `_assert_reviewed_source_relation(J,A2,M3)` with exit 0.

The actual shared-host guard was freshly observed **`active:false`** immediately before merge; no timestamp for that later observation was supplied. The subsequent shared-host installation completed as recorded below. This Git merge and host work do not establish completed live publication.

## Production and preservation

The last independently live-verified deployment recorded before this UI publication remains J `0c5e5b98795b8d30289ec0e4d3c95738fd660af1`, source `8665e8acf1d7bb4878cd2fc0b912a20132f1e0c7`, release `rg5-4d0f595ee0711c73459e0cb18711c87657602d7886a885f0ccb1a3013e31dc05`. Its prior original live verification passed three probes and closed the lease at `2026-11-22T00:24:02.181628Z`; [that completed evidence](https://github.com/AMASI-SA/AMASI-SA/blob/01d65a5c53ba6de488d1286a257d136886d3a1d7/docs/operations/CATALOG-MEMORY-RECOVERY-20261122/release-continuation-20261122.json) must not be mistaken for verification of this UI candidate.

All **30** production paths changed since the old UI fork `6e41bf25b936e8c4047df75bb170610dfc71b7f7` remain byte-identical from J to source A2. This count includes the inherited intent; B replaces that one file to bind the new UI. The remaining 29 paths stay identical to J. Their exact blob IDs, byte sizes and SHA-256 hashes are retained in JSON. In particular, `backend/dashboard_v2_routes.py` retains `0d9f4fb40beebbaf8aa27c6cb51d962913cfa2d8399afb82bea1a2e52ac42feb`.

Root's fresh actual Emergent terminal observation at **`2026-11-22T01:04:23.943349+00:00`** found the original guard **`active:false`**, `/app` HEAD equal to J, tracked worktree clean, and **417 untracked files preserved**. No source installation or lease had occurred at that observation. A new exact-B clone is ready at `/tmp/mezan-pr2056-release-20261122-ix4_840i/repo`; the stage retains `pre-rehearsal-status.json`. The subsequent actual-host adapter completed successfully as recorded below.

At that observation, `/app` had 23,760 KiB free; the separate `/tmp` volume had approximately 260 GB free. The adapter runs outside `/app`, with any later generated-output handoff bounded and prior outputs preserved. Do not reuse a stage from a previous release or remove unrelated files.

### Actual-host clean adapter completed

Root ran the original governed adapter against exact B in the new `/tmp` clone. It started **`2026-11-22T01:05:33.508493+00:00`** and finished **`2026-11-22T01:06:37.831046+00:00`**, elapsed **64.323 s**, exit **0**. Generated runtime identity exactly equals the frozen B intent; source A2, release ID, artifact tree, metadata and reproducibility proof all match the reviewed values.

The stage retains `adapter-result.json`. Stdout SHA-256 is `f724d6702631e8661319480873db7052810e67caa6e2feef42990c533f7739db`; stderr SHA-256 is `497f5fa853881c7a72d27a780637c269420a9bfd59aa53472be569975c40eefd`. At that earlier adapter-completion observation, root confirmed no `/app` source installation, merge, lease or publication had occurred. The later normal merge and completed installation supersede those two historical pending stages.

### Actual-host M3 installation completed

Root's installation started **`2026-11-22T01:22:18.687804Z`** and finished **`2026-11-22T01:22:20.068078Z`**. `/app` is on the production branch at exact M3 `02e8be41e6f1972806f0e095b8fdc38645a7f113`, with B's tree, a clean tracked worktree, **all 417 untracked paths preserved**, **zero collisions**, and **79 source paths updated**. The stage repo is clean and detached at M3, retaining the same generated outputs.

Under the original `_release_operation_lock` and with no lease active, only `frontend/build`, `frontend/.release/reproducible-build.json` and `backend/release_identity.json` were preserved to `/tmp/mezan-pr2056-release-20261122-ix4_840i/previous-app-generated` and installed from the already verified staged adapter output. Metadata, proof and runtime identity matched frozen B; available `/app` space afterward was **21,082,112 bytes**. The stage retains `install-result.json` and `install-git.log`.

An initial helper attempt stopped before mutation because its symlink scan was broader than the three generated outputs. The helper was corrected to check only those output trees; the original guard and governed source were not changed. The original prepare and prepublish subsequently passed as recorded below; that success does not establish publication.

### Historical prepare and prepublish passed; matching lease later closed

Both unmodified original guard operations passed with **exit 0**, binding source A2 `e41695244bbe0bfde4052e074d623b8c60f16331`, deployment M3 `02e8be41e6f1972806f0e095b8fdc38645a7f113` and the reviewed `rg5-08a665da9e84ab1684f181a7b87701fecc4ac2f74c935ac5ccd32e1a1bd25142` release.

| Operation | Actual observation |
|---|---|
| Prepare | Started `2026-11-22T01:23:03.478123Z`; finished `01:23:07.936043Z`; `prepared_at` `01:23:06.836156Z`; stdout SHA-256 `c08e14038849ce8dcdd2dd02137b8d4ca50de1c3d04238855f13abd6d76345d7`. |
| Prepublish | Started `2026-11-22T01:23:07.938455Z`; finished `01:23:11.464235Z`; **`ready_to_publish:true`**; stdout SHA-256 `3a1863f7127ccb8ec2bf5298ccff240d83bd0677970cc2d6e034340d32567872`. |

Both stderr files are empty, SHA-256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`. The host stage retains `prepare.stdout.json`, `prepare-result.json`, `prepublish.stdout.json` and `prepublish-result.json`.

The lease was prepared by **`chatgpt-pr2056-release-20261122-b90f3c3040a7`** and was active through publication and verification. The completed original verification later closed the matching lease; both final and fresh status now report **`active:false`**. This actor identifies the preparation history, not an asserted current active owner.

### Admin surface recovery and publication started

Root first observed focus API timeouts on old admin tab 69. The capability-report attempt also failed, claiming the affected page was not open, and one reload failed. Fresh tab 72 in the same browser opened the original `https://app.emergent.sh/chat` surface and subsequently recovered normally. Root selected the existing **Salla Analytics** project; Manage Overview confirmed **mezansalla.com** and prior platform version **`7cf99c2`**.

Immediately before publication, the original prepublish was rerun successfully: start **`2026-11-22T01:27:09.522559Z`**, finish **`2026-11-22T01:27:13.200265Z`**, exit **0**, **`ready_to_publish:true`**, with the exact A2/M3/release. Its stdout SHA-256 remains `3a1863f7127ccb8ec2bf5298ccff240d83bd0677970cc2d6e034340d32567872`. The stage retains `prepublish-final.stdout.json`, `prepublish-final.stderr.log` and `prepublish-final-result.json`; no additional unobserved stderr hash is asserted.

Root opened **Republish**, observed the **Free republishes** modal and clicked **Publish changes exactly once**. The button was then disabled and showed **Publishing...** at approximately **`2026-11-22T01:27:31Z`**. This is an approximate first observation, not an invented exact click timestamp. No paid chat or billing change was used.

The sole new deployment event has container ID **`d8dd6d00-b640-5e67-87fe-cdc367b13f42`**, with visible label **Deployment Started Nov 22, 04:27 AM** in **Asia/Aden**. Expanded progress showed **Workspace ready** and **Changes packaged** complete, with **Building your website** active at that intermediate observation. These were progress observations for the same single attempt.

### Platform completion and original identity verification complete

The same event **`d8dd6d00-b640-5e67-87fe-cdc367b13f42`** now displays the exact green label **Deployment finished Nov 22, 04:37 AM**, **Asia/Aden**. Root observed it in recovered tab72, with a screenshot independently showing green status. The new latest platform version is **`ccb1df5`**; it is a platform version label, not the source Git SHA. The UI displays **100th publish / Few seconds ago** and Republish is enabled again. The history display is capped; this record does not call it 101st or assert an independently counted publication total. The exact completion second was not supplied.

There was exactly **one Publish changes click**. The final intermediate observation, at approximately 8m30s elapsed, had Website built, Database ready and Keys & settings applied complete while the platform installed the site on its servers. It is retained as history, superseded by the newer green completion event.

The unmodified original command **`/usr/local/bin/python -B scripts/production_release_guard.py verify --url https://mezansalla.com`**, with inherited `PYTHONDONTWRITEBYTECODE=1`, ran in host **Terminal2**, PID **32626**, from **`2026-11-22T01:38:54.987068Z`** to **`2026-11-22T01:53:46.242131Z`**: **891.255 seconds**, **exit 0**, **`verified:true`**, **three successful checks**. This final result supersedes the earlier unfinished 480-second observation.

The observed result binds exact source **`e41695244bbe0bfde4052e074d623b8c60f16331`**, deployment **`02e8be41e6f1972806f0e095b8fdc38645a7f113`**, release **`rg5-08a665da9e84ab1684f181a7b87701fecc4ac2f74c935ac5ccd32e1a1bd25142`** and artifact tree **`65582eef5ae1c6aef9fc774c2ea10e5168c2fb396535c34b44be266526817526`**. There were **760 frontend requests per probe**, hence **2,280 across three probes**, computed as 760 × 3.

Original stdout is **42,599 bytes**, SHA-256 **`1f0419be6c667d83d23410f418ac817d3c258333d860474506310c241828107d`**. Stderr is **0 bytes**, SHA-256 **`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`**. The same stage retains full `verify.stdout.json`, `verify.stderr.log`, `verification-result.json`, `guard-after-verify.json` and `verified-summary.json`.

Boot observations are retained exactly: **`2026-11-22T01:37:19.732904Z`**, **`2026-11-22T01:37:19.728875Z`** and **`2026-11-22T01:46:01.109401Z`**. These differ; no single stable boot, uninterrupted uptime or absence of restarts is claimed.

The original final status and a fresh original status both report **`active:false`**. `/app` remains at exact M3, tracked clean, with **417 untracked paths preserved**. **Publication and the required original release identity verification are complete. Inventory functional acceptance remains unresolved.** Do not repeat publication, prepare, verification or lease closure for this completed M3 release.

### Bounded live UI acceptance observed

An ordinary authenticated live visit to **`https://mezansalla.com/operational-balances`** rendered the actual new burgundy/white **الرئيسية · ميزان ٢** home, including current account and movement content. The main drawer opened and showed all **seven primary sections plus tools**.

Navigation through that drawer to **`https://mezansalla.com/operational-balances?view=reports`** completed successfully. The **التقارير · ميزان ٢** title, all **12 sections**, and the actual saved report snapshot rendered after loading. Period/currency controls were present, with the explanatory text **لقطة محفوظة؛ إعادة القراءة لا تحدّث سلة ولا تسجّل حركة مالية**. Incomplete-source warnings and unknown amounts remained explicit.

Root selected Shipping/collections through the visible button, reaching **`https://mezansalla.com/operational-balances?view=reports&report_tab=couriers`**. The actual carrier, balance, shipment and collection tables fully rendered. Collection liability and cash were shown separately, with explicit unknown/incomplete-source notices.

The tools directory at **`https://mezansalla.com/operational-balances?view=tools`** rendered all specialist link groups. The provider fee page at **`https://mezansalla.com/operational-balances/provider-fees`** rendered its actual table inside the **تعرفات وعمولات المنصات · ميزان ٢** shell with the same unified header. An existing Apple Pay row displayed **تحتاج تعرفة سارية**; no rate, configuration, financial write, sync, approval or form submission was changed. The visible tools return link worked.

Root then followed the stock link to **`https://mezansalla.com/inventory-v2?from=operational-tools&tab=stock`**. **The initial attempt and one read-only retry both failed before catalog rendering**, displaying **تعذر إتمام العملية. احتفظنا بالمدخلات؛ حاول مرة أخرى.** Inventory acceptance is therefore **failed**, not pending or successful.

Source analysis points to a rejected legacy **`GET /api/operational-balances/context`** request path. The exact HTTP result has not been captured, so this is an investigation lead, **not a proven root cause**. Root is implementing the reviewed separate branch **`codex/operational-context-read-recovery-20261122`** from M3 in an isolated worktree. Its intended runtime change is limited to the GET `/context` reader using the already-used `read_financial_position_state`, with regression tests. Frozen M3 and its release evidence remain unchanged; no corrected regression or live acceptance result is asserted yet. No financial or configuration mutation was used to investigate.

These observations are limited to read-only rendering and navigation. No customer balances, names, account/transaction identifiers or live screenshots are copied into this public evidence. They are separate from the now-successful original release identity verification and do not establish a full visual audit, financial reconciliation, or sustained/per-request performance. Do not repeat completed publication or verification.

### Recent post-publication health observations

Root inspected Emergent **View Logs** at **2026-11-22T01:48:02–01:48:04Z**. This is a **post-publication snapshot, not a live stream**. It includes recent nginx `/health` upstream timeouts at **01:47:35Z, 01:47:45Z and 01:47:55Z**, together with mixed slow and fast backend timings. No unreported timing values are inferred.

These observations are material and remain unresolved in this checkpoint. They do not alone prove the inventory root cause or establish sustained performance. The subsequent original release identity verification passed; that success does not erase these historical timeout observations or establish inventory acceptance. No customer data, IPs or raw log lines are retained here.

## Next safe action

1. Verify this documentation-only checkpoint against its remote branch head before resuming. It records completed publication/identity verification and an unresolved inventory failure; it is not a deployment candidate.
2. Do not repeat M3 publication, prepare, original three-probe verification or lease closure. They completed successfully, and both final and fresh original guard status are inactive.
3. Continue the reviewed separate branch **`codex/operational-context-read-recovery-20261122`** from M3. Limit the intended runtime change to GET `/context` using existing `read_financial_position_state`, with regression tests and the required fresh release contract for any later deployment.
4. Preserve frozen M3/A2/B and its evidence. Confirm request behavior and the correction before accepting inventory; its initial attempt and one read-only retry failed, with no corrected live result yet observed.
5. Retain the bounded successful UI observations without declaring complete functional acceptance, a single stable boot, no restarts or sustained performance. Keep customer values, identifiers, IPs, raw logs and live screenshots out of public evidence.
6. Update only observed follow-up outcomes. Never deploy this documentation branch.

The platform's single publication and required original release identity checks are complete, and the matching lease is inactive. Inventory remains functionally blocked and requires the separate correction. Full functional acceptance and sustained/per-request performance are not established.
