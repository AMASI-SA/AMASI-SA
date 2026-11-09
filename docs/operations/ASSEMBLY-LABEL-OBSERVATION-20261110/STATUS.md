# Assembly label observation — WIP, not released

Repository: AMASI-SA/AMASI-SA. Branch: codex/assembly-label-observation-20261110.
Base: 20dc12ac907ca7baafed620ffabbcad23b09103c.

Two defects reproduced before fixes:
- A delivery worker lease rejected explicit label fetch before reading an already completed provider shipment. Added a read-only observation during delivery ownership, preserving manual single-flight, authorization, material snapshot and carrier checks. No lease stealing, provider writes, polling or background work added.
- Employee source refresh reauthorization inside OperationalDatabase treated access-row user_id (employee) as merchant. Real permission lookup inside the restricted capability reproduced operational_owner_scope_conflict. Added an exact find_one owner_user_id + nonempty employee user_id read for fulfillment, retaining existing write restrictions.

Validation: pytest --noconftest -p no:cacheprovider for test_mobile_assembly_refresh_actions.py, test_manual_label_delivery_overlap.py, test_manual_courier_label_refresh_race.py: 53 passed, exit 0. Both new cases failed with their specific reported 409 before their respective fix. Mongo mocked; real replica-set acceptance pending.

Live web observation: an affected external-carrier label opened from one explicit click. Not physical Android or printer evidence. Shared Release Guard freshly returned active:false after terminal sign-in. No shared source mutation, merge, lease, publish or production data repair by this task.

Remaining: negative authorization/concurrency tests, CI integration and real Mongo acceptance; investigate separate all-products-ready versus completion eligibility mismatch using current order data; review and v5 release only after acceptance. Do not treat screenshot mismatch as proven cache accumulation. Preserve unrelated mobile worktree changes and prior successful release.

Production changed: no. PR/CI: not yet created. Next: extend negative boundary tests, commit/push this checkpoint, create Draft PR, record exact SHA in Issue #1006; continue targeted read-only diagnosis.
