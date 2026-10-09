# PR-B #1300: isolated Linux environment gate

Source: `617858e5d327d7c1cd898e73ef6aa12e726e567f`.
This independent branch adds test infrastructure only. PR-B, runtime,
operational_owner, sync writers and schema remain byte-identical to that SHA.

Only the new environment workflow runs on its dedicated branch push. It has
read-only GitHub contents permission, no environment, no secrets references,
no persisted checkout token, no deployment and **no benchmark step**.

Public image/dependency preparation runs on the ephemeral GitHub host first.
The Python image build context contains only Dockerfile, requirements and the
probe. Neither application source nor its configuration is copied into it.
The actual proof then runs in a private Docker `none` network namespace.
Mongo and the Python probe share only loopback. They run without root,
capabilities, host mounts, exposed ports, Docker socket or inherited host
environment. Fresh Mongo data lives in tmpfs and is destroyed afterward.
Image IDs/digests, exact source/harness identities and probe hash are retained.

The gate requires Linux, Python 3.11, MongoDB 8.0.12, four available CPUs,
replica set `shipping_envcheck` PRIMARY, and a successful commit/abort of one
synthetic environment-proof document. This is not a workload, benchmark,
application correctness test, race test or performance measurement. Mongomock
is neither installed nor accepted.

Both container configurations are inspected before use. The probe verifies
loopback-only interfaces, absent usable external routes, zero effective
capabilities and no-new-privileges. Connections to reserved documentation
addresses must be rejected by the kernel. No Production address is contacted
or resolved to test blocking. Salla/Qoyod/notifications have no code, credentials
or external route in this runtime.

The GitHub host retains control-plane internet access for package preparation
and artifact upload. The isolation claim applies to the Mongo/probe processes,
not to GitHub's runner daemon. Artifact upload occurs after both containers
have been removed. Reports contain only synthetic identities and environment
metadata; stdout is retained because tmpfs disappears at container exit.

The runner is temporary. A successful report proves this run's environment,
not an indefinitely running server. Any subsequently approved benchmark must
recreate and pass this same gate first. Do not launch historical benchmark
workflows or assume that environment readiness resolves PR-B's known race.

## Current checkpoint

Implementation: environment gate staged, actual GitHub execution pending.
Next action: verify the harness, push only this independent branch, inspect the
environment-only run and retained artifacts, report measured environment, then
stop for the user's review before any benchmark.

Production changed: no. Merge/deploy/Prepare/Prepublish: not authorized.
