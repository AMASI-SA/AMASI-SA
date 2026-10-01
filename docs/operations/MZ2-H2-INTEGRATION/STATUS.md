# MZ2 Accounting UI H2 integration

WIP — 2026-10-01. Draft PR #1221. This document supersedes the historical uncommitted status in MZ2-H2-CURRENCY/STATUS.md.

- Repository: AMASI-SA/AMASI-SA; branch: `codex/mz2-accounting-ui-h2`.
- Base **only** frozen H #1217: `be1540582c32760c6308c975d6ebf6308feeb7aa`, tree `8eb15e8c78f4bc7712dc0aa94156f1bf6bdd74db`.
- Fresh Production: `5a7b44b71c6c9974aba358493b3267a47d6e6314`, tree `87f9a44af5dc7d00fbc62417fb17e2f6273d8d93`.
- Remote currency checkpoint: `78174f7bd83ac1bb79b330182dabee891a041f79`, tree `2ba8ad96bd883d0a1d194bcf8e7c27b74543c74c`.
- Currency patch SHA256: `5242c2ceeb178b036545c4fa1215a4ba48a18bd46e1a327c6a23902af40adeb2`.
- Fresh currency verification on frozen H base: 81 frontend / 9 suites, 44 backend passed (exit 0).
- Draft is stacked onto H's branch to isolate the H2 review diff. H has not been changed.

## Contract boundaries

Read-only contract inspection refs (not merged or copied): E #1218 `8c91ab2a9faa59978ac6ccdb4b0818e3c65d1344`; F #1220 `6ee615508fdb0153a626341cbaeee08ca247ddf7`; G #1219 `41cfa2b55ee3398190cc4e37395ba297f9ce49e4`.

H2 is a native UI reader/presentation layer. Only the accepted currency patch changes backend input validation. E/F/G backend changes are absent from this branch; unavailable routes fail visibly with no fallback. No ledger arithmetic, activation, funding, approval or new posting implementation.

Next safe action: complete native panels and central inline desk; run combined tests/build and synthetic desktop/mobile review; push final checkpoint; inspect CI and update Issue #1006.

Production changed: no. Production writes = 0; Merge = NO; Deploy = NO; Opening Post = NO; Activation = NO.

## Integrated local verification checkpoint

- 267 frontend tests /42suites PASS (currency and adjacent accounting included).
- Adapter subset: E20, F12, G8, centraldesk1 =41PASS /5suites.
- Currency backend44PASS. Source Vite buildPASS (existing chunk/import warnings).
- Windows-only test runner preparation: normalized unchanged accountingModule.js line endings temporarily for an inherited source-text assertion, then restored exact original bytes. No service diff.
- Read-only panels and native capability gaps are documented in E-contract.md, F-contract.md, G-contract.md. All new network calls are GET. Synthetic fixtures contain no production data; unknown reads and all writes fail closed.
- Remaining: desktop/mobile screenshots and CI. Production unchanged; frozen H unchanged and clean.

## Browser verification — synthetic transport only

2026-10-01: tested the actual central daily workspace and all three new panels via the local Vite harness. No Production API request or browser write was used.

- Desktop viewport1440x1000 and mobile390x844. Document client/scroll widths matched:1425/1425 and375/375 respectively (browser scrollbar excluded). Wide tables scroll inside their regions.
- E: loaded-account search/clear, native Meta details, distinct wallet/payable bindings; policy configuration not portrayed as posted. Unknown history/CLOSED_ZERO and FX readiness visibly blocked.
- F: driver selection displayed backend COD700, payable34.50, collections0, payments0; pending bank500/POS200 stayed evidence, never final collections. No approve/pay controls.
- G: separate sales/input VAT, fees, explicit prepaid cutover, backend prepaid3250 (synthetic), missing fees shown unavailable. Native date keyboard change verified; browser automation fill alone did not emit React date change, so ArrowUp/ArrowDown was used to exercise the normal input event.
- HTTP404/unavailable, network-error retry, empty and loading states verified in browser. Viewport override reset after verification.
- Screenshots in screenshots/: desktop-advertising, desktop-driver, desktop-prepaids, mobile-daily-desk, mobile-driver, mobile-advertising, mobile-obligations, mobile-unavailable. All are synthetic viewport captures, not stitched full-page images or Production evidence.

## CI and final source check

First CI run36787172207: frontend tests/build passed; backend collection failed because test dependencies bcrypt and mongomock_motor were missing. H2 workflow now includes the existing auth/test import dependencies. No test was removed or weakened. Final-head CI result and exact HEAD/TREE are recorded in Issue#1006 and Draft PR#1221 to avoid a self-referential commit hash.

Final local source: currency backend44PASS; full frontend267PASS/42suites; E20/F12/G8/desk1PASS; sourcebuildPASS. After presentation-only compact blocker change, adapter41PASS and buildPASS rerun. Logs are whitespace-normalized for git diff check; diagnostic content preserved. Existing React act/import/chunk warnings are not new failures.

This is ready for review as an adapter layer; native E/F/G runtime deployment is not claimed. All unavailable capabilities listed in the three contract documents remain blocked. Next authorized action after handoff: review Draft#1221; backend integration must be separately authorized. No merge/deploy/activation/opening post.
