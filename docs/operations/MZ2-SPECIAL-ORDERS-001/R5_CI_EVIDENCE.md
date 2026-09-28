# R5 source materialization and reconciliation — 28 September 2026

Status: IN_PROGRESS_NOT_READY_TO_DEPLOY. This checkpoint installs all33 tested
source blobs into ordinary backend/frontend paths. R5 is no longer archive-only.
Historical R5_RECOVERY and its exact hashes/guard remain unchanged for provenance.
The old predecessor refusal is resolved by a NEW explicitly hashed cumulative
source contract in R5_RECONCILIATION, not by weakening old checks or dropping paths.

Reconstructed source CI: run36453828449, job109034969805, source checkpoint
21084c79a81eb061203a9e9f18fa192b6a624ca1 AFTER exact reconciliation. The raw
checkpoint alone is not the tested runtime tree. Tests executed on the reproduced
33-file output; every output SHA256 and full Git blob SHA was compared independently
with the local tested bytes before materializing this commit.

Actual inspected results:
- Frozen ordinary-order baseline:117 PASS,0 failures/errors/skips.
- Reconciled backend:273 PASS,0 failures/errors/skips (the same117 plus156 special tests).
- Frontend Jest:14 PASS,0 failed/pending; both label test files executed.
- No counts are added across baseline/candidate as distinct test coverage.

Artifact10984861425,12906 bytes, independently verified archive SHA256:
a3c105d2150ead0d423206dffea9b915a433d8544d1eb3789658ad7698efb179
r5-baseline.xml SHA256 d321422808de6156ba29a257513180743496ec63f461559fdcbbcf679a450c2e
r5-candidate.xml SHA256 63cf1fa6b83bf4e2beb29024f16e3f6362e1fa19b81a1398f574749363d8b140
r5-frontend-tests.json SHA256 408eb519879112374e415c7bd5477b03e68b174660c88a7d8d86ae6fb7c0ecd4
r5-reconciled-blobs.json SHA256 e234f177f530f30bbe7818e606f4faf3cd1bd95fe08ddecf83f53e85aea5640d

Preserved newer Production behavior from a7bcb1626e2ad4defba2260d4a113417f91f3120:
employee_workspace_stage_summary; two-decimal COD label display; unchanged741.40
regression assertion. The appended R5 amount assertion now expects20.00 rather
than20, consistent with Production. Both old and new test additions remain.

Native tests exercise actual supplier/inventory/delivery/settlement and MZ2 ledger
owners against a disposable Mongo replica set, including free/partial preparation
lifecycle, rollback-on-failure and idempotency. Generated synthetic supplier PDF
was rendered and visually inspected locally. This does not establish Android device
or Preview/Production acceptance, every purpose-specific lifecycle, or safe deployment.

Still required: reliable centralized writer stop/fencing, persistent Salla balance
sidecar, actual report consumers, old/current web and both Android clients, independently
reviewed recovery target, consistent data inventory, backup restore and operational
rollback drills. No server.py registration, runtime gate activation, Merge/Deploy,
merchant/provider/financial live writes, lease or release-intent changes.
