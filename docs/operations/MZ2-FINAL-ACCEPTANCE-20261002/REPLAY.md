# Executed verification commands

All executions target the clean candidate worktree, not this evidence branch.
HEAD88cc9131027fd6783a46e2e00fc6aaec4788b8fa;
TREE57483f44efa381ca872262f5a8bc61d2a87cae93.

Environment identifier: `mz2-final-acceptance-88cc9131-20261002`.
Fresh owned Mongo8.0.12, replica `mz2late`, loopback27135, transaction lifetime5s.
The UUID data directory/process command/creation identity are retained in
`evidence/environment.json` and `fixture-process.json`. No Production database,
credentials or host was used. Backend runner creates and cleans its own
standalone27136 fixture; individual tests own UUID databases.

The existing runners sanitize inherited application environment, disable dotenv
and use synthetic identities. Smoke B adds parent/child non-loopback network
denial, real full server startup and real password/MFA. Temporary setup data are
not physical business evidence. Do not reuse an existing server/data directory
or port, stop another actor's processes, run financial schedules on Production,
or change a write-control to make a check pass.

PowerShell, from the candidate checkout (replace output paths with fresh absent
directories for another execution):

```powershell
$python = 'C:/Users/amasi/mz2-final-integration-owner-20261001/.venv/Scripts/python.exe'
$node = 'C:/Users/amasi/AppData/Local/npm-cache/_npx/7b6227974258eb8d/node_modules/node/bin/node.exe'
$mongod = 'C:/Users/amasi/.codex/tmp/a10-mongo/bin-dist/mongodb-win32-x86_64-windows-8.0.12/bin/mongod.exe'
& $python scripts/testing/mz2_smoke_b_acceptance/run.py --execute-after-c3-approved --mongo-uri 'mongodb://127.0.0.1:27135/?replicaSet=mz2late' --port 18769 --evidence 'C:/Users/amasi/mz2-final-acceptance-proof-20261002/smoke-b-88cc9131'
& $python scripts/testing/mz2_late_delivery_evidence/verify.py backend --head 88cc9131027fd6783a46e2e00fc6aaec4788b8fa --node $node --mongod $mongod --output 'C:/Users/amasi/mz2-final-acceptance-proof-20261002/backend-88cc9131'
& $python scripts/testing/mz2_late_delivery_evidence/verify.py frontend --head 88cc9131027fd6783a46e2e00fc6aaec4788b8fa --node $node --output 'C:/Users/amasi/mz2-final-acceptance-proof-20261002/frontend-88cc9131'
```

Raw runner `started.json` contains the exact argv/cwd/env policy; `finished.json`
contains status and before/after source manifests. Smoke `result.json` contains
exact404/423 transcript, full database/index/options fingerprints and cleanup.
The independent evidence verifier is retained as a read-only script. Its local
Mongo option-key diagnostic was corrected from `replSetName` to observed
`replSet` before its final successful run; no application/assertion changed.

Frontend ordinary compile output is retained locally and hashed in
`compile-output-manifest.json`. It is not a release artifact or runtime identity.
Exact-B governed build/rehearsal results remain in CI-MATRIX.json and their
GitHub job links. No release lease, publish, deployment or rollback was run.

Rollback reference only: Production/base
`83363097d48e034dc7140a60c290efc684e1ffde`.

## Resource failure and unchanged full rerun

The first backend command returned exit1:2115PASS+900subtests,2setup errors.
Its raw records remain unchanged. The owned C replica was stopped after exact
PID/creation/command/dbpath verification; data were retained. A new dedicated
fixture was created at
`D:/CodexAcceptance/mz2-final-acceptance-88cc-20261002-9b5d37b1`, with Mongo8.0.12,
the same loopback port/replica/5s transaction contract,512MiB test oplog, and more
than718GB free. No disk-minimum guard or accounting guard was relaxed.

The recorded focused-retry.py executes the original current-carrier native test
file with a sanitized environment;15/15PASS. The full retry used:

```powershell
$env:TEMP = 'D:/CodexAcceptance/mz2-final-acceptance-88cc-20261002-9b5d37b1/temp'
$env:TMP = $env:TEMP
& $python scripts/testing/mz2_late_delivery_evidence/verify.py backend --head 88cc9131027fd6783a46e2e00fc6aaec4788b8fa --node $node --mongod $mongod --output 'D:/CodexAcceptance/mz2-final-acceptance-88cc-20261002-9b5d37b1/backend-88cc-retry'
```

Result:2117PASS+900subtests,0failures/errors/skips,1017.06s. Source unchanged.
The retained summarizer independently checks every original selected module,
all parent cases, source manifests and exit status before producing the domain
matrix. It changes no test. D replica/standalone cleanup and absent test database
proof are retained under evidence/retry-D. Data directories remain offline for
audit; no recursive removal was performed.
