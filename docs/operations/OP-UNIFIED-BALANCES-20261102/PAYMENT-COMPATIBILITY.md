# Native payment normalization: compatibility correction

This follows application source b30bd7c6a2db49fc2ed1d3e17156c3f0ae9f16a3 in Draft PR1687, not a release. Main scope/authority inventory remains STATUS.md. Production, original branches, live balances, policy/opening/engine files, /app and leases are unchanged.

Initial exact-head Operational Balance Web run 41360373945 completed with two backend failures, not a passing acceptance. The downloaded official backend artifact13104574965 has ZIP SHA256 7b9ada93823edbc346bd58867bf9412fae99227ad392f074321782e6e76b2ce4. XML has 811 cases: 809 passed, 2 failed, zero errors/skips. Failed cases: published customer-return read with unknown_payment_method, and exchange contribution preserving a named-bank original order receipt. The frontend job passed. This initial failure is retained, not waived.

Cause: strict source routing was also applied to read-only customer-return party resolution. Unknown party should leave the order readable with provider=None, while financial provider confirmation still rejects a nonmatching/unknown party. Also reuse the existing anchored transfer_bank classifier for named bank transfer methods; do not route them as provider money or create a spurious provider warning.

Correction adds require_known=False to pure identity resolution and calls it with require_known=True only for monetary source normalization. Conflicting known party evidence still fails in both paths. All source capture/reference/date checks and existing original-platform refund confirmation restrictions remain. No test was removed, disabled, skipped or weakened.

Five explicit local compatibility cases were added in test_operational_balance_native_payment_compatibility.py. Final selected local run is now 194 passed, zero errors/failures/skips, including the previous 95 payment cases, five new compatibility cases and 94 existing source/engine/open-cost tests. Prior 189 counts are not additive and did not establish full regression acceptance. Full exact-head real-Mongo/HTTP/Web CI must be re-read on this correction; no local Mongo was available.

Corrected payment module blob: 9ee7d0cb3d35b7818e89265054a6b56a0217d3a3. Source adapter, customer-return code and the original new payment test module are unchanged from b30bd7c6. This correction does not complete the actual dated capture/plan ingestion, provider V2 post-settlement reconciliation, inclusive payroll integration or full financial report. Keep the task Draft/unmerged; one final combined publication only after all remaining scope and release gates.
