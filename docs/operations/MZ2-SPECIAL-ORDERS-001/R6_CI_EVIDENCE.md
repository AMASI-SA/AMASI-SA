# R6 scoped writer admission — verified reconstructed source, not release approval

Status: IN_PROGRESS_NOT_READY_TO_DEPLOY. All22 exported source blobs are now
installed in ordinary source paths. R5 was already materialized in95244856.
The complete historical R5/R6 archives remain unchanged. The current layered
verifier checks47 distinct source paths, not merely the latest22 files.

## Exact evidence

- Reproducer source: e400b306ad7dadf23d0f21d2bac5f8e028e9cc53.
- Reproducer tree: db69b4f795ba034d96813af587a613989a7ce969.
- Run36459259226 / job109053385550 completed successfully.
- Tests ran AFTER applying the exact R6 patch, not on the raw reproducer tree.
- Every22 exported Git blob SHA and SHA256 was independently matched to the
  locally tested source before installation. Committed-tree CI is a separate gate.

## Inspected results

- Frozen ordinary-order baseline:117 PASS,0 failures/errors/skips.
- Candidate Backend:307 PASS,0 failures/errors/skips (same117 plus190 special tests).
- Frontend Jest:14 PASS,0 failed/pending across THREE files; both moved R5 tests
  and the untouched Production741.40 regression assertion execute.
- Offline rollback evidence checker:55 unit tests PASS; not an operational drill.

Artifact10986164611:12975 bytes, ZIP SHA256
18d07a06fe9ddda7dbaed8f9918ce2355a678b73c2273cdf2a4f1472a5d3401f

r6-baseline.xml:6b9e92aa880a5c4b70eb874b09906f1cee9166005e81f9f5532cb3b59db43b26
r6-candidate.xml:f05f8446e122e729a3aeb923cbaac57181936a5a65b13c73fbd9a17aaffb7edb
r6-frontend-tests.json:68d7022fc262e7151261cd3409d5c78f47ceecfa1ccc1ea3e3a0ec930a5be05b
r6-source-blobs.json:645f52f2e9943b220a98865df6dadef0d4e3a0793e6c1c6814d3e2f63d89fe7b

## Meaning and limits

Persisted six-scope control is owner-only and initializes paused; missing or
malformed state fails closed. Actual Mongo tests exercise pause versus active
transaction, stale snapshot/epoch, revoked role, same-key replay, cross-tenant
isolation, read availability and whole mixed invoice refusal. Native supplier
closure uses the admitted real session. Upload callback retry preserves bytes.

This protects INSTRUMENTED local database writers. It is not a DB permission
stopping arbitrary raw/legacy workers or proof of production-wide acknowledgments.
The control row serializes protected merchant transactions; load/timeout behavior,
all-writer discovery, unknown external effects and compatibility require review.
No approved recovery release or backup restore/operational rollback drill exists.
The offline55 tests and synthetic native lifecycle tests do not replace those.

No server.py registration, static gate enablement, Merge/Deploy/Preview/Production,
real merchant/provider/financial writes, DB restore, APK/OTA or release lease/intent
changes. Remaining persistent Salla balance sidecar, actual report consumers and
actual client/operational recovery acceptance remain open.
