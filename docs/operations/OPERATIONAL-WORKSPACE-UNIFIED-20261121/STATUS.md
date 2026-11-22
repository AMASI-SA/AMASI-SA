# OPERATIONAL-WORKSPACE-UNIFIED-20261121

Repository: AMASI-SA/AMASI-SA
Release continuation branch: codex/operational-workspace-release-20261122
Original candidate: PR #2049, codex/operational-workspace-unified-20261121, source 32be99e430f835c8c5221d9411a2fabfe1dd00f7, tree 32795d8caa61b13110b1c2eed9136a197cd4ed4b.
Current reviewed production base J: 0c5e5b98795b8d30289ec0e4d3c95738fd660af1.
State: verified source checkpoint; fresh exact-head CI, governed A/B intent and publication remain pending. This checkpoint is not a release-ready claim.

## 2026-11-22 owner-authorized completion

The owner supplied the older payment-fee release ID and screenshots of the unified operational workspace, and explicitly requested publication of the complete latest workspace. Repository and PR discovery found #2049 to be the latest matching UI candidate; the older rg5-ddbdc7c7bfa083d85224a0f0275318ca7dacfdd05d3f628e8f3dc05c278be895 identifies already-published PR #2046, not this UI source.

Production has since advanced through the separately verified performance recovery to deployment 0c5e5b98795b8d30289ec0e4d3c95738fd660af1, source 8665e8acf1d7bb4878cd2fc0b912a20132f1e0c7, release rg5-4d0f595ee0711c73459e0cb18711c87657602d7886a885f0ccb1a3013e31dc05. Its original guard verification passed three probes and closed its lease. A fresh actual Emergent terminal check during this continuation again reported active:false, that exact production HEAD, and no tracked worktree changes. No paid Emergent conversation was used.

The UI source is integrated above that production base. All 30 production paths changed since the old UI fork remain byte-identical to J. In particular, backend/dashboard_v2_routes.py retains SHA-256 0d9f4fb40beebbaf8aa27c6cb51d962913cfa2d8399afb82bea1a2e52ac42feb. The published performance fixes, current reviewed intent, unrelated worktrees and the original writer's uncommitted navigation-test edit are preserved.

A normal merge of the stale UI branch into J was evaluated without moving a remote ref. Although the resulting intent bytes equal J, the original protocol's full-history query includes that merge as an intent change. Consequently this successor uses a sole-parent source commit above J with the same reviewed source content. The original PR #2049 branch/history is left intact. No guard change, force push, frozen-source rebase or hand-written release identity is used.

### Concrete integration corrections

- The two historical-source review routes now have explicit non-delegable owner policy declarations. Employee PAGES and grant validation retain their existing meaning; actual native owner scope still protects the four read/approval APIs. Focused catalog and operational employee-grant tests: 13 passed, exit 0. The old catalogue failure was reproduced first.
- The direct operational-balances navigation test now follows the actual home and tools flow, retaining the owner-only payment-fee service. Its obsolete secondary-rail assertion failed first; all 11 tests now pass, exit 0.
- Direct reports and entry/log-to-reports use one directory-only context read, then the report's own bound snapshot. Cached home previews are reused. The three duplicate-read regressions failed before the change; the full ApprovedFinance suite passes 11 tests, exit 0, including employee and foreign-actor denial.
- The accounting backend contract job installs the same cryptography==50.0.0 pin already present in its isolated-Mongo sibling. Local accounting contracts pass 93 tests in an existing environment with cryptography50.0.2; exact pinned CI remains required.
- The actual CI routing failure was reproduced through the shipping/App/permission suite order. The App and permission tests now use the real router with its existing CommonJS DOM-subpath harness, avoiding inconsistent virtual whole-router mocks. Existing assertions are unchanged; six related suites pass 40 tests, exit 0.
- Provider-filtered statement links now resolve only the existing balances.statements grant, retaining its exact object, mode and fragment boundary. The allowlist reuses the actual tabby/tamara/salla parser presentation contract; duplicate, unsupported and additional query selectors remain denied. The new return-to-ledger link is shown only with its existing page grant. Four resolver failures and the statements-only return-link failure were reproduced before their corrections. The final combined routing/grants/finance/statements run passes six suites and 192 tests with zero failures or skips, exit 0.

### Exact source checkpoint and completed local gates

The first successor checkpoint is cb2583082b0ca8929ee911eb87b1b5f9fe9b6660, tree 41a1a46eb1fd3cf2bb4ad8ca7c741b39ac8f3eb0, on Draft PR #2056. Independent immutable-source review passed its original adapter ancestry validator, confirmed all 70 original candidate paths plus eight integration paths, and verified the 30 published recovery paths and inherited intent remain unchanged.

The complete frontend regression on that exact checkpoint passed 456/456 suites and 4,978/4,978 tests, with zero skips, failures or runtime-error suites, exit 0. It used the governed toolchain and `yarn --cwd frontend test --watchAll=false --runInBand`, finishing at 2026-11-22T00:57:26.558416Z. Raw log SHA-256: 661609188fc70e2028960a4b808422a08c412fb09b2c460f1054a0b99caf6bbe; Jest JSON SHA-256: d1a2cba9c97c8facb54c0770f7b9f82ef42e3bf287f47fe5caabbe9ebf611380. No frontend/runtime source changed in the follow-up below.

Exact-head CI run 44968745105/job 137043615611 reached the accounting imports after the crypto correction and exposed a second focused-environment omission: five router contracts lacked httpx (88 passed). The follow-up adds only the existing sibling pin httpx==0.28.1 to that job. A newly isolated Python3.11.18 environment installed only the focused workflow dependencies, including cryptography50.0.0 and httpx0.28.1; its exact workflow test selection passed all 93 tests in 3.23 seconds, exit 0, and `pip check` found no broken requirements. The source follow-up changes only that workflow and this status record. The earlier checkpoint remains in history; no frozen source is amended.

The exact source SHA is recorded by the successor PR head and commit containing this document. After source publication, the next safe action is exact-head CI plus a clean governed source A/B build, external freeze, and an intent-only commit B. Recheck production and the actual shared-host guard before any rehearsal or source installation. Only after all required checks and the clean-clone adapter match may normal merge, prepare, prepublish, a single authorized publish and original three-probe verification proceed. Production changed by this UI continuation: no.

## Historical UI implementation evidence (2026-11-21)

Original reviewed fork: 6e41bf25b936e8c4047df75bb170610dfc71b7f7. The checks and foreign-lease observations below belong to that earlier checkpoint; they are not fresh release evidence for this successor.

The owner authorized remaining unified-workspace changes and a single publication after validation. The global operational balances entry must open the workspace directly without a dropdown or secondary rail. Existing specialized financial services will retain their URLs, authorization and accounting contracts inside the unified layout. Reports must use actual authenticated sources and explicitly preserve incomplete or unavailable evidence; design-preview fixtures are not production data. Brand icons use local assets with safe identity matching.

## Verified checkpoint

- Owner global entry is a direct link to /operational-balances. Specialized balance routes retain active section classification without the former second navigation rail.
- Managed employees enter their granted balances home, or their first granted balance page. Other restricted sections retain existing behavior.
- Canonical fees, statements, returns, exchanges, permissions and financial-position services retain their API and authorization boundaries inside the shared finance shell. Shipping and prior-provider reviews use actual scoped APIs. Inventory opened from finance remains embedded across tabs and remounts when actor/grants change.
- The legacy /financial-position entry redirects to /operational-balances?view=position; the independent financial-position-ledger is preserved.
- Reports read actual saved financial-position snapshots, with owner/session/revision binding, source coverage, explicit unknowns, separate forecasts and inline provider review details. No design-fixture amounts or personal report greeting are shipped.
- Local brand assets and exact identity aliases distinguish banks, providers, advertising accounts and carriers. Unknown or failed logos retain a generic accessible fallback.
- Carrier ledgers display cumulative snapshot COD responsibility, outbound/return fees, allocated settlement and actual cash independently of paged movements. Pending evidence and unallocated account settlements remain incomplete. Same-source summaries add no database query or financial mutation.
- Carrier entries offer existing cash receipt/payment commands inside the unified flow. Provider statements retain only genuinely supported parsers; account context cannot accept a different provider's detected file.
- Final local integrated frontend validation: 70 operational suites / 1,184 tests PASS; 5 navigation/App/permission suites / 75 tests PASS. Independent read-only review found no remaining blocker.
- Backend summary/workspace/statements/financial-position views: 152 tests PASS. Additional writer coverage/summary validation: 117 tests PASS. All tests use synthetic/local sources; no production financial commands.
- Fresh shared-host guard before this task was active:false; later checks found the foreign PR2039 lease active (actor codex-pr2039-owner-authorized). Shared mutation stopped. No own lease, merge or publication.

## Remaining

Persist the exact reviewed source tree on Draft PR #2049, validate exact-head CI and protocol-v5 A/B build, then publish only after the foreign lease closes. Recheck latest production and shared guard before any rehearsal or shared source changes. Preserve unrelated PR #2048 (supersedes #2047) and concurrent work. Financial production acceptance remains read-only. No receipt approval, ledger adjustment or synchronization is authorized as a test.

## Explicit boundaries

The report does not assert certified Salla/Mezan reconciliation, infer missing refunds, or treat expected COD as collected cash/profit. Existing eligibility/accounting policies remain unchanged. A new noncash carrier offset command requires the separate accounting design in ../noncash-settlement-design.md; no automatic netting is introduced. Existing statement import capability is not expanded to unsupported formats. The isolated local Chromium process was blocked by host socket permissions; no local browser screenshot is claimed. Required CI/browser checks and live read-only acceptance remain outstanding.

The previous fee authority release is already deployed at source eeef3913ae2e1b8fa4b58f3835478e1ed17b2988, release rg5-ddbdc7c7bfa083d85224a0f0275318ca7dacfdd05d3f628e8f3dc05c278be895; its three probes passed and lease closed. This task must not repeat that publication.
