# Driver self assignment — source candidate

Task: authenticated active courier can claim a ready unassigned store-courier shipment by the same shipment barcode or canonical order number, then use verified Salla delivering transition. Existing other-driver assignments are informational only, exposing order, driver name, status and actual timestamps; no transfer or customer data. Existing own assigned shipments remain receivable.

Backend base: f37d4bebfb64e1e94b0aafd6cf3a52a700b1886f.
Task branch: codex/delivery-self-assignment-r2-20261026.
Production source base:11a52dd933d7269cd80046219dcd93d5a6e62297. Original checkpoint and PR1558 preserved.

Shared handover operations extracted into service functions; operator HTTP routes retain their permission checks. Native receive routes derive merchant and driver from authenticated active profile. Reuse city, readiness, fee snapshot, instruction gates, rollback and workflow CAS. Self assignment does not manufacture print confirmation. Provider failure leaves assignment assigned for retry; never reports pickup success before provider status verification.

Local evidence: 58 focused tests passed (8 new self-assignment scenarios), then 38 related tests passed / 3 transaction tests skipped (no real Mongo URI). Four native message tests passed separately. Salla provider is simulated; no live shipment modified. Initial test environment missing email-validator resolved; mongomock exclusion-projection defect reproduced and isolated in test adapter. One source-location assertion updated for extracted service; original assertions retained.

NOT RELEASED. No merge, live change, deployment lease, Salla mutation, APK, OTA, EAS or device acceptance. Native candidate based on verified build12 commit 7e78ddf3d932221cbb7079fd0728e2609cc3a03d; unrelated instruction draft299 preserved.

Next: CI typecheck and source review; real Mongo concurrency/recovery checks, final integrated provider/native acceptance, then governed backend release and signed APK build. Local environment has no Expo/EAS credentials and no authenticated git clone for private native repo; connector can read/write source. Do not claim this source milestone is an installable version.

Latest local focused+related verification:97PASS/3real-Mongo transaction SKIP, including a deterministic two-courier race with barriers at both empty-assignment reads and workflow CAS. Exactly one confirmation succeeds; assignment/order/workflow retain the same winning driver. New self-assignment tests use real Motor when CI supplies MZ2_TEST_MONGO_URI; mocks only locally. Existing shared handover rollback already satisfies that race; no extra reordering retained.

Original native CI run40389336310 SUCCESS with TypeScript,13tests and Expo doctor. Native Draft305 source14608d0ca98d3b2814029c9b2f67cd118fc07a4c reached correct Expo project and existing remote keystore but cloud build refused exhausted monthly free quota. Native de760740eb3b33e0d3533c271d98716e54d67b86 starts EAS --local on GitHub runner, run40390173160 currently building; no billing/key/project changes. No APK success yet.

Backend integrated checkpoint8f717d087e41e0557c1a057f787ad6645133cc90 passed StoreDelivery CI602, but release-readiness correctly rejected the mixed old/new intent history. Replacement source candidate starts directly at current11a52dd; old commits and PR remain recoverable. Issue1006 refused a handoff comment because it exceeds2500comments; this task document and task PR carry the durable record. No production change or lease.
