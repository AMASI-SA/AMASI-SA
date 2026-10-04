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

Verified locally: 16 pure acceptance/integrity tests, 9 isolated real Mongo stage/PDF
read-only tests, 44 existing supplier tests. A-H include 200 identical and 200 varied.
Canonical fixture also consumed by Mobile companion PR #254 and web rendering tests.
Full CI and frontend execution still pending; not release-ready.

Next: finish frontend tests, independent diff review and full exact-HEAD CI.
No Merge, Prepare, Prepublish, Deploy, APK or modification of the active release lease.
Production data unchanged; Production financial writes=0.
