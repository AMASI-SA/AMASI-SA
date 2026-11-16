# Provider fee display repair — published and UI verified; official checks running

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
- Intent-only B is `c4bddfc4adb012620a161549b42c875c40e0e5d0`, tree `d91e281aa9fc7e02ddab1a36989dc6c3bf0b68ec`, sole parent A. PR1951 was subsequently normally merged as recorded below. B changes only the intent file; source A remains frozen.
- Original isolated B validation completed successfully with official `load_release_intent()` and `verify_release_intent_git()`; exact A/J/B/runtime matched, tracked worktree clean. Original `/app` was baseline J at that isolated check; it is now reviewed M as recorded below.
- All 11 expected latest B workflows completed successfully, including readiness `43773611516` and operational `43773611494`. Both actual Host Node 20/24 rehearsals passed. Two earlier duplicate Snapchat runs were superseded/cancelled; their latest replacements passed. The unrelated conditional TikTok candidate run is skipped.
- Independent generated-byte/package acceptance passed on both hosts: all 297 generated files are byte-identical, all 294 frontend files and canonical metadata match the intent; complete frontend/backend package trees reconstructed. Common package-proof SHA256 `8bc0564eba0bbf3c4e7c45464c7324445b16d011df3ef1721459ca41f75f9747` (1,278 bytes); acceptance receipt adjacent.
- PR1951 normally merged as `2589f440106a7c5d463ee03eac3617d113319673`, ordered parents J/B, tree `d91e281aa9fc7e02ddab1a36989dc6c3bf0b68ec` exactly equal to B. Git API verified this after merge. All three exact-M push workflows completed successfully: Mezan Release Readiness, Security Gate and CodeQL.

## Preserved limits and original environment

- Original `/app` is now M `2589f440106a7c5d463ee03eac3617d113319673`, tracked-clean, official guard inactive; 120 unrelated untracked entries were preserved exactly across the successful ff-only update. The live site is still prior platform version `3e5bbfb`.
- The narrowly scoped diagnostic owner lookup did not establish a unique target. Source provides no proof that shell configuration is production-bound; no saved financial state was read, scope was not broadened, and no financial data was written. No live incomplete-row predicate is claimed or used to weaken calculation eligibility.
- Rehearsal helper is now bound to accepted A/B/M, source-parent map, intent/runtime and package proof; 18,859 bytes, SHA256 `40479a58a63004bd6ba9b3257f775a5110eace755c20a05656dbc7143e5a1140`. It is statically parsed and final independent byte/binding review passed. It was executed once; see the stopped-at-preflight record below. Do not run this starting-J helper again. Verifier is partially bound but `PREPARED_AT` remains None until the official prepare receipt exists; it must not run yet.
- The production Git branch has advanced to reviewed M through the normal merge; original `/app` has now advanced by the accepted ff-only handoff, while the live deployed application has not changed yet. No new lease, deployment, source refresh, statement reimport, payment/refund confirmation or other financial write occurred. Live Production changed in this follow-up: **no**.

## Original rehearsal stop — retained evidence

The bound operator ran once. The ff-only update to M succeeded, source/tree/intent and untracked metadata checks passed, but `intent-preflight` exited 1 before the adapter/build began. The official manifest validator rejected `backend/__pycache__`. Independent read-only inventory found exactly two ignored, untracked regular `.pyc` files (120,563 bytes total), last modified before this source handoff. Guard is inactive; no lease or publish occurred. A preceding old generated identity warning is not being treated as the primary failure. See `original-preflight-stop.json` for exact receipt and inventory hashes.

## Reviewed environment recovery

A separate resume helper is staged for static review only: `resume_original_release_1951.py`, 18,784 bytes, SHA256 `040131064e7d03607747339d0c7812636d3af3008e3d2bbdb68550acf132488b`. Root reviewed the complete helper and accepted the bounded behavior. The independently reconstructed 756-byte inventory exactly matches original SHA256 `449a2329eb6ad4aa463e6b03e669ccd245aaa4c7a939ed110addeb4912bf528d`; it is adjacent. The original cache device and /tmp device differ. Recovery therefore uses explicit exclusive copy, fsync, digest/metadata/member revalidation, then removal of only the two reviewed originals and their now-empty directory. Copies remain private outside /app. No validator, financial data, or source code changes are involved. Execute once only after independent static review acceptance; it requires exact M, prior pinned receipts, inactive official guard/mutex, preserved unrelated metadata, and the existing accepted package proof. It runs the unchanged official preflight/build/package checks. No lease or publication is performed by this helper.

## Accepted original rehearsal

The reviewed resume helper ran once and completed successfully at 2026-11-15T23:43:06.486531+00:00. The two generated cache files were durably preserved outside /app and their copies remain hash-verified. Official intent preflight passed, configured adapter build exited 0, and official package verification returned the exact accepted B proof bytes (SHA256 `8bc0564eba0bbf3c4e7c45464c7324445b16d011df3ef1721459ca41f75f9747`). Exact M/tree/intent/runtime remain unchanged; 120 unrelated untracked entries have identical before/after metadata. No byte-identity claim is made for unrelated contents. Original final result SHA256 `5c488e012c60f2c9655bf27626e32275c46a1ec7f4adae055dd3819176cc82fe`. Root observed the receipts and fresh official inactive status; independent receipt review accepted the reported evidence. See `original-rehearsal-acceptance.json` for hashes and preserved limits.

## Official prepare and final verifier

Official prepare ran once and exited 0. Owned actor `chatgpt-provider1951-20261116-root`, exact prepared_at `2026-11-15T23:45:16.349437+00:00`, exact A/J/M/runtime and protocol 5. Full private receipt is `/tmp/provider1951-publication-20261116/prepare.json`, SHA256 `e542d1c14b0252c52201adf57d4a59bf9a2cde4383c601f8880f2b8c90621643`. The owned release lease is now active. Original M/build remain unchanged.

Final bound verifier `official_verify_1951.py`: 9,702 bytes, SHA256 `db3ee59f4c9b27717b6757b278aaef02d8208dba46cc61cfc949007f509b85d3`. Root and both independent static reviewers accepted exact pins and unchanged executable behavior. No verifier execution yet. Fresh Manage Overview still shows prior platform `3e5bbfb` (100th publish), so publication has not happened.

## Live publication and UI acceptance

Official prepublish passed at 2026-11-15T23:47:41.993569+00:00; stdout SHA256 `4b0921de44e7a5909e6b9841a60b20c2942f81612c8efe0f7661d842253ca125`. The authorized platform publish was initiated once. New published version `c1784c4` and green `Deployment finished Nov 16, 02:58 AM` were observed in the platform UI (Riyadh timezone, minute precision). Live Production changed: **yes**.

Bound verifier launched once: wrapper PID39422, official child PID39424. It is running uninterrupted; latest observed stdout/stderr are empty and no final receipt exists yet. Do not run a second verifier, abort the lease, or stop these processes. Private receipts remain `/tmp/provider1951-publication-20261116`.

A normal fresh live provider-page read shows the new numeric known estimated subtotal, its partial-coverage label, concise table cells and source details below under «توضيحات». Visual inspection passed; the existing numeric review control opens the prior review dialog and was closed without mutation. No source refresh, statement reimport or financial write was performed. User-facing table is marked for review. Account figures and order identifiers are intentionally omitted from this durable engineering ledger.

## Next safe actions

1. Allow the current once-only official verifier to finish. Observe its exact final receipt, three successful checks and inactive guard; do not infer technical completion from successful UI acceptance alone.
2. Save the official outcome in this evidence branch and update the canonical PR body. No further source change or test expansion is needed absent a concrete failure.
3. Complete the browser workflow and report the calculated estimate, below-table explanations and the remaining explicit partial-coverage qualification to the user.

Issue1006 remains at its known comment limit. Live Production changed: **yes**; UI acceptance passed. The owned publication lease remains active until the running official verifier succeeds.
