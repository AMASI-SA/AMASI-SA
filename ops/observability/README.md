# Phase 1 external collection (inactive)

Nothing here is installed, scheduled, or activated. The collector defaults to
disabled and creates no files or requests without `--enabled`. Production
activation requires a separate review and release authorization.

Reuse the existing authenticated `/api/health/diagnostics` endpoint. Run this
stdlib script outside application workers on a private management network.
Supply a restricted JSON inventory with `workers` mapping `worker-1` etc. to
explicit private IP origins (one routable target per actual worker, maximum
16), plus `health_origin` HTTPS origin. A load-balanced public diagnostics URL
cannot establish coverage of every worker. PID/start time changes mark an
observed restart, not an exact restart count across missed samples.

The token comes only from `INTERNAL_DIAGNOSTICS_TOKEN`, is sent only to each
explicit private worker target, and never goes into JSONL. Disable HTTP proxies
and redirects. HTTP is accepted only on loopback; all other private targets
require HTTPS with certificate verification. Inventory
and token provisioning must be owner-readable only; on Windows configure ACLs
explicitly because POSIX mode bits are insufficient.

Collection is serial with a two-second socket timeout and at most 64 KiB per
response. There are no retries or catch-up requests. Each completed cycle waits
at least 30 seconds. Socket timeout is not an absolute total deadline against a
slow-drip endpoint; this is a trusted management-network collector limitation.
One unauthenticated `/api/live` probe at most every 60 seconds measures external reachability;
it cannot diagnose business dependency readiness. On timeout, record only a
fixed error category and elapsed time. No URLs, tokens, exception text, raw
response bodies, query shapes, customer/order/invoice identifiers are retained.

The new numeric phase1 metrics are allowlisted; legacy Mongo gauges, Governor
capacity, and memory gauges are copied only through fixed numeric keys. Compute
interval histogram deltas per worker boot, then aggregate buckets across workers
before p50/p95/p99 calculation. Do not average percentiles. CPU is the delta of
worker CPU seconds divided by elapsed wall seconds; sum over workers and compare
with allocated CPU. Missing workers/collector failures are coverage gaps, never
zero activity. A restarted or replaced worker begins a new delta baseline.

JSONL output is bounded to eight 4 MiB segments (32 MiB total), with seven-day
expiry checked against each segment's oldest stored timestamp on each append.
Segments rotate daily or at their size cap. Whole-segment expiry may remove
newer records in that segment early. High volume may shorten effective retention;
retention is an upper bound. Use a dedicated directory owned by the collector;
one collector process only. If the collector is stopped, scheduled expiry stops
too: remove the dedicated evidence directory through a separately approved
retention process after seven days. Disk permission/full errors stop only the
external collector, never application transactions.

`nginx-timing.example.conf` is not included anywhere. Route mappings must be
verified against the actual ingress paths before future activation. An example
access log must not be enabled until bounded rotation or an external logging
driver is tested. Proposed external log ceiling: 32 MiB plus seven-day retention;
rotation/shipper availability is a deployment prerequisite, not implemented here.

Rollback: stop the external collector; disable the phase1 runtime switch using
the reviewed control plane; restore previous nginx configuration only if it was
separately activated. There is no change to Mongo writes or business workflows.
No cleanup, restart, scheduler creation, or production command is performed by
this PR. Collector CPU/disk cost occurs on its host, not the API worker; actual
overhead must be measured in the reported test environment, not inferred.
