# AMASI_READY_CANONICAL_STATUS_RACE — FINAL_FIX

Decision: **BLOCKED**. The local canonical-writer/Ready snapshot race is repaired,
but the independent PR1321 compatibility experiment demonstrates unsafe external
effects and stale shipping publication after canonical `delivered`. This patch
is not permission to merge, publish, prepare a release, or call a live provider.

## Immutable inputs and isolation

- Freshly fetched Production base: `094d0ef7fa001d3b3794f2600c77795190ddb1a8`.
- PR1300 diagnostic HEAD: `617858e5d327d7c1cd898e73ef6aa12e726e567f`.
- PR1321 compatibility HEAD: `c036f8a969ef4e2835e2ebcf623305d89ca22af6`.
- PR1305 compatibility HEAD: `d187dfbb729923f491f6ce3a106dde043e2b890c`.
- New branch: `codex/ready-canonical-status-race-final`; existing branches untouched.
- New MongoDB 8.0.12 replica set `readyRace`, loopback port 27941, new empty data
  directory, unique synthetic databases per test. No production URI or fallback.
- Authentication/catalog enrichment and provider transport use the original
  test doubles; stock, owner serialization, transaction retries and rollback are
  real. No live Salla/Qoyod calls, deployment, release intent or financial writes.

## Root cause and writer inventory

`operational_owner` first writes the merchant serialization document, then runs
Ready under snapshot/majority isolation. A second read in that same transaction
cannot see a newer commit by a writer which never touches that serialization row.

| Runtime writer | Previous protection | This patch |
| --- | --- | --- |
| `orders_db.upsert_order` | Owner only when shipping observation exists | Always owner; read/merge/write all inside callback |
| `salla_integration.auto_sync._reconcile_status_page` | Direct root/raw status update | Owner per changed order; re-read current row and skip if status changed since page inspection |
| `qoyod_auto_unified.live_source._promote_snapshot_to_unified` | Direct root status/slug update | Read latest inbox snapshot and promote inside owner |
| Verified order webhook and Order Engine authoritative refresh | `persist_component_source_snapshot` already owns transaction | Nested upsert joins the same scope; no extra lock |
| Make, custom app, Excel import, bulk Salla sync, single resync, discovery, commerce enrichment | Inherit upsert's conditional protection | Inherit unconditional owner protection |

Even status-less upserts need the fence: `_upsert_order` performs `$set` of the
whole merged document and otherwise can replay old canonical fields. The
existing shipping CAS is retained. No new lock, migration, global status revision
or provider call is introduced. Existing catalog/Tamara metadata capability
restrictions remain enforced. Standalone Mongo is intentionally unsupported.

Other inspected direct writes update shipping/read/gift/payment metadata rather
than canonical status. The startup legacy migration inserts old rows; it is not
an existing-row status update. Maintenance scripts are not execution eligibility
authorities and must not be run concurrently with live business operations.

## Positive Ready policy

The same small status gate already reviewed in PR1321 is included independently:
Ready requires positive `in_progress` evidence (including the canonical Arabic
name), rejects conflicting mapped status and preserves historical component
evidence when presentation mapping is unavailable. Search and save use the same
gate. The matching three test-fixture changes model the subsequent provider
execution transition explicitly; inventory/transaction assertions are preserved.

No asynchronous-delivery implementation or performance changes from PR1321 were
merged into this branch. Its outbox file is absent from the Production base.
Printing's completed-only policy and immediate Ready acknowledgement are tested
as compatibility requirements on those independent proposals; this standalone
writer patch cannot claim to deliver their whole behavior.

## Original real-Mongo counterexample

The original physical and virtual reproduction scripts were read and rerun on
the exact PR1300 HEAD against the new replica set. See the two original JSON
artifacts in this directory. Both returned HTTP 200 after:

1. Ready read `in_progress` inside the transaction;
2. status-only `upsert_order` committed `delivered`;
3. a fresh canonical read saw `delivered`;
4. a second transaction read still saw `in_progress`.

The physical case consumed stock **20 → 18** and persisted piece/component/event
changes. The virtual case persisted ready/completion. Shipping was mocked.

`test_g47_ready_canonical_status_race.py` uses independent ContextVar contexts for
concurrent requests and the actual endpoint/writers. Its 21 cases cover:

- delivered/cancelled before Ready for physical and virtual pieces, through all
  three writers: 409, identical business collections, stock remains 20;
- Ready holds owner first: all three writers stay uncommitted, Ready sees
  in_progress and commits first; delivered commits later and a repeat Ready
  rejects without additional stock/progress changes;
- status writer holds owner first: Ready cannot finish until delivered commits,
  then rejects with stock 20 and no partial business writes;
- two different writers contend on the same owner and both serialize.

The allowed Ready-first case is linearization, not a claim that an event which
arrived during Ready must retroactively undo an already authorized transaction.
Stock becomes 18 exactly once for a physical piece, and stays 20 for a virtual
piece. The existing G47 rollback suites also remain in CI. The new suite is named
`test_g47*` so the existing real-replica CI and zero-skip gate execute it.

## Independent compatibility and confirmed remaining blocker

Both compatibility worktrees are detached at the exact PR heads above. Only the
three-writer patch and the new test module are overlaid; no PR branch is changed.
PR1305 uses an older `seed_execution_status` fixture helper; the test imports that
helper when present without changing its assertions or production implementation.
The first PR1305 run failed at fixture setup because this helper was missing from
the borrowed fixture methods; it is not counted as a product failure or a pass.

On PR1321 plus the writer patch, the five existing tests for immediate durable
Ready, atomic outbox rollback, lost-response status/AWB dedupe, and harmless
revision changes pass. These passing tests do **not** cover the newly demonstrated
status-only interleavings.

Run `outbox_counterexample.py` from that compatibility backend with its tests on
PYTHONPATH and the isolated `MZ2_TEST_MONGO_URI`. `outbox-counterexample.jsonl`
records fresh real-Mongo results, with only provider transport mocked:

| After canonical delivered commits | Observed result |
| --- | --- |
| During initial provider status read | Mock POST `/orders/internal/status`, final ready=true |
| During draft shipment read | Mock POST `/shipments`, final ready=true |
| During label refresh | No POST, but stale ready=true and outbox confirmed |

`source_fingerprint` deliberately excludes status to tolerate the expected
in_progress → completed transition. `guard_effect` checks material/component
evidence but not current canonical status; the status-only owner writers leave
that material evidence unchanged. **Owner serialization alone cannot repair this
separate outbox authorization gap.** The diagnostic's zero exit means the
experiment ran, never safety acceptance.

The next integration must add an explicit canonical-status check inside the
outbox guard for both effects and publication, including legacy readback. It must
preserve monotonic attempt flags, the current-revision CAS and material evidence;
normal completion must not be stranded by harmless revision increments. This
requires an integration patch against PR1321's absent-on-base outbox, not silently
copying or changing the user's independent performance PR.

Even that check leaves a gap after the final local transaction and before the
external POST (including auth/rate-limit awaits inside the client). A repeated
GET cannot prove a global atomic condition. Do not put provider POST inside a
Mongo callback which `with_transaction` may retry. A strict remote ordering
guarantee requires provider-side conditional mutation or an explicit dispatch
protocol; neither is established here.

## Remaining limits and CI

- Increased merchant-wide owner contention for status-only and non-shipping
  imports is expected. Concurrent writer/Ready behavior is tested; Production
  throughput/p95 and high-volume bulk-import load are **unverified**.
- Serialization orders local commits, not provider event freshness. A stale page
  fetched before the local inspection or an out-of-order source event can still
  regress status where the existing intake lacks an authoritative version check.
  The added local re-read protects changes after page inspection only.
- Full-document upsert still has shipping CAS, not a universal CAS for every
  unrelated metadata writer. No claim of global metadata linearizability.
- These are isolated local results, not Preview or Production acceptance.
- Final source HEAD/TREE and CI results belong in the Draft PR's final evidence
  section after remote completion. Pending/skipped jobs are not PASS, and green
  CI cannot override the demonstrated outbox blocker.

Rollback is a source revert only. No deployment, release lease, schema changes,
data repair or destructive cleanup was performed.

## Executed local result checkpoint

- Source race suite: **21 passed, 0 skipped**, split into 7 Ready-first/two-writer,
  6 virtual-before, and 8 physical-before/writer-first cases (retained XML).
- Current-shipping regression suite: **35 passed** using the repository's async
  fixture mode, `--asyncio-mode=auto`. An initial invocation omitted that mode
  and had 26 setup errors; its 9 partial passes were not counted as acceptance.
- PR1305 + writer patch: **8 passed** (upsert races and writer-first transactions).
- PR1321 + writer patch: **4 passed** (upsert races and writer-first transactions).
- PR1321 existing outbox regression subset: **5 passed** (durable immediate Ready,
  rollback, both uncertain POST replay protections, harmless revision changes).
- PR1321 new outbox counterexamples: **3 unsafe scenarios reproduced**; BLOCKED.

The source suite command is `python -m pytest --noconftest -p no:cacheprovider -q
--tb=short tests/test_g47_ready_canonical_status_race.py` from `backend`, with
`MZ2_TEST_MONGO_URI` explicitly set to the disposable loopback replica set.
Compatibility tests use the same command/test module on their pinned worktree.
