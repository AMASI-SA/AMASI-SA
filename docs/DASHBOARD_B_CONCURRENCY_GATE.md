# Dashboard B concurrency gate — runtime adoption BLOCKED

Scope: correctness characterization only. The approved experiment at fa311db8
is unchanged. No runtime, formulas, filters, governor, cache or transaction change.

## Deterministic schedule

The isolated MongoDB8.0.12 replica-set test uses separate reader/writer connections
and asyncio events, not sleeps, to establish:

Current: legacy normalized-order read completes with old rows -> independent
fixture mutation commits/acknowledges -> V2 normalized-order read starts and can
observe new rows. V2 is explicitly held until that acknowledgement regardless
of Motor scheduling. Shared: its single load completes with old rows -> the same
mutation acknowledges -> consumers use that load, with no second normalized read.
Raw currency/attribution proof remains a separate later read in both designs.

For Google advertising, mutation occurs after the first daily_costs read and
before the later V2 daily_costs read; both designs still execute those two reads.
For cost-profile mutation, the write occurs between the normalized-order read
points; the authoritative product-cost profile itself is read once later in each
design. We do not claim that profile was formerly loaded twice.

The handler retains a read-only DB facade and command listener asserting zero
writes. Fixture setup and the explicit writer affect only UUID test databases.
The artifact records full Current/Shared JSON, all differing paths, queries,
returned rows, actor, and monotonic timestamps for read/write ordering.

## Cases / observed local results (CI must reproduce)

Fixture: one completed SAR100 order, product cost37.5, Google spend10, report
filter includes completed. Values below are Current -> Shared.

|Mutation between reads|Current|Shared|Significance|
|---|---|---|---|
|status completed->cancelled|0 orders / SAR0|1 order / SAR100|Business-significant|
|payment+status, mada filter|0 /0|1 /100|Business-significant|
|shipping+status, carrier filter|0 /0|1 /100|Business-significant|
|payment alone, mada filter|0 /0|1 /100|Business-significant|
|shipping alone, carrier filter|0 /0|1 /100|Business-significant|
|profile base cost37.5->55|cost55|cost55|No difference; later profile read retained|
|embedded order cost37.5->60|cost37.5|cost37.5|No JSON difference in this authoritative-profile fixture|
|Google input10->40 between ad reads|ads40|ads40|No difference; second ad read retained|
|order date moved outside range|0 /0|1 /100|Business-significant|
|internal note not used in output|identical full JSON|identical full JSON|No business effect|

Status/date divergence also changes cost0->37.5 and net_profit -12.3->50.2.
This does NOT mean Current is a coherent snapshot: its legacy fees/reference
values can remain old while V2 totals are new. Shared loses an existing window
for observing a newer update, and is not a full snapshot either (proof, cost,
ads and settings may still be read later). Treating either as globally consistent
would be incorrect. These are controlled schedules, not Production traces or
proof of every possible interleaving.

## Design decision required BEFORE runtime adoption

Recommended least semantic change: preserve separate database reads and their
existing observation boundaries. Retain only candidate computation reuse that
is proven to use identical already-materialized in-memory inputs. This gives up
the claimed Mongo read reduction; any revised experiment requires fresh evidence.
No such revision is implemented in this gate.

If shared database reads are desired, explicitly approve a new per-request
read-consistency contract first: one agreed read boundary for settings, normalized
orders, raw FX/attribution, status policies, catalog/profiles/bindings/resources,
ads and other financial inputs. A read-only snapshot/session design must propagate
to every relevant query; putting only order reads in a transaction would not
establish whole-response consistency. Define visibility of writes after that
boundary, error/timeout behavior without silently switching snapshots, and test
each crossing write plus large-dataset snapshot lifetime/resource cost. Select
the concrete transaction/session mechanism only after that design review.

No transaction, snapshot session, runtime adoption, extra optimization, C/E work,
merge, prepare, deploy or Production access is authorized/performed here.
The gate test expects and proves divergence: pytest PASS is successful diagnosis,
NOT runtime correctness approval. Production writes=0.
