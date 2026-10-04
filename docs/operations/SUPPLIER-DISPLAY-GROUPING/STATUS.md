# Supplier invoice display grouping — WIP candidate

Base: Shipping #1251 `b3dba04dbc4f076f3412faffb977353e64e59fd9`, above Supplier #1250.
Scope: display only. No financial source edits, deployment or Production operations.

Canonical Python projection is used by the authenticated write-free display endpoint
and by finalized invoice GET/PDF using original finalized per-piece event snapshots.
Product/SKU/Variant plus exact effective cost forms a card; originals remain separate.
Fractional service costs retain original financial rounding boundaries. Display-only
minor-unit attribution is explicit, deterministic and never enters save/post/close.
Missing original snapshots yield an explicit display error; financial readback remains
available, and PDF refuses to invent identity. No current product-price fallback.

Verified locally: 30 projection/isolated Mongo/financial boundary tests and
48 web rendering/response integrity tests passed. A-H cover 200 identical and
200 varied pieces; I exercises Draft/Services/Review/final/PDF. Actual native
close output is compared to draft grouping, including mixed variants. Original
preparation options are joined read-only using invoice/session history and
validated physical identities when finalized events omit options.
200 varied native close and rollback: 2 passed (36.07 seconds combined).
Close/posting/service execution AST and native/accounting file hashes match base.

Draft Backend PR #1252; Mobile companion Draft PR #254. Full exact-HEAD CI
is pending; no release-ready claim. The frontend build runs via existing CI
build entry. No Security/Accounting/Shipping policies or sources changed.

Next: inspect full exact-HEAD CI, fix only evidenced display-scope failures.
No Merge, Prepare, Prepublish, Deploy, APK or modification of active release lease.
Production data unchanged; Production financial writes=0.

## PR #1252 release-blocker checkpoint

Starting HEAD: `302e1a29ddd8c4fd0fda3827fe81461f5c95b179`, above the
Shipping merge `513e87304097c34f9c0af99a0420cfbb711d5ab1`.
The invoice display GET exposed raw ValueError text; the neighboring PDF and
projection adapters shared that boundary. They now return literal domain codes
or safe fallback codes, retaining exception diagnostics only in server logs.
HTTP status and financial readback semantics are unchanged.

Two synthetic HTTP disclosure cases failed before the fix. After the fix,
21 focused HTTP/projection/financial-boundary tests passed locally; the 11
Mongo-dependent cases are pending isolated CI, not counted as passing.
Source changes are limited to display error handling and its tests/documentation.
Close, posting, accounting, Shipping, frontend source and the Security exception
remain unchanged.

Required Frontend build failed while classifying the stale #1250 intent against
the staged Shipping production base. Next: freeze a new official Protocol v5
intent for this source, append intent-only B to this same PR branch, and verify
all applicable CI on B. Do not change the workflow or reuse another branch's CI.
Resolve the CodeQL review thread only after the fresh analysis proves the alert
fixed. No Merge, Prepare, Prepublish, Deploy or Production write is authorized.
