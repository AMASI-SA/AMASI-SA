# PR #2122 live-scope operator — reviewed, awaiting deployment prerequisites

Independent review of this minimal adaptation passed with no remaining code
blockers, and the root coordinator reviewed the complete diff and main flow.
This E5 evidence checkpoint preserves the reviewed helper and its six passing
fixture groups. It is not an execution, merge or publication record. The root
coordinator retains control of the remaining deployment prerequisites.

No dashboard execution envelope has been created. The predecessor's
[immutable E7 envelope](https://github.com/AMASI-SA/AMASI-SA/blob/a6853cf13af7a64fe2443eea588de12f43744066/docs/operations/INVENTORY-CABINET-ARCHIVE-20261124/cabinet_archive_release_envelope_live_scope.json)
is a reference for another release and must never be used for this dashboard
release. The prior E1 helper remains historical and must not run.

## Immutable upstream and minimal adaptation

The source is the predecessor E7 commit
`a6853cf13af7a64fe2443eea588de12f43744066`, path
`docs/operations/INVENTORY-CABINET-ARCHIVE-20261124/cabinet_archive_release_operator_live_scope.py`.
Its 43,560 bytes match SHA256
`1c366e6add99c1560243e53660a34df8c4121ead56c1246656dfb9afc0a7f14f`.
The upstream 1,366-byte envelope matches SHA256
`b06bd5ecb519f4f485e959c0006ad6baf31e77dd911f6599382ef82c8113c9c5`.
The upstream review records both independent preservation reviews and AST
validation as passed. It does not supply isolated behavioral-test results.

The adapted `pr2122_release_operator_live_scope.py` has 43,582 bytes and SHA256
`62e545a2b11520f121aebf0a985b0854134052070ff7355892fcec0f95eb72c7`.
`adaptation.diff` contains the entire difference: five exact identity literals
and four task-label strings (module description, receipt task, private stage
prefix and guard import name). No executable control flow changes.

| Constant | Reviewed dashboard value |
| --- | --- |
| `A` | `10bb33957c8c2994814a76ba5feed1182336ebda` |
| `J` | `f199a471f8bc6e05b475bb9b65a12107cbdc946b` |
| `B` | `c3fd1d1a4e3e7fc590b1f5b82d1bf0a091c54bf7` |
| `TREE` | `fa0f36e01d3c3a706a0cbdb23aa1cfcd0b8f09a1` |
| `RELEASE_ID` | `rg5-71a053d9aff1c8c4dec4f5b5e9171630b8c53bf6fea6d3516427bd7836c4ba9c` |

The exact identity equality check is preserved. The normalized AST is identical
after reversing those nine substitutions, and the complete `LIVE_SCOPE` source
is byte-identical. The original guard/lock, Git controls, capacity accounting,
owned clone, backups/fsync, timeouts, canonical adapter, package/proof checks,
foreign preservation and failure behavior remain unchanged.

## Unchanged execution-envelope schema

The actual envelope must be a regular, nonsymlink file, no larger than 16,384
bytes, containing a JSON object with exactly these ten keys and no extras.
This table documents the schema; it is not an executable envelope.

| Field | Original validation / required dashboard value |
| --- | --- |
| `source_sha` | Lowercase 40-hex string, exactly the reviewed `A` above |
| `source_base_sha` | Lowercase 40-hex string, exactly `J` above |
| `intent_sha` | Lowercase 40-hex string, exactly `B` above |
| `deployment_sha` | Actual future D, lowercase 40-hex; distinct from J, A and initial head; task requires the reviewed normal merge and completed exact-D CI |
| `tree` | Lowercase 40-hex string, exactly `TREE` above; B and D must have this tree |
| `release_id` | Exactly `RELEASE_ID` above |
| `initial_head` | Freshly verified starting host SHA; for the intended sequence, J after the preceding publisher completes |
| `expected_untracked_count` | Exact integer from 0 through 100,000, matching the fresh nonignored Git inventory; do not reuse the predecessor's historical count |
| `allow_runtime_metadata_append` | JSON boolean, independently reviewed against current platform metadata; when enabled, only the same-file original prefix may append |
| `live_foreign_scope` | Exact JSON value of the unchanged `LIVE_SCOPE` constant; equality is checked using canonical key ordering |

`LIVE_SCOPE` pins the exact `.worktrees/p01-acceptance-state-20260919/mongo`
data root, its real parent/root identities, Mongo PID/start and executable and
command-line identities, plus sibling `mongo.log` identity and append-only
policy. Descendant database bytes and paths are deliberately neither accessed
nor compared. The log's complete original prefix is hashed through bounded
reads with `O_NOFOLLOW`; rotation, replacement, truncation and prefix mutation
fail. All other ignored/nonignored foreign paths and records remain exact.
Lexical and resolved overlap with operator/source/adapter write targets fails.

The exact upstream v2 code does not revalidate the E6-observed supervisord
PID/start/fd. It validates Mongo identity plus the independent log's identity
and prefix. No supervisor-writer check has been added or claimed by this
adaptation. Fresh matching process/root/log evidence remains required before
execution; changed identities require a reviewed new boundary, never automatic
substitution or a broader exclusion.

## Verification performed locally

`static-verification.json` records the complete nine-literal restriction, AST
parse, normalized AST equality, unchanged live-scope source and exact hashes.
Six focused fixture groups passed, exit 0, in 0.366 seconds:

1. The exact live database subtree is never traversed or read; live fixture
   changes and log appends pass, while sibling changes/additions fail.
2. Existing log-prefix mutation, truncation and byte-identical inode
   replacement fail.
3. Lexical ancestor/descendant and resolved symlink write collisions fail;
   similarly named separate siblings remain outside the live scope.
4. Synthetic process/start, command, executable, directory and log identity
   mismatches fail without reading actual process or database state.
5. All four capacity-checked generated-output backups preserve source bytes,
   modes, literal symlinks and fsynced receipts/stage entries.
6. The adapter environment strips inherited GitHub/bootstrap markers and uses
   its owned cache/tmp with the reviewed public origin.

The harness loads the helper under a non-main name, substitutes temporary
fixtures and fails on any attempt to invoke main, the protected rehearsal,
subprocess runner or adapter. No Git/network, actual `/app`, database or service
operation occurred. These checks do not claim an actual clean-clone or host
adapter rehearsal. The raw results are in `ISOLATED_CHECKS.log`, with exact
helper/test/log hashes in `verification.json`.

## Invocation change from historical v1

The unchanged v2 gate requires `sys.flags.optimize == 0` and rejects any
presence of `PYTHONOPTIMIZE`, including the value `0`. This supersedes all
earlier E1/E3/E4 advice requiring `PYTHONOPTIMIZE=0` for this successor.
After the preceding publisher completes publication, original verification
and lease closure, the root must recheck the production base and exact-B CI,
obtain the normal merge D and passing exact-D CI, and bind a fresh reviewed
envelope to current host observations. Only then may the required form run:

```bash
cd /app
env -u PYTHONOPTIMIZE PYTHONDONTWRITEBYTECODE=1 python -B \
  "$PROFIT_OPERATOR_DIR/pr2122_release_operator_live_scope.py" \
  "$PROFIT_OPERATOR_DIR/reviewed-envelope.json"
```

The private directory and envelope must already exist and have verified exact
bytes before this eventual command. There is no automatic retry, rollback,
lease preparation, publication, service stop/restart or database operation.
