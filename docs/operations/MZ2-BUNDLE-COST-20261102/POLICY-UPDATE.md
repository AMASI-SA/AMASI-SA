# AMS13281 — approved embroidery financial policy, 2026-11-02

## Result

The user explicitly approved the proposed bundle definition and confirmed that name embroidery is **10 SAR additional to the 15 SAR shawl only when name-Yes is selected**. The user's subsequent clarification was that this had been charged as an independent service. The existing conditional service binding is the sole source of the10; no second service, surcharge or cost binding was created.

Production changed: **yes, the original product's embroidery financial policy and evidence reference were saved through the product UI**. The UI returned: **حُفظت سياسة المنتج. الطلبات السابقة تحتفظ بسياساتها التاريخية.** Final bundle definition saved: **no**. New code deployment: **no**. Historical correction, approved-journal edit and fulfillment execution: **none**.

## Observed and preserved values

| Field | Verified value or action |
| --- | --- |
| Product | AMS13281, product735616589 |
| Mezan base |15 SAR, unchanged |
| Salla fallback |70 SAR, unchanged |
| Existing name-Yes option |Embroidery service +10 SAR, unchanged binding |
| Name-No / name text field |No additional cost, unchanged |
| Cost treatment |Saved `independent` |
| Assembly origin |Preserved `unknown` |
| Evidence reference |Owner confirmation of extra10 and explicit statement that executor remains unidentified |
| Shortage supplier |Unspecified, preserved |
| Split source setting / source rules |Preserved |
| Pending recipe |Original conditional rose qty1 plus exactly one unconditional required current_product qty1, auto source, no variant, embroidery `per_unit` |

The exact saved evidence statement is:

> اعتماد صاحب المتجر في المحادثة بتاريخ 2026-11-02: تكلفة تطريز الاسم 10 ريالات إضافية لكل شال عند اختيار نعم لإضافة الاسم. جهة تنفيذ التطريز لم تُحدد بعد.

The option-cost editor confirms this is an existing **shared component/service** binding, quantity1, selecting embroidery10; it is not currently a direct-amount binding. The editor was inspected and canceled without mutation. The old automatic-review authorization blocker is resolved. The remaining issue for this inherited shared-resource model is missing business data, not permission: **who actually performs embroidery?**

## Why final recipe save is held

`backend/bundle_catalog_v2.py` allows a definition save with `strict=False`, but strict expansion used by intake rejects unresolved provenance with409 `bundle_component_policy_unresolved`. `independent` alone does not satisfy an `unknown` assembly origin. A supplier/purchased-complete origin has an additional `supplier_component_execution_required` guard; do not label a supplier service as in-house to bypass it.

This is a requirement of the current inherited shared-resource model, not a universal prerequisite for recording a service expense. The application also has a supported direct-amount cost-only model. Converting this existing binding to that model would clear its resource reference, detach centrally maintained service costs/usage and leave the saved resource-policy key invalid for later policy editing unless separately migrated. It is not an equivalent interpretation of “independent service”; the binding was preserved. The resource link also supplies preparation-service identity and later service-invoice eligibility, and its per-unit scope is not generally equivalent to a direct-amount binding. Even a non-stock service can have these effects. No direct-cost conversion or guard bypass was performed.

The existing regression `test_v2_current_unknown_policy_saves_then_blocks_execution` was rerun successfully: **1 passed in1.46s, exit0**. The test uses its synthetic fixture; it is not a live order or a production fulfillment attempt. Command (from `backend`):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests /tmp/mezan-bundle-cost-venv/bin/python -m pytest --noconftest -p no:cacheprovider -q tests/test_bundle_catalog_v2.py::test_v2_current_unknown_policy_saves_then_blocks_execution --tb=short
```

The persisted definition's schema version was not exposed by the current UI observation and is not inferred from its row count. The stricter behavior of the proposed schema2 save is verified by source and the existing test.

## Continuation

Obtain only the actual executor, then reread the live revision and policies. Apply a truthful supported execution policy and validate name-Yes and name-No paths before committing the already-approved bundle recipe. Preserve the existing10 binding, source settings and historical snapshots. If supplier execution is involved, verify the supported path before any recipe save. Do not republish the already-verified code or restart the released guard lease.

The original80 historical cohort and trustworthy frozen evidence remain unidentified. This update does not certify current catalog prices as historical prices or claim a live220 repair.

## Durable evidence

- [Saved financial policy and unsaved recipe context](evidence/embroidery-policy-saved-20261102.jpg).
- Deployed code M: `f8235d57e90f8a1271df6350d7e7919f5621ea11`.
- PR: https://github.com/AMASI-SA/AMASI-SA/pull/1690 (merged).
- Preceding archive checkpoint: `5f300e41655ee40d141da78f65759f5f2f57b18e`.
- Archive branch: `codex/mz2-bundle-cost-merge-handoff-20261102`, documentation only, not a new release candidate.
- Issue1006 remains at its2500-comment cap; this record and PR1690 preserve continuity.
