# P01 receipt intake — WIP, Preview only

The owner approved separate non-posting bank-receipt intake and platform
statement upload in either order, explicit matching, separate entry/approval
permissions, and one atomic settlement after both sides pass review.

The previously uncertain synthetic settlement was checked on the isolated
Preview replica set using read-only queries. It remains reviewed, with no
matching journal legs and unchanged balances. It must remain unposted under
the current instruction. Private identifiers and financial evidence are kept
outside this repository.

Base: e82918d130f42b228346b2cbd3d70ff4daf21bf3, open draft PR1091.
Task branch: codex/p01-settlement-intake-20260919.

WIP changes:
- Shared non-ledger receipt service with explicit bank-message references,
  request retry identity, owner scope and no automatic matching.
- Daily financial movements form and explicit statement-side receipt linking.
- Missing receipt/difference blocks settlement lifecycle.
- Wrap the first-installed lifecycle posting route in the existing transaction;
  previous atomic tests covered the later compatibility handler instead.

Only syntax and diff checks have run for this checkpoint. Real Mongo,
frontend build and actual Preview acceptance remain unverified. No completion,
merge, Production release or P01 closure is claimed.

Next: run the focused receipt suite on a fresh synthetic database using the
isolated Preview replica set; inspect failures, then test the complete candidate
tree and real UI. Preserve previous journals and settlements. Production
changed by this task: no.
