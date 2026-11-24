undefined
## Host identity checkpoint E7 — independently reviewed v3

This checkpoint follows evidence E6
`3174ca0d7ecc087dbf306213ca19b4f3f55b9ee5`. It preserves all frozen application
source, intent, earlier evidence and historical operator bytes. No deployment
commit D or dashboard execution envelope exists yet.

After the code-server reconnect, root observed the old pinned Mongo PID 96507
and prior `/tmp/mezan-cabinet-archive-release-live-e7l6f9wi` stage were absent.
The checkout remained clean at J
`f199a471f8bc6e05b475bb9b65a12107cbdc946b`, and the exact predecessor lease
remained present. These are observations of changed state; no specific cause or
full-host replacement is claimed. E5's old pins must not execute on that state.

Root's two fresh process/root observations identified the same exact dbpath,
full command hash and executable path, now at PID 311 with stable start ticks
40102116. `/proc/311/exe` and the direct regular executable matched identity
`[4194406,85818,493,0,0]`. The protected directory and log device is now 66340;
their prior inodes, modes and uid/gid remain unchanged. All parent/root paths
were real nonsymlink directories and the log was regular. No DB descendant or
log content was read in those observations. Fresh nonignored entry count is
432; subsequent operator checks must measure the current state again.

The v3 helper proposal
`operators/pr2122_release_operator_live_scope_v3.py` is 43,579 bytes, SHA256
`d7832f19f5475912f3ebd0ba14c4fd2cd9b61572426f67190719c507d655a343`.
Exactly eight integer literals inside `LIVE_SCOPE` changed from E5: PID, start
ticks, executable device/inode, three directory devices and the log device.
Reverse substitution restores E5 exactly, and the entire AST outside that
assignment is unchanged. No release identity, scope path, command hash, guard,
adapter, capacity, backup, preservation, timeout or environment rule changed.

All six existing isolated fixture groups passed, exit 0, in 0.25 seconds, with
empty stderr. The fixture harness differs only in its expected helper hash.
Main, host rehearsal, subprocess runner and adapter were forbidden during these
tests. Root independently reviewed the exact eight-literal diff, normalized AST
and final bytes/hash and reported PASS. The complete proposal, diff, fixture
results and metadata are retained under `verification/operator-live-scope-v3/`.
These results do not establish a successful actual-host rehearsal.

Root's later original public guard status returned exit 0, `active=true`, with
the same predecessor actor/source/deployment/release and no incomplete lease
directory. Original verification and closure by that owner remain required.
This task has not called its verifier, cleared or reused its lease, or changed
shared source. A missing previous stage is not authority to recover or delete
another owner's state.

The newly reviewed baseline must still retain the exact Mongo process/root/log
identities throughout the eventual run. DB descendants remain unaccessed; the
log retains the initial prefix and identity captured for this new baseline.
No uninterrupted historical byte continuity across the reconnect is claimed.
The ten-field envelope schema and `env -u PYTHONOPTIMIZE
PYTHONDONTWRITEBYTECODE=1 python -B` invocation remain unchanged.

Next safe action: wait for the predecessor owner to complete original verification
and lease closure, freshly confirm guard inactivity, production J and final exact-B CI,
perform the authorized normal merge and exact-D CI, then review the actual
D-bound envelope and current host pins before executing the exact reviewed
helper once. Production changed by this task: **no**. No host helper execution,
dashboard lease, publish, service operation, database query, financial write,
source change or automatic rollback was performed by this review.

A second independent reviewer also approved these exact frozen bytes for durable checkpoint and later gated use. It independently rehashed E5/v3, verified the eight AST integer changes, exact reverse-restored bytes, unchanged policy/control flow, and recorded fixture-file hashes and six-group result. It did not rerun tests or access the host. Original E11 evidence and the active predecessor lease still block the next release; this approval is not execution or lease-takeover authority.
