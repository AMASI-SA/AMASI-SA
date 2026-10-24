# Source addition stage policy — WIP, not verified or deployed

Owner clarified on 2026-10-24 that prior products were all assigned, but Salla
and the order details badge still show pending review. Order number is needed
for read-only examination of its actual workflow, approval proof and allocations.

Verified source inspection: local completion intentionally leaves provider
status unchanged; order details displays status_native/status, not workflow.stage.
Thus that badge alone cannot determine whether an order is internally reviewed.

Requested behavior: enable new-line review for internal reviewed/in_progress;
hide additions action for truly pending review; show an explanation for other
ineligible stages without asserting an unverified historical addition time.

WIP broadens the server workflow gate while retaining the existing provider
execution envelope, original-line checks, review proof, component checks, owner
transaction and revision fence. The conditional append and event preserve the
observed workflow stage. Web renders a stage-block explanation without a button.
Companion mobile WIP consumes the optional Arabic blocked_message.

Validation: git diff --check passed. No runtime tests yet. No claim of fix.
Next: obtain order number, inspect actual workflow read-only, add and execute
reviewed/in_progress/pending/terminal regression matrix, and validate both UI
clients. Do not publish this WIP. Existing production 4e4e064 and APK57 unchanged.
Recovery switch and existing rollback references retained. Issue1006 is at its
comment limit; this task branch/status file is the continuation checkpoint.
