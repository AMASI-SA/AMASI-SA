# Governed source / intent handoff

Manual POS follow-up: the user approved accountant receipt review and explicit
documented other-receivable selection. The prior source6c266c2c / intent252d81000
pair and PR1229 remain preserved. Its source tree plus the bounded manual POS
wiring are carried from unchanged reviewed Production J into a new source on
`codex/mz2-final-integration-pos-review-20261001`, retaining J's intent bytes until
a fresh governed build produces a new intent-only commit. No old history is
rewritten or remerged, and no old intent is claimed to verify changed source.
The exact new pair, PR and evidence are recorded externally in Issue1006.

This candidate preserves the completed integration and the newer reviewed Production source. It does not authorize production merge, deployment, opening posting or activation. C and live acceptance gates remain blocked.

- Original integration branch: codex/mz2-final-integration-20261001, PR1222.
- Preserved integration checkpoint: 85b3f71409c94217dc851b0df634798bd38cda07, tree79515efbcbb16923610ae284ba1385d6c7362d7b.
- Original integration base: 5a7b44b71c6c9974aba358493b3267a47d6e6314.
- Fresh reviewed Production base J: 901568ccaaf510dc1f84d9c28f38d368d07dc64d; its governed source P is5a68b86762d6916bbde0d1a27e49885d5a497f36. P is an ancestor of J and P-to-J changes only the intent.
- Candidate branch: codex/mz2-final-integration-reviewed-20261001.

The integration history contains intent edits/reverts. Adding another intent commit cannot satisfy the unchanged full-history guard. That branch and every checkpoint are retained. A new branch from J receives the cumulative original-base-to-integration tree delta, excluding the intent, through three-way application. No reset, force push, old-history rewrite or guard change occurs.

Before additional candidate evidence/fixture edits, exact Git blob comparison proves all447 integration-only changed paths and all23 production-only changed paths preserved. The only overlapping path is backend/store_delivery_driver_app_routes.py: both the Production native mobile-session authorization guard and integration native observer/evidence behavior are retained. No financial writer or economic contract is modified by this handoff. Subsequent independent verification found a first-use event collection initialization race. The bounded B repair provisions that existing collection through production startup before business readiness; no financial hot path or atomic/replay guard is changed. See CANDIDATE-SHIPPING-BOOTSTRAP-REPAIR.md. The inherited13 whitespace findings are retained with the exact integration blobs; they are not a semantic merge conflict.

The newly preserved mobile-session guard correctly rejects two older synthetic native-driver callbacks. Only those callbacks now identify the existing amasi_mobile client; existing403 authorization tests and the guard remain intact. Candidate regression covers native driver/payment/shipping plus the newer Production authentication, OTP, mobile session, recipient projection, preparation and supplier-PDF changes. Exact results are stored in evidence/governance and the canonical Issue1006 checkpoint.

The existing PR CI must classify candidate A, run the pinned Linux Node22.23.2/Yarn1.22.22 double build, verify the complete proof, and produce release-v5-reviewed-intent-candidate. Only that verified artifact may become intent-only B. After B, no governed source or documentation change may be appended without producing a new source/proof/intent pair. Exact A/B HEAD, TREE, artifact identity and CI are recorded externally in Issue1006 and the linked Draft PR, avoiding self-reference.

This document is a handoff plan and preservation record, not proof that a pending build or rehearsal passed. Full business UAT and Smoke B remain blocked as recorded in REMAINING-BLOCKERS.md. Production financial writes=0; Merge to Production/Deploy/Opening Post/Activation=NO; write-control unchanged; no lease is created.
