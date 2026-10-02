# Three P2 regressions: scope and verification

Starting reviewed source: `9fef288420a614a132ee790c54cd462b59997054`, tree `d7a366bef650cdb129434259beee9998ff984c39`. Continue Draft PR #1247; no BUILD37 changes or release preparation.

## P2-1: source recovery must preserve saved advertising facts

The UI previously restored a partial financial catalogue, then enabled editing after a source retry without restoring the affected rows. A hybrid wallet 20 / payable 30 could become a payable-only replacement.

`AccountingOnboarding` now tracks stages restored against unavailable sources. It rehydrates those stages from the current saved session when their sources recover, before enabling the editor or shared-section saves. Other local edits and evidence remain unchanged. The existing projection, funding modes and idempotent session controller are reused.

Four actual-component regressions failed before the fix and pass afterward: prepaid, postpaid and hybrid account-source recovery plus hybrid binding-source recovery. They assert wallet 20 stays 20 while payable becomes 31, preserved provider facts/bindings, unrelated dirty input, byte-identical lost-response retry, one version increment and saved-session restoration.

## P2-2: canonical snapshot/leg identity for review rows

Server `line_no` is not unique when explicit-zero source lines precede the equity counterpart. React retained a stale row when switching snapshots (four DOM rows for three supplied legs).

`OpeningReview` now scopes each canonical leg by session ID/version/hash and entity/type/subaccount/category/side/account/line number. It does not filter, merge, renumber or post entries. Two regressions failed before the fix; all four review tests pass. Tests cover zero facts, repeated line numbers, snapshot changes, stable reordering, repeated render, empty snapshots and unchanged input data.

## P2-3: missing variant source stays unresolved

The read catalogue no longer treats sync-generated empty variants/count zero as proof that option-bearing products have no stock combinations. It uses existing retained source provenance. Explicitly empty source plus explicit text-only customization remains distinguishable from missing combinations; a later details payload cannot fall back to an older light payload. Ordinary products with no options remain eligible.

Missing/malformed/duplicate canonical variant identities remain blocked, including partial catalogues and direct UI contexts. No variant ID, index identity or component SKU is generated. The UI states: «تعذر تحميل تركيبات هذا المنتج — لا يمكن اعتماد جرد المنتج حتى تكتمل هوية التركيبات.»

The old text-customization fixture now supplies affirmative retained source evidence; this strengthens the source requirement approved by the owner. No economic assertion is removed. The backend domain suite demonstrated 15 failures before the fix, then 30 passes. Inventory frontend tests demonstrated 11 failures before the fix, then 30 passes across two suites.

## Integrated evidence and remaining boundaries

- Local governed Node 22.23.2 focused frontend: **205 PASS / 18 suites**.
- Local backend domain, inventory draft and product-details suites: **37 PASS**.
- New real-Mongo HTTP catalogue regression is included in the existing A+B CI job. It requires a loopback replica set, uses a disposable UUID database, checks owner isolation/repeated reads and compares every collection before/after.
- Exact pushed-head full frontend/backend integration, real Mongo, browser fixtures, build and security results are recorded in Issue #1006 / PR #1247 after CI completion. Older-head CI is not evidence for this follow-up.
- Live Browser UAT remains **BLOCKED_BY_ENVIRONMENT**: fresh tool attempt failed before connection with kernel-assets `os error 3`. Isolated browser fixtures are not Live UAT.
- No accounting writer, ledger/journal semantics, C3, Track F, 409/423, write-control, Opening execution, Activation or G47 sequencing change.
- Production financial writes by this work = **0**. Opening / Inventory Initialization / Activation / Backfill / Merge / Deploy = **NO**. No real owner opening balances entered.

Next action: finish exact-head CI review and owner review of the Draft. Existing C and Financial Go-Live gates stay held; no new economic contract is introduced.
