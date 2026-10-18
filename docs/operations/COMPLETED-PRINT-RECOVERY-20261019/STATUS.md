# Completed shipment print incident — BLOCKED, no deployment

Base Production: e015b913ce950d6e5cf52cc0f1893da94b7b30b4 (fresh fetch).
Owner authorized urgent Backend fix and deployment with rollback.

Live browser evidence: selected affected order has two of two pieces ready, order details completed, but assembly completion response displays evidence_changed and pending completion/label. No order/customer identifiers retained here.

Code findings:
- Blocked v2 readback excludes assembly_completion_evidence_changed and requires original workflow/source hashes.
- Legacy completed queue has independent ownership, confirmation and handoff filters; does not depend on completion outbox.
- Legacy issue endpoint resumes delivery; changing GET readback alone cannot fix all old clients.

Rejected experiment: bypass historical workflow hash for this error, retain source hash, count current physical/virtual ready pieces, optional decision coverage. Real local replica set tests passed 29 tests / 64 subtests in 33.70 seconds, but independent review found incomplete approval/coverage proof. Removed entire experiment from source. Local patch retained outside repo marked UNSAFE-EXPERIMENT-NOT-FOR-RELEASE. Passing tests are not acceptance.

Remote diagnostic: read-only Release Guard status returned active:false. Targeted read in preview backend database returned zero matches, so this is not evidence about Production order fields. No production data writes, provider POST, release intent, merge or deployment performed.

Next safe action: obtain scoped read-only Production diagnostic of which stored source/workflow fingerprint changed, current source-to-piece coverage and queue exclusion facts. Build a minimal proven repair, test new and old API entry points plus replica-set races, then complete CI/release guard and live print verification. Do not deploy the rejected experiment or remove material/stock/custody protections.
