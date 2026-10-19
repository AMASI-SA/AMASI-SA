# Mezan and Android continuation — WIP

Publication ownership transferred explicitly from task 01a12745-aac3-73e0-9533-9e3a4e5ed981 on 2026-10-19. One publisher only. No production change or lease by this continuation.

Backend baseline: production 56919d2a8c48ca5fe85ee5543e7d86563d4c982f, print PR1444 6cf8d2cd4ee8fe84b1b456a66d76b33536db21ed. Integrated unchanged PR1438 14be2841e9b57ede4e88b4a2766db15951c24466, PR1440 3cb02eed73d7cb6131db7ffce67465c97d5a06ef and PR1435 03395ef5a6cba18fbc1e58cc6101e65b6ac3306e on a separate task branch. Automatic merge clean; release intent unchanged. Combined validation PENDING. This is NOT a reviewed release or deployment candidate.

Android remote/local checkpoint fd4347bef40494902a987ae75dc4243727acc302 verified. Its diff from tested48467af9960785ed5f03532435f3f30da25c1727 is only STATUS and verification-summary. Tested tree90119f7cfcbb2e4e366e8b77a596e2f7da2c096b verified. Prior30/30 local groups are historical source evidence, not native/live acceptance. No APK/OTA.

Read-only gift evidence: selected reported order has authoritative Salla source buy_as_gift and actual boolean address_incomplete=false NOW; status delivering and shipment ship_to has detailed address. Root cached shipping address comes from order.customer and only city/country; it must not prove recipient completeness. No customer values retained. Do not invent a provider confirmation field. Existing gift_db_enrichment uses source buy_as_gift. Gift review gate remains unimplemented.

Hidden-options investigation remains unresolved; preserve approval and ambiguous-outcome protections. Existing synthetic hidden-option success did not reproduce live failure. No live approval repeated.

Browser connection verified usable; Mongo read surface available. /app guard still must be checked before any workspace mutation. PR1444 checks24SUCCESS/4SKIPPED: two settings tests depend on scope; adapter rehearsal requires reviewed intent; manual deployment job requires push. No new frozen intent, rehearsal or deployment.

Next: combined regression validation; gift address gate from actual provider evidence; sanitized hidden-option rejection evidence; current /app guard; reviewed source A/intent-only B with CI/rehearsal and rollback; governed deployment and actual Web PDF acceptance before new independent-channel APK; emulator timing and physical device acceptance. Preserve all existing installs and channels.

## Source-only correction and gift slice
PR1445 is closed unmerged: its octopus merge ancestry failed Release Guard source-A history checks even with unchanged final intent bytes. Replacement PR1446 on codex/mezan-completion-source-20261019 preserves identical source without importing old intent history; original work preserved. Full-history intent-path check is empty from production56919. Do not use1445 for release.
Gift gate is now implemented in local review transaction and detail projection; Web and Android disable approval and show server message. Only provider source buy_as_gift is subject to it. GET /orders/{id} address_incomplete must be exactly false; missing/true/string/number evidence blocks. No buyer-address fallback. Initial real-Mongo test run failed because incomplete gifts returned200; after change5tests+3subtestsPASS14.95s. Combined regression pending. Web DOM cases added to CI; no claim of Web runtime acceptance yet.
Android isolated continuation codex/android-completion-20261019 commit46da326 preservesfd4347b and adds gift UI/transport fence. Actual TS/TSX harness including blocked-gift noPOST case and TypeScript bothPASS. No APK/OTA.
Actual read-only hidden-option evidence: reported order has four not_committed closures and a later completed local operation at2026-10-18T19:05:35.929407+00:00, revision6/committed7. This proves later success, not the original rejection cause. No approval replayed; root cause remains unresolved.
Live /app guard status freshly returned active=false. No /app file mutation or publish by this continuation.

## Verified integration and remaining acceptance
Real isolated Mongo combined run finished73tests+51subtestsPASS181.36s (gift, local completion, signed displayed approval, tracking, receipt custody, settings). Backend diff between tested742d2bfb and5eb6418a is empty. GitHub exact5eb6418a301546f8cc011c864461d9b763d48239 last observation38SUCCESS/4SKIPPED/1NEUTRAL/CodeQLPythonIN_PROGRESS; Web gift test workflow passed. Rehearsal skipped because no reviewedB yet; no deployment implication.
Android runtime46da32638b932b9cfb78ad55bc8c711d0df65344 passed31/31 local groups. CI initially found an obsolete raw-code text assertion; fixture updated preserving zero-ready-effect checks,18focusedcasesPASS. Latest Android checkpointa77a090d06eab756ea29298ae481b29c9fc6cafb (test/docs only after46da326), CI pending. Branch codex/android-completion-20261019 Draft282; runtime/gates unchanged by fixture correction.
Missing live acceptance input: target293928783 now Salla delivering. Owner asked for an eligible all-ready/completed order with authorization to open its current label. Do not change target status or replay actual receipt/review to manufacture a test. Physical-device acceptance also remains outstanding. No new APK/version/build/hash exists.
Current live health sourcefdf719e67c0385fd73bea3d1931f32e4595822fc, releaserg5-4161b4475169f735ef9df7e52b2a8afbd6ca1799acc2f8bd22b14f3e3be32e99; verified identity available. This is the pre-existing live release, not this branch. Production unchanged.
