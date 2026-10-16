# Bounded shared latency session (Draft; not activated)

Purpose: diagnose server latency seen by web and employee-app API users without
changing business requests or inferring mobile/network/rendering latency. The
existing Dashboard/Snapchat phase timings and loop-lag observer remain. No
Collector, provider call, Mongo hook, Governor change or PR1316 watcher is added.

## Explicit capabilities and authority

Default: no control routes, task or control DB access. The operator must separately
authorize a deployment/configuration change setting all of:

- `LATENCY_EVIDENCE_ENABLED=1`
- `LATENCY_EVIDENCE_CONTROL_ENABLED=1`
- `LATENCY_EVIDENCE_EXPECTED_WORKERS` (1..8, topology must first be established)
- `LATENCY_EVIDENCE_CONTROL_TOKEN` (dedicated random secret of at least32 characters;
  cannot equal `INTERNAL_DIAGNOSTICS_TOKEN`; never supplied in query strings)

Shared mode and `LATENCY_EVIDENCE_PERMIT_FILE` are mutually exclusive. The old
safe-file mode remains for isolated consumers; invalid shared setup cannot fall
back to it. These capabilities are read during app setup, so an environment change
does not magically change a running process. No setting is enabled by this PR.

GET `/api/internal/latency-evidence` and POST `/start` or `/stop` under that path
require `X-Mezan-Latency-Control-Token`; the ordinary diagnostics reader token has
no control authority. Responses are no-store. Start/stop bodies bind release_id,
UUID session_id and expected_revision; duration_seconds is1..900. An identical
start replay never extends its deadline. A stale stop cannot stop a new session.

## Shared control and truthful acknowledgments

One document per release in `latency_evidence_control_v1`, primary/majority reads
and majority writes. It contains only revision, random session/worker identities,
expiry, explicit worker targets and acknowledgments. No user/order/provider data
or credentials. The collection is operational state, not a business collection.

Each explicitly configured worker starts one async control task after startup,
polling every5 seconds, with1-second I/O timeouts. This is not a new thread or an
independent stall watchdog. Each cycle performs a read and acknowledgment write;
two workers cost approximately24 reads and24 ack writes/minute, plus infrequent
registration/stale-entry cleanup and owner commands. This cost must be measured.
The existing loop-lag monitor is not made dependent on a Mongo round trip.

Start requires the exact configured number of fresh, disabled workers and uses
revision CAS. Only those worker UUIDs can join. A replacement worker never joins
an existing session. Registrations are bounded to16 per release; stale non-target
entries are reclaimed with timestamp CAS. Old target entries remain until a later
explicit session changes the targets. No TTL index or automatic business backfill.
The number of release documents grows with releases, not requests; removal of old
operational documents is a separate maintenance action, not implemented here.

The response is `unconfirmed` until every target acknowledges the current revision
and desired state. Missing/stale workers cannot be treated as disabled or healthy.
A group-wide immediate stop cannot be promised across network/process failure.
Without a fresh read, emission fails closed after10 seconds, and local monotonic
expiry cannot be extended by a backwards wall-clock change during a session.
Forward clock changes may end a session early. Accurate UTC remains operationally
necessary for cross-container evidence correlation.

Emission and local off transitions share a lock. No evidence is emitted after
local off confirmation, and old in-flight samples cannot appear in a new session.
Full process suspension cannot execute an acknowledgment; status is unknown. On
resume an expired/stale permit is checked before emission. It is not a guarantee
that HTTP health responds while the same event loop is intentionally stalled.

## Evidence scope and limits

API samples use four fixed route groups (ready, supplier_invoice, shipping, other)
and status class. Never read headers, bodies, query strings or raw path identifiers.
Thus web/mobile server paths are covered, but client type is not inferred. Health
and control endpoints are excluded from samples. Maximum one sample/group/30s;
together with existing stage/lag limits, at most14 evidence records/minute/worker
apart from interval-boundary effects. API samples are rate-limited examples, not
unbiased whole-traffic p95/p99 or an exhaustive request/error counter.

All records include session/worker/release identity. Platform log metadata must
supply pod identity. Capture hosting logs promptly during the approved session;
this PR adds neither a log exporter nor persistent customer-data storage.

## Verification and rollback

Tests use only a synthetic isolated Mongo8.0.12 replica set. The two-process
rehearsal uses loopback HTTP, synthetic data, no business startup and no provider
connections. Predeclared timing: off/on/off, each2s warmup,10s at100rps and3s
throughput; one run without profiler. Raw timings and per-process CPU/RSS retained.
Separate injected stall/replacement tests are excluded from latency measurements.
No Production performance acceptance or15-minute sustained-load claim follows.

Rollback activation: stop the exact session; require fresh per-target disabled
acknowledgments or report unconfirmed. Do not interpret silence as success. Expiry
and fresh-control deadlines are secondary safeguards. Disable capability during a
separately approved configuration lifecycle change to remove control polling.
Code rollback is a separately reviewed release reverting only this PR, preserving
the original #1368 instrumentation and subsequent Production fixes.
