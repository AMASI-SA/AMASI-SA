# Mobile balances read performance, 20 November 2026

The native fixes for the balances workspace were merged in amasi-mobile PRs 367 and 368. The signed Android candidate uses source 4ede388c8c9926863faf2080906942572a13a1f8.

This server change uses production base a3b8042957809d837bc8f9f848e97592f1fde245. Register pagination now preserves the first authorized matching projection of each event and the full count, while retaining only the newest prefix needed for the requested page. Report-only snapshot reads exclude the two top-level write histories at MongoDB. Financial packets, nested audit manifests, authorizations, running balances and the canonical mutation reader retain their existing behavior.

The workspace and report snapshot suites passed (61 existing cases). Three new cases cover page equivalence with ties, duplicate event IDs and out-of-range page numbers, bounded live objects, and the exact Mongo projection. On synthetic input (11,224 visible postings, 48 accounts, five rows per page) the display ranking stage peak was 9.64 MiB before and 1.28 MiB after. This measurement excludes financial report construction and device/network time.

The server candidate needs the reviewed v5 release intent and exact deployment verification before reporting a production change. No live financial transaction was created as part of the performance checks.
