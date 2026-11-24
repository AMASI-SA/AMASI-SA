# PR #2122 host identity proposal v3

This is a narrowly changed, locally checked proposal for the existing live-scope
operator. Root independently reviewed its exact bytes, complete eight-literal
diff and normalized AST, all passing. Durable persistence and all existing
release gates remain required before execution. No dashboard deployment commit D or execution
envelope has been created by this review, and the helper has not been run against
the actual host.

## Why the earlier host pins no longer apply

After the code-server reconnect, the root observed that the previously pinned
Mongo PID 96507 and predecessor temporary rehearsal stage were absent. The
shared checkout was still clean at J
`f199a471f8bc6e05b475bb9b65a12107cbdc946b`, and the predecessor's exact prepared
lease was still present. This establishes a changed observed host state; it does
not prove the cause of the reconnect or authorize closing that lease.

Two subsequent root observations identified one running Mongo process using the
same exact protected dbpath, full command-line hash and executable path. Its
start ticks were stable before and after the second observation. The executable
was regular and its `/proc/311/exe` target identity equaled the direct executable
file identity. All protected directories were real, resolved, nonsymlink
directories, and the sibling log was a regular file. No DB descendant or log
content was read during those root observations. This reviewer received the
metadata from root and did not perform the live observations independently.

Root subsequently ran the original public guard status successfully (exit 0).
It still reported an active predecessor lease with the exact predecessor actor,
source, deployment and release identity, without an incomplete lease directory.
That active lease continues to block this task's shared-host rehearsal.

| Pin | E5 | Fresh root observation |
| --- | --- | --- |
| Mongo PID | 96507 | 311 |
| Start ticks | 106766690 | 40102116 |
| Executable device | 3145868 | 4194406 |
| Executable inode | 85851 | 85818 |
| Device of the three protected directories | 66338 | 66340 |
| Device of sibling log | 66338 | 66340 |

The dbpath remains `.worktrees/p01-acceptance-state-20260919/mongo`; the sibling
log remains `.worktrees/p01-acceptance-state-20260919/mongo.log`. The three
directory inodes remain 394231, 396081 and 396082, with modes 0755, 0700 and 0755
and uid/gid 0/0. The log inode remains 121637, mode 0644, uid/gid 0/0. The
executable remains `/usr/bin/mongod`, mode 0755, uid/gid 0/0. The full command
hash remains
`3afd2534eb5355d9b66e77ed088d8d3db819115b34d0e8d2901e052468cd2832`.

The root also observed 432 nonignored entries and fresh free space of
420,474,880 bytes on `/app` and 268,655,374,336 bytes on `/tmp`. These are point
observations, not an execution envelope or proof that a later build has enough
space. The original helper retains its own fresh measurements, exact backups,
clone-derived requirements, reclaim calculation and actual adapter gates.

## Exact change and verification

The immutable E5 upstream is evidence commit
`0beba15679fed2ada56896a4847b0368040e8edd`. Its 43,582-byte helper has SHA256
`62e545a2b11520f121aebf0a985b0854134052070ff7355892fcec0f95eb72c7`.
The v3 proposal is 43,579 bytes, SHA256
`d7832f19f5475912f3ebd0ba14c4fd2cd9b61572426f67190719c507d655a343`.

Exactly eight integer literals changed, all inside `LIVE_SCOPE`: PID, start
ticks, executable device and inode, the three directory devices and the log
device. `adaptation.diff` contains the complete change. Reversing those six
text replacements reproduces E5 byte-for-byte. The full AST is identical after
restoring the original `LIVE_SCOPE` value. No control flow, release constant,
scope path, command hash, policy, guard, adapter, snapshot, backup, capacity,
environment, timeout or cleanup behavior changed.

All six existing isolated fixture groups passed, exit 0, in 0.25 seconds, with
empty stderr. The test harness differs from E5 only in its expected helper hash.
The tests enforce the unaccessed exact DB descendants, strict sibling records,
append-only log prefix and identity, lexical/resolved write separation,
process/root identity mismatches, four verified backups and the owned adapter
environment. Main, protected rehearsal, real subprocess runner and adapter were
forbidden. No actual `/app`, DB, Git or network access occurred. These are fixture
checks, not a successful real-host rehearsal.

The raw results and hashes are retained in `ISOLATED_CHECKS.log`,
`static-verification.json` and `verification.json`.

## Preserved boundaries and remaining sequence

- The existing live DB subtree remains the only subtree whose descendants are
  neither accessed nor compared. No broader exclusion is added.
- The sibling log must retain the exact new-baseline identity and complete
  initial prefix. A later replacement, rotation, truncation or prefix mutation
  still fails. No uninterrupted log-prefix continuity across the reconnect is
  claimed.
- `.emergent/stripped_env_names` retains the original optional, same-file,
  prefix-preserving append rule. Its identity and prefix are captured anew by
  the reviewed operator; this proposal grants no broader metadata exception.
- A further process/root identity change fails. The helper does not discover a
  replacement PID, repin automatically, restart services or resume a failed run.
- The predecessor owner must complete original verification and close its
  existing lease. This task must not use that lease or execute its verifier as
  a shortcut, since successful verification removes the matching lease.
- Root must recheck production J and exact-B CI, obtain the authorized normal
  merge D and completed exact-D CI, and independently review a fresh ten-field
  envelope bound to D, the unchanged B/D tree, fresh initial host state and this
  exact `LIVE_SCOPE` value. The E5 envelope schema is unchanged.
- Before changing `/app` or starting the rehearsal, the original public guard
  must report inactive without an incomplete lease directory. All exact pins
  must still match. The helper also repeats these original checks internally.

Any eventual invocation requires `env -u PYTHONOPTIMIZE
PYTHONDONTWRITEBYTECODE=1 python -B` and the exact frozen helper/envelope bytes.
Setting `PYTHONOPTIMIZE=0` is still rejected. No lease, publication, source edit,
financial write, service operation or automatic rollback is included here.

## Git-only merge distinction

`AGENTS.md` explicitly gates `/app` changes and release rehearsals on an inactive
guard. It does not separately forbid a normal GitHub-only merge while another
lease is active. The original verifier reads the lease and deployed identity,
and does not compare remote HEAD. Nevertheless, the predecessor's original
`prepare` and `prepublish` require local/remote/leased deployment equality. If its
verification fails and J needs another publish, advancing remote production to
our D would obstruct that recovery. The recorded E4/E5 sequencing remains the
recommended coordination boundary, especially with the changed host state:
finish the predecessor's original verification and closure before merging.
