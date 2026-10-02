# B correction: native commit conflict recovery

The complete132-file local regression on reviewed B
`ba743862f4ac0e6b428bf251402bd7fc04862002` produced1769 passing tests,
900 passing subtests and two failures. Both original Track E concurrency
assertions failed with server code112 and `TransientTransactionError` when a
native advertising collection was first created at concurrent commit. They
were not transaction-lifetime errors and the assertions remain unchanged.

Motor3.7.1's transaction context may commit outside its callback retry handler.
The existing `accounting_atomic` boundary now admits another existing driver
transaction only for a server `OperationFailure` labelled
`TransientTransactionError`, explicitly excluding
`UnknownTransactionCommitResult`, including a failure carrying both labels.
Unlabelled errors, domain HTTP errors and cancellation propagate unchanged.

There are at most three driver invocations and a120-second monotonic admission
budget. This is not a hard request timeout: an admitted driver call retains its
own retry budget. No commit is cancelled merely to enforce a wall-clock limit.
An unknown outcome is not blindly replayed; existing domain idempotency permits
an explicit later request to recover the previously committed journal.

Every new attempt recreates the existing session-bound database scope,
reacquires the owner revision lock and rereads paused state. Snapshot isolation,
majority+journaled writes, PRIMARY, period/identity checks, writer transition,
423/503 guards and financial economics remain unchanged. No financial writer,
default identity, collection bootstrap workaround or Legacy fallback is added.

Nine new cases in `test_mz2_atomic_recovery.py` use actual Mongo transactions and
native journals with controlled commit fault injection. Abort precedes a
synthetic transient/nontransient/cancellation error. An actual commit precedes
an unknown-outcome error. They cover one sealed sale after retry, unknown/mixed
labels, nontransient/unlabelled refusal, attempt/admission limits, cancellation,
and a real HTTP423 when the owner pauses between attempts. The two positive
retry cases failed against the unchanged prior source; three refusal cases
already passed. The original advertising race tests are also retained.

The existing A+B real-Mongo CI job now includes this recovery suite using its
already-delivered replica and standalone fixtures. Focused, full-regression,
exact source/intent identities and final CI results are recorded externally in
Issue1006 and the review PR after verification. An earlier interrupted broad
attempt has no final result and is not counted as PASS.

This changes governed source and requires a fresh v5 source/build/intent pair;
the prior reviewed pair remains preserved. Release remains BLOCKED by the
documented Stage7, H2 and business-UAT/SmokeB boundaries. Manual POS accountant
review remains delivered; no external processor source is required.

Production financial writes=0. Production merge, Deploy, Opening Post and
Activation=NO. Accounting write-control policy and release guards are unchanged.
