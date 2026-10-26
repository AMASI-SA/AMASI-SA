# Driver self assignment — source candidate

Task: authenticated active courier can claim a ready unassigned store-courier shipment by the same shipment barcode or canonical order number, then use verified Salla delivering transition. Existing other-driver assignments are informational only, exposing order, driver name, status and actual timestamps; no transfer or customer data. Existing own assigned shipments remain receivable.

Backend base: f37d4bebfb64e1e94b0aafd6cf3a52a700b1886f.
Task branch: codex/delivery-self-assignment-20261026.

Shared handover operations extracted into service functions; operator HTTP routes retain their permission checks. Native receive routes derive merchant and driver from authenticated active profile. Reuse city, readiness, fee snapshot, instruction gates, rollback and workflow CAS. Self assignment does not manufacture print confirmation. Provider failure leaves assignment assigned for retry; never reports pickup success before provider status verification.

Local evidence: 58 focused tests passed (8 new self-assignment scenarios), then 38 related tests passed / 3 transaction tests skipped (no real Mongo URI). Four native message tests passed separately. Salla provider is simulated; no live shipment modified. Initial test environment missing email-validator resolved; mongomock exclusion-projection defect reproduced and isolated in test adapter. One source-location assertion updated for extracted service; original assertions retained.

NOT RELEASED. No merge, live change, deployment lease, Salla mutation, APK, OTA, EAS or device acceptance. Native candidate based on verified build12 commit 7e78ddf3d932221cbb7079fd0728e2609cc3a03d; unrelated instruction draft299 preserved.

Next: CI typecheck and source review; real Mongo concurrency/recovery checks, final integrated provider/native acceptance, then governed backend release and signed APK build. Local environment has no Expo/EAS credentials and no authenticated git clone for private native repo; connector can read/write source. Do not claim this source milestone is an installable version.
