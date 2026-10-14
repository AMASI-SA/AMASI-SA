# Receiving attempt evidence retention

The current policy is **indefinite retention** of all receiving attempt journal
rows, including confirmed, rejected, and unresolved/in-progress attempts.
There is no TTL index, deletion worker, age-based purge, or automatic collection
drop in the application. `expires_at` is a lease/fencing deadline, not a data
retention deadline. The reconciler updates expired in-progress rows to a terminal
state and releases only their matching session token; it does not delete rows.

Preserve the request identity, payload shape/deadline, terminal disposition and
exact event aliases. They are required to distinguish a retry from a new receipt,
reject reuse of an identity for another piece, recover lost responses, and prove
the outcome of same-session rescans after the invoice is finalized. Piece/event
history and cancelled receipt evidence remain necessary alongside this journal.
Deleting old journal records would also remove terminal rejection and alias
evidence that cannot always be reconstructed from a client request ID alone.

This policy intentionally favors recovery correctness over bounded storage.
Storage grows with every admitted/rejected attempt; quantity-selection previews
may retain a larger response. There is no supported safe cleanup command in
this change. Monitor collection bytes, document counts by state, index sizes and
the age/count of in-progress rows. Any future archival or compaction policy needs
separate approval and tests proving permanent identity tombstones, exact alias
lookup and finalized/cancelled recovery survive migration before deletion.

`test_supplier_scan_retention.py` exercises a real disposable Mongo replica set:
age confirmed/rejected rows 400 days; start the reconciler; inspect the installed
indexes for absent TTL; sweep; recover and replay the original request; reject
the same identity for another piece; verify one event and one reserved piece.
It also verifies the old rejected request remains explicitly rejected.
