# Final Stage10 provenance P2 — 2026-10-03

Scope: existing Opening catalogue read/provenance and refresh-response ordering only.
Starting HEAD: 2ad8e17dbcf0b456de0a618237cf83d8bc63d768.
PR: #1247, Draft; original comparison base78dcf31af73581ceba3677c464c657a0b9b2c4fc.

## Cause and precedence

The catalogue previously always preferred retained detail data for its explicit-empty text-customization proof. A newer light source could introduce stock options while the old normalized text option and empty detail variants remained. The UI inventory refresh also allowed an older request's success/error to overwrite the latest request.

The read resolver now:
- compares timezone-aware provider updated_at/date_updated timestamps when both sources are comparable;
- never uses response arrival/last_synced_at as proof of source revision;
- uses the winning source's options and variant identities together, without mixing stale normalized combinations into it;
- fails closed for contradictory sources with missing, malformed or equal revision dates;
- treats missing fields as incomplete evidence, not removal; explicit current options=[] and variants=[] can prove a plain product;
- preserves mandatory combinations when older raw evidence contains stock options/variants, including when normalized fields were defaulted to empty;
- still rejects absent/duplicate/invalid canonical variant IDs; no position/index is synthesized;
- makes no database write and does not change sync producers, inventory initialization or financial writers.

The frontend uses a request sequence to accept only the latest catalogue response, and invalidates pending requests on unmount. Old errors cannot replace a newer ready state. Existing drafts survive repeated refresh/recovery.

## Acceptance evidence

| Case | Fresh focused result |
| --- | --- |
|1. Plain product without options |PASS; no invented combinations|
|2. Explicitly proved text customization |PASS; no stock variants required|
|3. Stock options with canonical variants |PASS; canonical IDs retained|
|4. Stock options, missing variants |PASS; fail closed|
|5. Older empty snapshot versus fresh stock |PASS in both source orders; unorderable conflicts fail closed|
|6. Variant missing/invalid ID |PASS; never use index|
|7. Older response after fresh response |PASS; deferred success and error tests preserve latest state|
|Mixed option types, partial source/removal, timezone offsets, repeated reads/recovery |PASS|

Before repair, the new backend selection exposed8 failing regressions and frontend stale success/error tests exposed2 failures. Final local checks:
- backend focused catalogue/full-details/inventory-draft/real-Mongo:64 PASS.
- frontend Opening/context/inventory/review/currency focused:208 PASS /18 suites.
- git diff --check:PASS.
- Real Mongo: new loopback-only replica set mz2stage10 at127.0.0.1:27159, UUID disposable DB; actual HTTP catalogue reads repeated, tenant isolation retained and full collection fingerprints unchanged.
- Source/read tests include actual details/light normalization producers; no production call or data used.

Full affected CI must be read on the new committed exact HEAD. Its immutable job matrix, final HEAD/TREE and artifact links are recorded in PR #1247 and Issue #1006; prior-head CI is not substituted.
Local evidence: D:/CodexAcceptance/mz2-stage10-provenance-20261003/ (focused-backend.xml, focused-frontend.json).

## Boundaries

Accounting writers, ledger/journal semantics, C3, Track F,409/423,write-control, general Opening, Inventory Initialization, Activation/P08 and G47 sequencing unchanged.
No component SKU or identity introduced. No BUILD37 merge/rebase/runtime additions.
Existing C remains held. Full Business UAT is not PASS.
Live browser is not established by isolated tests; report actual tool availability separately.
Production financial writes=0. Opening=NO. Inventory Initialization=NO. Activation=NO. Backfill=NO. Merge=NO. Deploy=NO.
