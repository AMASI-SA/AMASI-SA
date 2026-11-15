# Provider fee display repair — merged M awaiting original rehearsal

Task `PROVIDER-FEE-DISPLAY-20261116`, repository AMASI-SA/AMASI-SA, PR https://github.com/AMASI-SA/AMASI-SA/pull/1951. This is the evidence-only branch; do not merge or deploy it.

## Exact source and behavior

- Production baseline/J remains `236a428364bc8163a684795ed1be87e237476f18`, tree `6967da090ac1301ca38bc8bc179758d7eaa91976`.
- Source branch `codex/provider-fee-display-recovery-20261116`: W1 `afc3a17784e020d9b1fe3751f4c4974ffc6a9941` (parent J); final source candidate A `f7c5e110a074a242036233ea004d63c270a4daaa` (parent W1), tree `5646f5ee7c346c17e3f8ceb9bfbbedeb15e16eab`.
- The user requires estimated commission to be calculated and displayed, with explanatory prose below the table under `توضيحات`. All existing financial scope/recognition controls remain.
- Per-order saved-statement estimation uses the exact source variable ratio and fixed mean plus evidenced VAT, with per-order rounding. The correction preserves the direct accumulated complete estimated charges when another order is incomplete; it never derives estimates by subtraction or prices unknown operations.
- Compact table, numeric review trigger, below-table source details, print, and explicit workbook estimate qualification are implemented. Component diagnostics are explicitly distinguished from the calculated subtotal.

## Fresh verification

- The exact-J regression failed for the expected hidden-known-estimate assertion (exit 1); the new focused regression passes.
- Original isolated W1 run: 124 backend tests and 147 frontend tests, exit 0. All 11 W1 workflows later completed successfully.
- Original isolated final-A run: 151 frontend tests across four suites, exit 0, tracked-clean. Git confirms backend bytes unchanged from the passing W1 backend run. Receipt summaries are adjacent.
- Independent arithmetic and integration review completed; component-scope wording and export qualification findings were fixed and retested. Source tracing confirms each provider GET recomputes the view from saved financial inputs; no persistent response-cache bump or financial refresh is required. A fresh page read remains part of live acceptance.
- Exact-A readiness run `43773012171`, attempt 1, event pull_request, completed success. Candidate artifact `14184522949`, `release-v5-reviewed-intent-candidate`; ZIP SHA256 `7af8610a0be7872ba8fdfded1fe0603e714f234c6a6478abdaae9e6bc2464c08` verified on download.
- Candidate intent: 1,325,607 bytes; SHA256 `253b2f15dbdbf1868c26bb4dbe8c38a4a471e553645c75ca31bc717b818b4a5e`; Git blob `9d9d986ff35aa0ea6a1e7837f7d29cdf3c6e90df`; source A/base J correct; runtime `rg5-79ac457d2b1bc4c417fb86d9f3d6dc10f7e613f3402018cb15f101f558cda9f6`. Independent full candidate acceptance passed: all 1,195 frontend, 1,231 backend runtime and 298 release-control records matched exact-A Git trees; canonical manifests/projection trees and deterministic runtime recomputed; public origin, pinned toolchain and two identical build passes verified.
- Final-A operational backend/frontend/browser acceptance, release readiness and other observed checks succeeded. Operational Stock Eligibility A run was cancelled when superseded by B; the exact-B replacement remains a required gate. Candidate-A success does not replace reviewed-B Host Node adapter rehearsals.
- Intent-only B is `c4bddfc4adb012620a161549b42c875c40e0e5d0`, tree `d91e281aa9fc7e02ddab1a36989dc6c3bf0b68ec`, sole parent A. PR1951 is ready for review. B changes only the intent file; source A remains frozen.
- Original isolated B validation completed successfully with official `load_release_intent()` and `verify_release_intent_git()`; exact A/J/B/runtime matched, tracked worktree clean. Original `/app` is still baseline J.
- All 11 expected latest B workflows completed successfully, including readiness `43773611516` and operational `43773611494`. Both actual Host Node 20/24 rehearsals passed. Two earlier duplicate Snapchat runs were superseded/cancelled; their latest replacements passed. The unrelated conditional TikTok candidate run is skipped.
- Independent generated-byte/package acceptance passed on both hosts: all 297 generated files are byte-identical, all 294 frontend files and canonical metadata match the intent; complete frontend/backend package trees reconstructed. Common package-proof SHA256 `8bc0564eba0bbf3c4e7c45464c7324445b16d011df3ef1721459ca41f75f9747` (1,278 bytes); acceptance receipt adjacent.
- PR1951 normally merged as `2589f440106a7c5d463ee03eac3617d113319673`, ordered parents J/B, tree `d91e281aa9fc7e02ddab1a36989dc6c3bf0b68ec` exactly equal to B. Git API verified this after merge. All three exact-M push workflows completed successfully: Mezan Release Readiness, Security Gate and CodeQL.

## Preserved limits and original environment

- Original `/app` remains baseline J, tracked-clean, guard inactive; 120 unrelated untracked entries are preserved. Changes/tests are in the isolated original checkout.
- The narrowly scoped diagnostic owner lookup did not establish a unique target. Source provides no proof that shell configuration is production-bound; no saved financial state was read, scope was not broadened, and no financial data was written. No live incomplete-row predicate is claimed or used to weaken calculation eligibility.
- Rehearsal helper is now bound to accepted A/B/M, source-parent map, intent/runtime and package proof; 18,859 bytes, SHA256 `40479a58a63004bd6ba9b3257f775a5110eace755c20a05656dbc7143e5a1140`. It is statically parsed and final independent byte/binding review passed. It has not been executed. Verifier is partially bound but `PREPARED_AT` remains None until the official prepare receipt exists; it must not run yet.
- The production Git branch has advanced to reviewed M through the normal merge; original `/app` and the live deployed application have not changed yet. No new lease, deployment, source refresh, statement reimport, payment/refund confirmation or other financial write occurred. Live Production changed in this follow-up: **no**.

## Next safe actions

1. Exact-M required CI and final bound-helper review have passed. The helper and package proof were staged from immutable evidence commit `a022e81c1e8adf8fc081609115a5049cb741fad7` with exact hash checks. Fresh staging observed original J, tracked clean, guard inactive and 120 unrelated untracked entries. The operator has not run. Execute the bound rehearsal once; no remaining pre-rehearsal gate.
2. Observe fresh original guard inactive, exact starting J and clean tracked files. Execute the once-only bound original helper with `--merge 2589f440106a7c5d463ee03eac3617d113319673 --package-proof /tmp/expected-package-boundary-1951.json --evidence-directory /tmp/provider1951-original-20261116`. It performs ff-only update/configured-entry rehearsal under the official mutex, preserves unrelated metadata, and creates no lease or publication.
3. Record successful rehearsal, then create the owned official prepare receipt. Bind/checkpoint the verifier's exact `PREPARED_AT` from that receipt, run prepublish immediately before the authorized UI publication, and observe newer explicit platform success.
4. Run the single official three-probe verifier and require inactive status, then inspect the live estimated commission and below-table explanations with a fresh read. No financial refresh is needed.

Issue1006 remains at its known comment limit; this task ledger and PR provide continuity.
