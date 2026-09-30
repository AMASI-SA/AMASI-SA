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
