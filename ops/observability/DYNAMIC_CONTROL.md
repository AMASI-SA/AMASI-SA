# Dynamic control: isolated evaluation only

Base: `c266b5a283230507d524ef2ec81370a2b5de8c1d` (#1315).
No Production activation, restart, release, collector change or business write.

## Contract

No OBS configuration means disabled, with no control thread. Static
`OBS_METRICS_ENABLED=true` without a control path retains the existing behavior.
An explicit `OBS_CONTROL_FILE` is capability, not activation: the process starts
disabled and starts its watcher with the diagnostics monitor, after worker start.
The watcher checks every second, independently of the event loop. No request,
Mongo callback, or Governor callback reads a file.

Control is supported on Linux/POSIX only. Use an absolute path outside `/app`,
on a local filesystem, in an owner-private 0700 directory. Its ancestors must
be owned by root/current uid and not writable by other users (root-owned sticky
temporary directories are permitted). No symlinks are followed. The file must
be an owner-owned regular file, mode 0600 or 0400, one link, at most 16 bytes.
Write `enabled` or `disabled` by atomic replacement with those permissions.
Other content, missing files, wrong permissions, symlinks, FIFOs, hardlinks and
unsupported platforms fail closed. Do not place it on a network filesystem.

`Metrics.set_enabled(False)` returns only after earlier metric mutations finish
under the existing metric lock; later callbacks recheck this flag under the
same lock. Request cleanup can still remove an active token after disabling.
Counters/histograms do not reset at toggles. Crossing spans are not a clean
measurement window; benchmark independent processes or drain between windows.

A heartbeat gap of five seconds latches instrumentation off. Recovery of the
heartbeat alone does not re-enable it: the watcher must see valid `disabled`
while the heartbeat is healthy, then valid `enabled`. A fork starts the child
with empty metrics, no watcher thread and dynamic metrics disabled. Child
startup explicitly starts its own watcher. Unique private control paths are
required for independently selected processes; a shared path intentionally
controls every opted-in process using it.

## Limits and rollback

An ordinary event-loop stall does not prevent the watcher running. A whole
process freeze, a native extension holding the GIL, or severe OS starvation can
prevent internal acknowledgement. Do not report such a state as disabled.
The isolated test supervisor kills only its own synthetic canary on that case;
this is NOT an approved strategy for a worker serving customers.

The smallest rollback is valid `disabled` and confirmation of stable metric
counts. Stop the isolated collector separately. If the canary cannot acknowledge,
terminate only the isolated canary. Production #1315 remains unchanged.
No claim is made that a public load balancer or container IP selects one worker.

## Evidence contract

The Linux workflow records exact HEAD/TREE, all tests (no skip accepted),
activation/deactivation times, real event-loop stall, SIGSTOP whole-process
freeze, fork, multiple workers and callback races. Canary imports only the
instrumentation modules and uses synthetic loopback HTTP, without application
startup, Production credentials, DB/provider access or customer data.

Five alternating independent baseline/candidate-disabled/candidate-enabled runs
retain all latency samples, failures and timeouts. Proposed acceptance limits:
CPU +1 percentage point of one core, RSS +8 MiB, p95 +5%, throughput -5%.
Each repetition and medians are reported; a failed threshold is not hidden by
an average or a rerun. Shared CI variability and synthetic scope limit inference.
Existing Phase1 CI separately exercises actual MongoDB 8.0.12 replica-set hooks.

Collector remains unchanged and disabled by default. Its output belongs outside
`/app`; existing limits are eight approximately 4MiB files and seven-day expiry
on append. Stopped-collector retention needs external cleanup. Never store raw
diagnostics, tokens, request bodies, query strings or customer identifiers.
