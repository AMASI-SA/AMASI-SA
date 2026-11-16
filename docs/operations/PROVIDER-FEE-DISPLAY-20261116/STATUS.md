# Provider fee display repair — completed and live verified

Task `PROVIDER-FEE-DISPLAY-20261116`, repository AMASI-SA/AMASI-SA, merged PR https://github.com/AMASI-SA/AMASI-SA/pull/1951.

**Completed:** source correction, independent review, required tests, original-environment build/package rehearsal, authorized publication, normal live UI acceptance and three official production checks. Official guard is inactive. No further implementation or publication action remains for this task.

This is the evidence-only branch. Do not merge or deploy it, rerun the historical helpers, or alter frozen source.

## Delivered behavior

The user's requested estimated commission column now displays the directly accumulated complete estimates even when another order has incomplete components. Genuine zero, unavailable data and partial coverage remain distinct. Amounts are computed from eligible per-order evidence; actual fees replace their matched estimate once. No estimate is inferred by subtracting aggregate totals, and incomplete components are not silently deducted.

The provider table contains compact amounts, statuses, source percentage and the numeric review control. Source explanations, dates, assumptions and incomplete-fee reasons are below the table under «توضيحات» on screen and in print. The workbook preserves evidence and decimal strings, with explicit estimate qualification below the exported table. Provider known-subtotal footer is labelled «المجموع المعلوم».

Existing principal/refund recognition, currency/account scope, bank settlement and demand-only read controls remain. The ordinary provider GET recomputes from saved inputs; no financial refresh or persistent response-cache bump is required.

## Immutable release

- Prior production/base J: `236a428364bc8163a684795ed1be87e237476f18`; tree `6967da090ac1301ca38bc8bc179758d7eaa91976`.
- W1: `afc3a17784e020d9b1fe3751f4c4974ffc6a9941`, parent J.
- Frozen source A: `f7c5e110a074a242036233ea004d63c270a4daaa`; tree `5646f5ee7c346c17e3f8ceb9bfbbedeb15e16eab`, parent W1.
- Intent-only B: `c4bddfc4adb012620a161549b42c875c40e0e5d0`, sole parent A.
- Normal merge M: `2589f440106a7c5d463ee03eac3617d113319673`, ordered parents J/B.
- Identical B/M tree: `d91e281aa9fc7e02ddab1a36989dc6c3bf0b68ec`.
- Intent: 1,325,607 bytes, SHA256 `253b2f15dbdbf1868c26bb4dbe8c38a4a471e553645c75ca31bc717b818b4a5e`, blob `9d9d986ff35aa0ea6a1e7837f7d29cdf3c6e90df`.
- Runtime: `rg5-79ac457d2b1bc4c417fb86d9f3d6dc10f7e613f3402018cb15f101f558cda9f6`.

## Verification completed

- The exact-J regression failed for the intended hidden-known-estimate defect. The correction passes.
- Original isolated W1: 124 backend tests and 147 frontend tests passed. Final A: 151 frontend tests in four suites passed; backend bytes remained identical to the passing W1 backend source.
- Exact-A broad operational CI: 4,964 backend tests, plus 16 shipping/calculator tests and 18 subtests passed. Frontend and browser/mobile acceptance passed.
- All 11 expected latest B workflows passed, including readiness `43773611516` and operational `43773611494`. Host Node20/24 rehearsals passed. Superseded duplicate cancellations and the unrelated conditional TikTok skip were not treated as current failures.
- All three exact-M push workflows passed: CodeQL `43774426793`, Security Gate `43774426801`, release readiness `43774427008`.
- Independent candidate review matched every frontend, backend and release-control manifest record to A and recomputed the runtime. Independent B artifact review confirmed all 297 generated files were byte-identical across hosts and reconstructed the complete frontend/backend package trees.
- Accepted package-proof SHA256: `8bc0564eba0bbf3c4e7c45464c7324445b16d011df3ef1721459ca41f75f9747`, 1,278 bytes.

## Original environment recovery and rehearsal

The first reviewed operator advanced original /app to M by ff-only, preserving unrelated metadata, then stopped before build because the unchanged official validator rejected exactly two ignored generated Python bytecode files. The failure and inventory remain retained; see `original-preflight-stop.json`.

A separate, independently reviewed once-only helper pinned to already-installed M preserved those exact two files (120,563 bytes) outside /app through exclusive cross-filesystem copies, fsync and digest/metadata/member validation before exact removal. No validator or source was relaxed. Quarantine copies remain retained privately.

The resumed official intent preflight, configured adapter build and package verification succeeded at 2026-11-15T23:43:06.486531+00:00. Package stdout exactly matches accepted B. Source/tree/intent remained fixed; 120 unrelated untracked entries have identical before/after metadata. This is a metadata preservation claim, not a byte-identity claim for unrelated contents. See `original-rehearsal-acceptance.json`; original result SHA256 `5c488e012c60f2c9655bf27626e32275c46a1ec7f4adae055dd3819176cc82fe`.

## Publication and official completion

- Actor: `chatgpt-provider1951-20261116-root`.
- Official prepared_at: `2026-11-15T23:45:16.349437+00:00`; full prepare receipt SHA256 `e542d1c14b0252c52201adf57d4a59bf9a2cde4383c601f8880f2b8c90621643`.
- Official prepublish exited 0 at `2026-11-15T23:47:41.993569+00:00`.
- Platform publish was initiated once. New version `c1784c4` and green `Deployment finished Nov16 02:58 AM` were observed in the platform UI (Riyadh, minute precision), replacing `3e5bbfb`.
- Final bound verifier was independently accepted, 9,702 bytes, SHA256 `db3ee59f4c9b27717b6757b278aaef02d8208dba46cc61cfc949007f509b85d3`, transferred byte-exact outside /app.
- Official verification ran once without interruption: wrapper PID39422, child PID39424; `2026-11-16T00:01:08.856227+00:00` to `2026-11-16T00:12:53.325935+00:00`.
- **verified=true, checks=3**, exact A/J/M/runtime, official stdout 33,743 bytes, SHA256 `b2d0bb70a781f5497b56eb3a7a8fd4c7776884e1b0c99ea229f5bc0bfd4053f8`; stderr empty.
- Fresh official status: **active=false** at `2026-11-16T00:12:53.452736+00:00`; status SHA256 `595219792dd54a38a8ace80a6a7fc33b928ea74f0dd1bdfac6a2b708dbe0dd1e`.
- Exact final receipt `official-verify-final.json`: 1,517 bytes, SHA256 `1b1596a3c2f0632f2cd58d06e421237bdca2e51376f4c59f4b60d022095d5248`. The adjacent copy was reconstructed and hash-matched byte-for-byte to the original receipt.
- Original private publication evidence: `/tmp/provider1951-publication-20261116`.

## Live UI acceptance and limits

A normal fresh read of https://mezansalla.com/operational-balances/financial-position?view=providers displayed the calculated known estimated subtotal and its explicit partial label, compact cells and below-table explanations. Visual inspection passed. The existing numeric review control opened the prior review dialog and was closed without mutation. The table is marked for user review.

No financial source refresh, statement reimport, payment/refund confirmation, advertising change or other financial write occurred. The narrowly scoped earlier diagnostic lookup did not establish a unique owner, so it was not broadened and no saved financial state was read through that helper. No live row predicate was invented or used to weaken eligibility.

Remaining incomplete financial evidence is still reported honestly and is separate from the repaired calculation/display defect. Account figures and order identifiers are omitted from this engineering ledger. Export/print changes passed source tests; no live financial export was downloaded during acceptance.

Issue1006 remains at its known comment limit; this ledger and PR are the canonical continuity records. **Live Production changed: yes. Task complete; no active lease.**
