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

Environment-only execution **PASS** on tested harness commit
`3124e9bf67327798202588f6d1a403a6553dc624`:
[run 37937650047](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37937650047).
All eight contract tests and the isolated real-Mongo probe passed. The artifact
was downloaded and its SHA256 independently matched GitHub's published digest.
See `evidence/RESULT.json` for the full measured environment and isolation proof.

Measured: Ubuntu 24.04.5 host, Debian 12 probe, Python 3.11.17,
MongoDB 8.0.12 PRIMARY, four logical/affinity/quota CPUs, 16373452 KiB host RAM.
Mongo memory limit 4 GiB; probe limit 1 GiB. External IPv4 and IPv6 attempts to
documentation addresses were rejected with ENETUNREACH. No Production probe,
application import or benchmark ran. Both containers and synthetic data were
removed before artifact upload. No mongomock or Salla/Qoyod traffic was used.

This result/documentation checkpoint does not change the executed harness and
intentionally skips CI. It is not an additional execution result.
Next safe action: await the user's review. Any later approved benchmark must
recreate and revalidate an isolated environment first. Do not run it now.

Production changed: no. Merge/deploy/Prepare/Prepublish: not authorized.
