# Ready combined release candidate — 2026-11-03

DRAFT source A candidate. NOT merged into production, NOT published, NO live financial activation.

Canonical task: PR1687, OP-UNIFIED-BALANCES-20261102. Read DELIVERY-REGISTER.md and READY-RELEASE-20261103.md. The latter records milestone004 and the full included/deferred matrix. Complete previous status is preserved byte-for-byte in STATUS-PRE-SOURCE-A-20261103.md (blob061fdfbfdf97d7ac53274af01997743e14b90b56).

The owner requires BOTH R03/R05 WITH ALL safely ready fixes in the next release; independent unfinished work is deferred for later releases. This supersedes the old all-backlog gate, without waiving dependencies or financial approval.

R03 root financial-base/existing-service to unchanged legacy receiving and R05 explicit native invoice/payment to operational identities are implemented. Source411e9cad8f6e2049e263074e794d80d052603d04 is preserved through integration3dd5bdbfe26a70e6dc8c66b2e6fed08e78e580f0. Both operational CI jobs41537738073 passed on3dd5: official artifacts independently verified1733 backend/335 frontend28suites, zero failures/skips. This is pre-newer-production acceptance, NOT final candidate acceptance.

Latest reviewed production J is9c550813afa5314dc498c20644033478aefbf80d. GitHub synthetic merge0383e0ab058ff388e6fc5865ff458748da917272 has parents J and3dd5, treef4c3d20c1a9be429383537ddfe08bdc83d8e0b46. Its incoming diff has exactly the six production bundle selection/provenance/intent paths. New sourceA uses that combined application tree with single parentJ, no source-transfer/export workflows, and unchanged J intent blob99cbf81a72b009b3016ef94d1841c8f423bb6c2b. Integration history remains recoverable; neither original branch nor production is reset. This avoids carrying old intent-edit history into sourceA; no classifier or protocol is weakened. The inherited intent is NOT authorization to deploy A.

Required next: exact-head combined operational/G47/security/CodeQL/release-readiness acceptance on this candidate, inspect functional limits and UI, freeze fresh deterministic intent-onlyB from the exact candidate artifact, verify B, coordinate actual /app inactive lease, prepare/prepublish and one authorized publish. Never merge source-onlyA directly to production. No live balances, cutoff, invoices, stock, provider policy, refunds, payroll, /app or lease were changed.

Known limited states remain honest: nested financial-only base without a unique legacy receipt identity; unallocated/partially allocated historic cash; native invoice before saved startup; provider postsettlement refunds and statement posting; full expected profit/COD and remaining independent reporting work. See the matrix for what executes, requires separate owner approval, or remains unavailable. Code publication alone does not certify merchant balances.

The exact candidate SHA and PR must be recorded in PR1687 after creation; no self-referential SHA is fabricated here. Do not reuse earlier test counts for a newer tree. Production changed:NO.
