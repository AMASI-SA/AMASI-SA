# Private bundle item view: adoption evidence

## Scope and decision

The authorized change removes complete native-item copies from the private operational collector's bundle preparation when the item is an exact, acyclic JSON tree. It adds no query, projection, cohort restriction, cache, retry or deadline change. The baseline for the experiment and pre-change runtime is source `7b4da05fba49d7b2e923ae3ac304af7e15dbcaa6`.

Only two runtime files change: `backend/operational_remaining_bundle_sources.py` implements the private view and preserves the public/default copy path; the existing call in `backend/operational_balance_sources.py` opts in with `_read_only_items=True`. The new regression module is `backend/tests/test_operational_balance_bundle_item_view.py`, which matches the existing strict Web/Stock `test_operational_balance*.py` selection.

The guard accepts exact dictionaries/lists, exact string keys, and exact JSON scalar types including only finite floats. Repeated containers, cycles, subclasses, custom objects and non-JSON BSON/Python values use the original `deepcopy` at the original call point. The unchanged option normalization still runs first. Each accepted item receives a new root; untouched nested native branches are borrowed internally. Normalized option/raw-option and custom-field structures retain their existing construction/copy behavior. An AST comparison confirms the extracted normalization body is identical after substituting its copy-function parameter for `deepcopy` and excluding the original docstring. `git diff --check` passed.

## Ownership and lifetime boundary

This private opt-in is supported only for the collector's freshly materialized, exclusively owned native owner-cursor rows. Production server and demand-reader clients use standard Motor/BSON decoding without a custom document class, `CodecOptions`, or `TypeRegistry`. Such decoded rows cannot introduce Python copy hooks or shared containers across BSON documents. The primary-ready callback receives no rows; concurrent profit work fetches a separate cohort. The collector awaits the bundle adapter before its subsequent source loop accesses these local rows.

The authority and selection consumers read the normalized items. Economic/fingerprint transformations retain their existing copies. Receipt mapping copies its snapshot and units before changes. Final bundle cost and issue results are newly constructed and do not retain native-item containers. Public `normalized_orders` and default `load_remaining_bundles` keep complete item-copy behavior; other callers remain on that default.

Normalization remains synchronous inside async `load_remaining_bundles`. This change does not move it into a preparation thread or introduce cancellation checkpoints. The authority-await regression proves cancellation of the current task, execution of its cleanup, unchanged input and confinement of the borrowed view. No whole-cohort snapshot, admission cache or graph retention is added. The private contract does not extend to arbitrary injected in-memory cohorts, hooks that mutate unrelated input rows, or hooks that monkeypatch module globals. Local unsupported-item fallback is not presented as a general concurrency or hostile-hook guarantee.

## Focused red/green verification

All commands ran from `/workspace/scratch/1f2d9548e6ff/mezan-refresh-source-root-fix` using the existing venv, with no dependency installs or live database access.

The red control added only the initial work-count regression before changing the A runtime. It exercised actual `collect_sources` on eight synthetic orders, first establishing the expected cost/output and input preservation, then failing because all eight full item copies still occurred. This was the unchanged A runtime with a newly added test and documentation work in the checkout, not a claim that the whole checkout was clean.

```bash
PYTHONPATH=backend:backend/tests PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 /workspace/scratch/39c6dec7c37f/profit-test-venv/bin/python -B -m pytest --noconftest -p no:cacheprovider backend/tests/test_operational_balance_bundle_item_view.py -q --junitxml=/workspace/scratch/1f2d9548e6ff/completion-release-prep/bundle-profile/adoption-red.xml
```

Result: exit 1, **1 failed in 2.43 s**, zero errors/skips. The failure was the intended eight unexpected complete-item copies.

The initially completed module passed 34 cases in 3.21 s. Independent review then requested explicit admission/fallback evidence in the populated bundle fixtures and positive supplier-obligation assertions. Those narrow assertions were added without runtime changes, followed by one final focused run:

```bash
PYTHONPATH=backend:backend/tests PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 /workspace/scratch/39c6dec7c37f/profit-test-venv/bin/python -B -m pytest --noconftest -p no:cacheprovider backend/tests/test_operational_balance_bundle_item_view.py -q --junitxml=/workspace/scratch/1f2d9548e6ff/completion-release-prep/bundle-profile/adoption-focused.xml
```

Result: exit 0, **34 passed in 2.99 s**, zero errors/skips. The cases cover:

- Public/default full-copy behavior; private independent overrides; original option-validation/error order; malformed IDs/items; alias/cycle/type/custom-hook fallback.
- Populated physical receipts and frozen fingerprints: whole default/private output and query equality, independently mutable returned results, and preserved failures for changed selection/variant/invoice/receipt owner.
- Populated mapped-financial bundles: external supplier and service receipts, future/foreign piece behavior, and the same full output/query/input preservation.
- Explicit observation at the real authority boundary: valid populated private cases borrow their native metadata and perform zero complete item copies; the alias case performs one complete copy, preserves its internal alias, and has independent metadata. The default comparison always performs one complete item copy.
- Complete collector/reducer/replay parity with positive supplier obligations of **SAR 180 expected and unassigned**, plus **SAR 80 expected for `external-supplier`**, all with confirmed `0.00`; no financial movements or engine facts are manufactured.
- Cancellation during the authority await: the current task is joined, cleanup runs, no orphan task remains, and the raw source is unchanged.

The populated tests use isolated `AsyncMongoMockClient` data and existing real financial preparation/authority/reducer code. They are not real-Mongo integration evidence. No broad local rerun or further timing sweep was performed; final exact-source CI remains required.

## Single prototype pair and limits

Before adoption, the reviewed script was checkpointed at evidence commit `514e0ea76cbffae96dfbce58bc189ecc2bd8716c`; the result and compressed raw artifacts were subsequently checkpointed by the release owner at `1d2ca5443885635f95f2b8d2efd3ae285db6c16a`.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONHASHSEED=0 /workspace/scratch/39c6dec7c37f/profit-test-venv/bin/python -B /workspace/scratch/1f2d9548e6ff/completion-release-prep/bundle-profile/measure_private_bundle_item_view.py --output-dir /workspace/scratch/1f2d9548e6ff/completion-release-prep/bundle-profile/run
```

Exactly one fresh baseline process and one fresh private-view process ran, without a collector warm-up or repeated sweep; exit 0 and both children joined. The synthetic cohort contained 19,151 orders / 76,604 items and 92,268,810 encoded order-source bytes. Fixture construction and input/output hashing were outside the measured collector window. GC remained enabled, with an explicit untimed collection before each collector.

| Measured work | Baseline | Private prototype |
| --- | ---: | ---: |
| Whole collector wall | 27,274.038 ms | 24,006.110 ms |
| Process CPU | 27,712.823 ms | 24,587.306 ms |
| Bundle normalization | 12,352.540 ms | 8,041.561 ms |
| Bundle authority | 548.265 ms | 2,061.319 ms |
| Total bundle stage | 13,359.167 ms | 10,388.357 ms |
| Full native-item deepcopies | 76,604 | 0 |
| GC wall | 4,268.406 ms | 2,877.617 ms |
| Whole-process peak RSS | 942,020 KiB | 719,188 KiB |

The selected pair reduced bundle normalization by **4,310.979 ms (34.9%)** and whole collector wall by **3,267.928 ms (12.0%)**. It avoided 1,551,228 nested container clones while examining at most 22 containers per item. Whole-process RSS was 217.61 MiB lower, but includes imports and fixture construction and is not an isolated allocation or production-memory claim.

Complete value and serialized outputs, query sequences, input/source hashes and original state were equal/preserved; 1,374 runtime-file hashes were unchanged. The shared output was 10,593,735 pickle bytes, SHA-256 `23e16abfbe0855486632c08b70d2dc71008febd99c30c55c88282104949a6339`; the input digest was `c08140d7a748e979d6cb11c406ee43120c662ae5f576fbd2dce8b577e521c24e`; the query-sequence digest was `ada563f25ef95250e3c6ea92a65c71054d149d68256f79fd1f7a872a28071cf1`.

Authority time increased in this pair and unchanged option-normalization time also varied. Inclusive stages overlap and cannot be added or all attributed to the changed copy operation. The large timing fixture had no selected physical/financial bundle history; the populated adoption regressions address correctness of those paths, not their live frequency or performance. The pair used no real Mongo transport, concurrent native/profit workload, reconciliation or financial save. Its 85 s bound applied to the synthetic source call, not the whole production request. It does not establish that the live 85 s refresh will now complete or that historical production entries have been preserved; successful live save and preservation acceptance remain necessary.

## Frozen file hashes

Runtime/test paths below are relative to the checkout. Evidence paths are relative to this report's original directory, `/workspace/scratch/1f2d9548e6ff/completion-release-prep/bundle-profile`.

| File | SHA-256 |
| --- | --- |
| `backend/operational_remaining_bundle_sources.py` | `8ca3c7b3985aea6264a6651d23cf75586ddf20c7409e9f730e31f27263026be5` |
| `backend/operational_balance_sources.py` | `aac2aeefba84ff2d4069540fffc08fbad59baddab4c13871c25728c4325a6e50` |
| `backend/tests/test_operational_balance_bundle_item_view.py` | `da8c4db1e87c6f5b098cab1350d4ac50d2bb2edc440e5604c9bab6b1ea8489a8` |
| `adoption-red.xml` | `7e329cbc210e966a99656d89bd92d94eef1352c07f0dcb91310c145a5899638a` |
| `adoption-focused.xml` | `850289b2418662fb2e0ccbe852b7e70b1c5d15d3fd30eee4d6ad5cb24038cba0` |
| `measure_private_bundle_item_view.py` | `684aaef8fd0149b3aa1d42fe28c3f035feff1efc0d6dcf227965196c14520735` |
| `run/result.json` | `8f9321f986e49580ac9e45756a940b8c7ada426c3a207986f7478af016c492a6` |
| `run/baseline/metrics.json` | `2f3d0fa36c74b9b96243005b5c8aaed8f8c446f39aa7bb30ba9db02d329f396a` |
| `run/private_view/metrics.json` | `9cf36d70bf9b691b313b854f25f0e12f9309e0ca1c172dc5225f657df06ff40d` |
