# R5 reconciliation checkpoint — IN_PROGRESS, activation OFF

Inputs: task predecessor beaac3e71e49d777ffd2b58322274add11f6f218;
archived33-file R5 JSON67786854114c808aa31916ff0294d8774422b601d6cea530d79df70977408529;
Production Git a7bcb1626e2ad4defba2260d4a113417f91f3120 (read only).

The earlier refusal was correct: newer Production changed employee stage-count
projection and two-decimal COD print formatting plus its test. The reconciliation
retains BOTH sets of behavior, with no force-apply or weakening of archive hashes.
Two source files merge cleanly against the actual common base. For the appended
test file, retain both additions and update only R5's new amount expectation to
20.00 SAR. The existing Production test for741.40 SAR remains unchanged.

The33 exact cumulative results are specified by archived hashes plus three
reviewed overrides in manifest.json. recover.py and all historical R5 chunks are
unchanged. reconcile.py build runs only in the disposable task-branch CI and
verifies every predecessor/output; export stores blob objects ONLY after tests.
The ordinary backend/frontend paths must then be materialized and committed;
this reproducible checkpoint alone is not an active R5 runtime source.

Observed local checks:156 new/core/native tests PASS across separate XML runs;
113 ordinary regression tests PASS, one known importorskip because mongomock_motor
is not installed in the local source fixture. Native grouping was split to respect
tool execution limits; two timed-out aggregate attempts are NOT counted as passes.
Dedicated CI installs the missing mock dependency and runs the full combined sets.
The3 native lifecycle cases and actual generated supplier PDFs ran on synthetic
loopback replica-set records. PDF rendered and visually inspected; no live customer
files or provider calls. Frontend Jest tests still require the CI run.

No Merge/Deploy/Preview/Production or live financial writes. R5 source acceptance,
central reliable writer controls, persisted Salla sidecar, actual reporting, both
Android clients and operational rollback/backup drills remain unaccepted.
