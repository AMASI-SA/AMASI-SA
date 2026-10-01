# C4 — executed Acceptance-only Smoke B

**PASS for the user-approved Acceptance contract only.** This is an actual full-application HTTP execution, not a unit-test result or Production verification. `production_verified=false`; Release Ready remains NO until the other final gates are proved.

Executed clean source HEAD `586f706301050c5f63274b77b13406696cbbc556`, TREE `01d377faaeff009670278d836bd1c9f5b2dab854`. The child process independently recorded the same complete backend/harness source manifest, unchanged after execution. Production/base/rollback is `901568ccaaf510dc1f84d9c28f38d368d07dc64d`.

## Environment and contract

- Environment identifier: `mz2_smoke_b_acceptance_c4ad3c2347494672a9d1eed1ddce6efa`.
- Actual shipped `server.app` and its startup lifecycle, listening only at `http://127.0.0.1:18769`.
- Real Mongo 8 replica `mz2c`, `mongodb://127.0.0.1:27130/?replicaSet=mz2c`; writable-primary/replica identity retained.
- Fresh synthetic Owner, real password login and real MFA enrollment/verification. No auth dependency override or simulated login response. Authentication secrets are not retained.
- Supported local test startup identity, not a verified Production release identity. `/health` explicitly reports `verified_identity_available=false`. No Release Guard was changed or bypassed.
- Canonical owner starts `writes_paused=true`, control revision1. No toggle or control mutation occurs. Existing idle application pollers run against empty local integrations only. Child and parent network audit hooks deny non-loopback connections; no external attempt was recorded.

The test-only startup identity and synthetic data are deliberate Acceptance differences. This execution does not prove a deployed package, real balances, Production release identity, opening, activation or Production schedules.

## Executed sequence

| Step | Actual result |
|---|---|
| Full application readiness | HTTP200 after actual startup |
| Owner password login | HTTP202, required real MFA setup |
| Owner MFA verification | HTTP200; exact synthetic owner |
| Canonical `/api/financial-provider-apps/accounting-module/write-control` | HTTP200; paused=true, revision1 |
| Direct Mongo absence check and GET of unique nonexistent onboarding session | No row; HTTP404 `onboarding_session_not_found` |
| Valid `opening-draft` POST for that missing session | HTTP423 `mz2_writes_paused` |
| Canonical control and complete Mongo fingerprint after probe | Identical to before |
| Source manifest after probe | Exact clean HEAD/TREE and all source hashes unchanged |
| Cleanup | Task PID35828 stopped, exact disposable database absent, port no longer listening |

The baseline is after actual authentication and before the nonexistent-session probe. It covers **all119 collections,8 documents, indexes and collection options**, without auth, lease or other exclusions. Both digests are:

`f2e7ebd4d9c144c3c49015cef9fa19531c7b77c91d041e66a956b5a03c88b895`

Both Native journal collections and Legacy general ledger contain zero documents before and after. No opening draft is created. Root independently rechecked source equality, full fingerprint equality, unchanged control, transcript423, absence of outbound attempts and cleanup artifacts.

## Reproduce and evidence

From a clean checkout of the pinned source, using installed backend requirements and a dedicated local replica:

```powershell
python scripts/testing/mz2_smoke_b_acceptance/run.py --execute-after-c3-approved --mongo-uri 'mongodb://127.0.0.1:27130/?replicaSet=mz2c' --port 18769 --evidence 'C:/absolute/new-smoke-b-evidence-directory'
```

The evidence directory must not already exist. The runner refuses preexisting databases, uses UUID ownership, suppresses inherited provider secrets and dotenv loading, starts only its task child, and removes only its task-created database.

[Run result](evidence/c4/acceptance-run5/result.json), [runtime source manifest](evidence/c4/acceptance-run5/runtime-source-before.json), [server log](evidence/c4/acceptance-run5/server.log), [verified summary and artifact hashes](evidence/c4/acceptance-run5/verified-summary.json).

Runs1–4 remain preserved as failures. Runs1–2 exposed missing declared local dependencies; run3 required configured loopback SMTP for the actual auth startup; run4 exposed a wrong control URL in the harness. Only environment/harness corrections followed; no financial guard or product semantics changed to obtain this result.

**Production financial writes=0. Merge/Deploy/Opening Post/Activation=NO. Write-control unchanged. Acceptance PASS grants no Production authority.**
