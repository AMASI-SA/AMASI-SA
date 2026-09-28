# R6 writer-control source checkpoint — NOT operational rollback approval

Continuing actual R5 source on0cefc65363f79933fbc0cffdde137dc4c3e25783.
This archive preserves22 source changes with every predecessor and result hash.
The reproducible CI must pass before exported blobs are installed into normal
source paths. Raw checkpoint does not yet mean R6 runtime is materialized.

Implementation: persisted owner-only control with six scopes (creation, workflow,
financial, evidence, configuration, dispatch), strict paused-only initialization,
no ordinary-writer bootstrap, missing/malformed state fails closed, monotonic
revision/write_epoch, immutable audit/replay and fresh persisted authorization.
Each instrumented business transaction writes the same control row inside its
actual Mongo transaction. A committed pause serializes with these admissions;
retries re-read state; old job epochs are rejected after pause/resume.

Special-owned collections reject unfenced mutations and history deletion; native
supplier invoice closure reuses the admitted session rather than escaping into
an independent transaction. Native UploadFile retries receive fresh identical
streams even when the first handler closes its stream. Mixed Salla/local invoice
financial pause rejects the whole closure without partial financial writes.
Read access and existing evidence remain available during pause. Static gates
remain a separate upper bound; this does not activate the deployed application.

Limit: instrumented local database transactions ONLY. This is not a database
permission preventing arbitrary raw/legacy writers, not proof every production
worker acknowledged an epoch, and not reconciliation of unknown external effects.
The shared row also serializes merchant transactions; concurrency/load and native
writer inventory must be independently accepted before activation. No actual
recovery build, backup restoration or operational rollback has been approved.

Preservation: all R5 source records remain checked by the new cumulative47-path
contract. Historical R5 archive/guard is unchanged. Production's original label
test file stays byte-identical; BOTH R5 added tests move to a separate test file,
resolving the EOF merge conflict without dropping or weakening tests.
Offline rollback checker grows from15 to17 surfaces (control and audit added)
and requires evidence/configuration/dispatch quiescence;55 offline cases pass.
This checker still cannot authenticate evidence or authorize/execute a rollback.

Local observed tests:189 pass across six bounded isolated test groups, plus the
new no-delete test1 pass separately. No failures/skips in those current groups;
this is not a single combined run on the latest frozen source. Early failures
exposed the supplier-session gap and are documented, not hidden. Full independent
combined native/baseline/frontend CI is pending at checkpoint creation.

No server.py registration, Merge/Deploy/Preview/Production or live financial
operations, DB restore, APK/OTA, release guard/lease/intent changes. Keep PR1171
Draft and UI1167 separate. Next: verify CI, materialize, update status/continuity
and test actual committed candidate. Remaining sidecar/reports/client/rollback
acceptance is separate and must not be called complete by these tests.
