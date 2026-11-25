# Private canonical BSON capture for PR #2095

This task-only helper prepares an owner-bound canonical state and navigation
source capture for a private Preview. It does not start the application, import
any database, modify the source database, or manufacture missing financial
state. Use `--include-navigation-sources` for the reviewed full navigation
contract. A completed capture attests to source-copy scope and integrity;
genuine runtime behavior and performance still require separate verification.

No genuine source connection or capture is part of the automated tests.

## Reviewed source identity

The helper is scoped to candidate commit
`9a851854132bd71be922c4aae24b4cc20a546e41` in `AMASI-SA/AMASI-SA`.
`--archive-source` must point to its exact
`backend/operational_balance_archive.py`; the required file SHA-256 is
`a8e08fdd573fae56f184e1c10070bde994845f55bdb1ac9c81ba3dd46b833000`.

The loader checks that complete file hash, then compiles the unchanged ASTs of
`content_key`, `manifest`, `validated_events`, `unpack_issues`,
`unpack_financial_snapshot`, `_audit_metadata`, their constants, `ArchiveError`,
and the BSON/hash/zlib imports. The offline deadline supplies the existing
`_checkpoint` hook. It does not import the server, auth, dotenv, refresh workers,
or source code that performs writes. Page/packet validation logic is unchanged.

## Source scope

The default scope is one raw `operational_balance_states_v1` document with both
`_id` and `owner_id` equal to the explicitly supplied owner. All original BSON
bytes, field order, numeric types, binary subtypes, packed packets, and inline
audit events are preserved.

Only pages referenced by that document's `engine_audit_archive` manifest are
read from `operational_balance_audit_pages_v1`. Each page is read with its exact
ID and owner, validated against the manifest's count, offset, content hash and
order, then written without re-encoding. Unreferenced/orphan pages and other
owners' documents are not enumerated. Packed financial/diagnostic payloads are
unpacked and validated only in memory, with their original packed bytes saved.

`--include-owner-context` adds four explicit read scopes:

| Collection | Filter | Captured fields |
|---|---|---|
| `users` | exact owner `id` | ID, name, stored role/owner/active/status and stored access policy fields |
| `users` | exact `created_by`, role other than owner | the same positive projection |
| `mz2_atomic_owners` | exact owner `_id` | `_id`, `ledger_backend_state` |
| `settings` | exact owner `user_id` | `_id`, `user_id`, `mezan2_financial_cutover` |

The owner user must exist exactly once and have its stored role `owner`. Mongo
`_id` is preserved in every context scope. Owner
IDs and stored access/cutover policies are not remapped or invented.
`denied_permissions` is preserved when present for both owner and team users,
including the original explicit-denial semantics of the legacy movements read.
Missing control documents are recorded as zero matches; the later runtime must decide
whether that genuine absence satisfies its own preconditions. User email,
password, session, MFA and authentication token fields are never selected.

`--include-resource-directory` also requires `--include-owner-context`. It adds
source-reviewed positive identity/lifecycle projections for employees,
suppliers, drivers, external persons, financial/accounts/bank accounts, expense
categories and owner beneficiaries; typed courier definitions; projected
shipping tariffs and payment-method configuration; projected ad account,
binding and confirmation policies; and active `other_receivable` opening facts
needed for native POS identity validation. Actual financial amounts and evidence
needed by that typed opening contract are preserved. Integration-account rows
are always positively projected, never copied in full.

No CLI option accepts an arbitrary collection, query or projection. Expanding
the allowlist requires a separate source review and a new explicit scope.

### Full navigation source contract

`--include-navigation-sources` implies the owner and directory options and adds
the complete reviewed initial-navigation source graph. The 27 expected/forecast
collections include the 12 base cost sources, COD identity rows, bundle
definitions, frozen snapshots/lifecycles/plans/units, receipts, supplier proofs,
purchase invoices, liabilities and immutable journal/ledger/audit proofs.

For conditional financial proof collections, a bounded exact-owner corpus is
captured instead of fragile reconstructed ID joins. It is a semantic superset:
the candidate still applies its original queries, limits, timestamps and proof
validators. All hash-bearing financial proof rows retain original BSON bytes.
Duplicate logical IDs remain distinct original Mongo `_id` documents. No
financial correction collection, sequence counter, customer directory, auth
collection, provider token store or whole database is captured.

The same option adds reviewed positive fields for provider fees/statements and
settlement-file metadata, supplier invoice details, automation status, refresh
health/lease expiry, integration freshness/runs/error codes and Salla status.
No statement file content, lease capability, credential, error message or
provider traceback is selected. One explicit global exception is the fixed ads
scheduler status document, projected to `_id`, `enabled`, `interval_seconds`;
it carries no other owner's records and does not start a scheduler. Employee
opening-stock workflows and statement upload/confirmation actions are outside
this initial-navigation contract.

The `unified_orders` owner cohort is read once for forecast, labels, courier
discovery and root payment-method discovery. A fixed read-only aggregation
selects only reviewed financial root fields and BSON type witnesses. The helper
then applies positive child-field whitelists in memory. Arrays retain their
order/cardinality; scalar and null values stay their original BSON types;
missing/null raw-source parents remain distinct. Unknown scalar/array source
parents fail. If filtering would erase truthy shipment, tracking, refund,
payment-action or item evidence, the capture fails without a COMPLETE marker.
No fake placeholder replaces such evidence.

Option/customization values used by the candidate's line fingerprints are
retained exactly. App-produced current-shipping observations are validated
against the reviewed field vocabulary and preserved in full, including their
equality on outer/raw paths. Unexpected observation fields or contact/address
containers fail instead of being stripped. Order rows are the only BSON rows
re-encoded after positive subtree filtering; full financial proof rows are
never redacted or re-encoded.

`source_contract_v2.json` is a stable owner-neutral policy containing every
named scope, exact query/projection, binding class, output mode and order shape
policy. Its SHA-256 binds the manifest. Every scope has an actual cursor-complete
receipt with source count, captured count and configured row limit; a queried
zero is distinct from an unqueried collection. Full navigation flags become
true only after all required scope cursors finish and every validation passes.
Core-only and directory-only modes keep the corresponding completeness flags
false. The importer must verify the exact full policy hash, not only booleans or
collection presence. Separate implementation hashes bind both Python modules;
the coordinator must pin those hashes to the reviewed tool bundle.

All capture reads use one sequential `ClientSession(snapshot=True)`, including
state, pages, owner context and directory cursors. This requires a source that
supports MongoDB snapshot sessions. Unsupported snapshots, an expired snapshot
or any read failure abort; there is no fallback to mixed-time ordinary reads.
This is a read-only snapshot session, not a long-running write transaction.
The final state-head freshness check deliberately runs outside that session on
the primary. Both captured consistency and current-head equality must hold.

## Credential and transport handling

Run only from a user-controlled terminal with a functioning controlling TTY.
Both stdin and stderr must be terminals. The URI is accepted only by `getpass`;
an unavailable no-echo facility raises an error before input is read. Never put
the URI in a command argument, environment variable, shell history, config
file, redirected input, or chat. Do not use a terminal/session recorder that
records keystrokes.

The URI lives only in process memory and the driver's connection settings. It
is not saved, included in manifests, printed on failure, or passed to a child
process. Driver logging is disabled, warnings are suppressed, and errors expose
only a fixed error code plus a sanitized exception class name. An accidental
unknown URI CLI argument is rejected without echoing arguments. No traceback
is printed. A successful run prints only manifest hash and counts. Progress
contains stage and counts only, with an audit update every 64 pages.

Non-loopback sources require TLS: `mongodb+srv` keeps its secure default, while
an ordinary remote Mongo URI must explicitly enable TLS/SSL. Explicit TLS
disablement on remote hosts, contradictory TLS options, and certificate,
hostname, revocation or OCSP validation bypass options are refused. The helper
does not alter a URI to make an insecure source connect. Local loopback Mongo
may use its ordinary local transport. Reads are pinned to primary preference.

Use a source credential whose existing scope permits these reads; a read-only
role restricted to the intended database is preferable. The helper does not
probe roles, demand an unknown role name, elevate privileges, edit credentials,
or change auth/CORS. Its explicit Mongo application calls are `find_one`, `find`
with the closed projection/raw-financial allowlist, and one fixed order
aggregation containing only exact-owner `$match`, reviewed `$project`, and
bounded `$limit`. The same snapshot session binds every cursor. No caller can
supply pipeline stages; `$out`, `$merge` and write methods are absent.

The field scanner rejects recognized password/token/session/MFA/private-key
material even inside unpacked financial packets; it does not silently strip
fields from an otherwise faithful copy. This is a supplemental defense, not a
general proof that arbitrary unknown BSON cannot contain a secret. Only the
reviewed financial collections and positive projections are eligible.

## Invocation

Prepare the task-local environment using `requirements.txt`. From this folder:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python export_native_snapshot.py \
  --source-db ACTUAL_SOURCE_DATABASE \
  --owner ACTUAL_OWNER_ID \
  --archive-source /absolute/path/to/exact/backend/operational_balance_archive.py \
  --output-dir /absolute/private/parent/new-capture-directory \
  --include-navigation-sources \
  --min-revision 16947 \
  --require-active \
  --max-mib 1024 \
  --max-source-rows 50000 \
  --max-seconds 900
```

The placeholders are not credentials. Supply the actual database and unchanged
owner ID explicitly. Choose the minimum revision from an independently
observed genuine state; the number above is this task's earlier observed
revision, not a fixture generator. The state may advance. Revision and active
gates only reject unsuitable input; they never change it.

The parent directory must already exist, every path component must be a real
directory, and the capture path must not exist. Symlinks and existing output
paths are refused. A new directory and its `audit`/`context` subdirectories use
0700 permissions; all files use 0600, exclusive creation and no-follow opens.
Before hidden credential entry, the output filesystem must have the configured
payload budget, small-file allocation allowance and 256 MiB metadata headroom
available. Existing output directories are never reused after a failed run.

Default limits are 600 seconds, 512 MiB, 4,096 pages, 16 MiB per BSON document,
250,000 total captured documents, and 5 million archived events. The total
document cap also bounds accumulated manifest/identity metadata in memory.
Maximum configurable limits are 1,800 seconds,
2 GiB and 8,192 pages. New financial/metadata scopes default to 50,000 rows each
and may be capped explicitly up to 200,000; owner/settings singletons, team and
directory scopes retain their narrower caps. These export caps do not change
the application's original query limits. Overflow fails instead of truncating.
Each source network operation is bounded by the
remaining deadline and at most 15 seconds. Human time entering the hidden URI
does not count against the capture deadline. No concurrent source requests run.

## Complete artifact contract and import handoff

`state.bson` and `audit/000000.bson`, etc. contain source BSON unchanged.
`context/*.bson` contains raw financial proof BSON, unchanged positive-projection
BSON, or the explicitly labeled financial-only order subset. Files are streamed
individually; the full audit history is not assembled in memory.

The private `manifest.json` records schema version 2, kind
`mezan_native_bson_snapshot`, source commit/validator hash, source database,
unchanged owner ID and its hash, state hash/size/revision, ordered page
IDs/hashes/sizes/offsets/counts, exact scope/projection/shape receipts, semantic
contract hash, tool implementation hashes, consistency and completeness limits.
Per-file `source_sha256` hashes the actual source read response. For orders,
that is the financial-root aggregation response including private type
witnesses, which are removed before the output BSON is saved. `audit.events` counts archived events;
`inline_events` and `total_events` also account for the state document's tail.

After copying and validation, the source state is re-read using the same exact
owner filter. Both its complete raw BSON SHA-256 and revision must equal the
first read. A concurrent state change fails the capture; it is not retried
against a mixed revision. Immutable page content hashes protect the page copy.

`COMPLETE.json` uses schema version 2, is written and fsynced last, and binds the SHA-256 of the complete
manifest bytes. `INCOMPLETE` is then removed. A crash may leave both markers;
only verification of `COMPLETE.json`, manifest hash and every referenced file
can establish completeness. A manifest alone, an `INCOMPLETE` directory, or an
unverified COMPLETE file is never importable. Partial private files remain for
manual disposition; the helper does not delete or reuse them.

An importer must independently verify the contract, all hashes/paths/counts,
owner and source identity, and the required scope flags before any writes. It
must target a fresh isolated task database on the approved loopback port, not
the source or a shared database. Group supplemental scopes by collection: the
owner and team are separate disjoint `users` scopes and must both be imported.
Settings is captured once using a unified reviewed projection, so no last-write
field merging is needed. Repeated source `_id` values within a collection across
scopes are rejected by the exporter and must also be rejected by the importer;
never overwrite one projected document with another. A destination binding marker must be minted
by that importer only in the task database after a verified import. This
exporter creates no source marker, destination database or importer.

Treat the capture, manifest, source database identity, owner ID, and financial
BSON as private task data. Do not put them in GitHub, public evidence, logs,
ordinary reports or chat. Only sanitized helper code and synthetic test results
are suitable for published review. This directory's virtual environment is not
part of a deliverable or repository change.

## Offline tests

```sh
.venv/bin/python -m unittest -v test_export_native_snapshot.py
```

By default the tests read the exact validator file in the sibling
`mezan-navigation` checkout. A different local exact file can be supplied via
`PR2095_TEST_ARCHIVE_SOURCE` (a source-code path only; no credentials).

The tests use a fake read boundary and independently constructed synthetic
packed BSON/audit hashes. They cover byte fidelity, ordered/owner-bound page
selection, ignored orphan pages, packet/page corruption, state changes at equal
or changed revision, secret-bearing compressed data, positive/nested field
projections, duplicate/owner failures, all-scope receipts, unchanged immutable
proof bytes, original logical duplicates, missing/null distinctions, safe
financial subtree projection, exact option/current-shipping values, a
regression executed against the candidate's own COD payment calculation,
count/byte/time limits, revision/active
gates, no fixture fallback, restrictive files, existing-path/symlink refusal,
validator mismatch, no-TTY/no-echo refusal, safe error/argument output, TLS
transport constraints, shared snapshot binding, a current non-snapshot head
read, and failure without a snapshot fallback. They do not establish live reachability, source role
permissions, genuine data completeness, importer correctness or runtime parity.
