# EXIT-2C independent runtime candidate

This is a source/test candidate, not a deployment approval.
**Callback acceptance passed; the final shutdown safety delta awaits Linux verification. See RESULTS.md.** No Production
credentials, data, providers, backups, operational Release Guard lease or new
Release Intent are used by this task. See sources.json for exact provenance.

## Roles

The independent Python entrypoint is `backend/independent_runtime.py`:

```sh
python backend/independent_runtime.py migration
python backend/independent_runtime.py web --port 8001
MEZAN_WORKER_ENABLED=1 python backend/independent_runtime.py worker
```

These describe the roles, not permission to run them on a live system. The
included Dockerfile is an acceptance image with exact public synthetic values.
Run it only through `bash packaging/exit2c/run_linux.sh` on an authorized Linux
Docker host with a checked aggregate compute budget. Runtime shares disposable
Mongo's network-none namespace, has loopback only, read-only app files, tmpfs,
no host ports, dropped capabilities and no real credentials. No Docker/WSL
installation or privileged machine change is needed on the owner's Windows PC.

The unchanged `server` entrypoint preserves default application behavior.
Independent web uses the full route surface and installs the actual protection
chain per process, including progressive login, mobile sessions, passkeys, MFA
and email OTP. It does not start router schedulers or global data migrations.
Readiness requires schema completion and Mongo availability; failed dependency
checks withdraw readiness. Configuration cache initialization is read-only and
per process. This is not the restricted EXIT-2A route/lifecycle substitute.

Migration reuses explicit index definitions through an index-only boundary that
forwards actual Mongo operations and refuses swallowed failures. It does not
execute legacy cleanup/order-date/cost backfills. Existing users are preserved;
a populated database without Owner fails for an explicit recovery decision.
Only an empty database uses the original password-required initial bootstrap.
Schema completion uses the application's existing fenced startup coordinator.

Worker requires explicit arming. A stable role claim prevents workers from two
source versions running concurrently against the same database. Heartbeat starts
before initialization; fence loss cancels initialization and workers. Existing
Qoyod per-job leases/idempotency remain. Salla maintenance and registered router
schedulers belong here; provider permissions and settings are separate controls.

Production mode additionally requires the repository's verified artifact identity.
No valid candidate identity is supplied here. Successful synthetic tests cannot
satisfy that gate, establish live SHA, or authorize business writes.

## Tests and limitations

The harness runs two real web processes and real Mongo. Synthetic test fixture
writes are separate from profiler windows that prohibit import/web lifecycle
writes. HTTP acceptance uses password plus actual second-factor endpoints;
WebAuthn uses a software authenticator and genuine cryptographic verification.
OTP challenge data is synthetic and SMTP is local only. No access token or
guard success function is substituted in real HTTP acceptance.

Phases include failed schema initialization, retry/idempotence, two protected
web processes, Auth/tenant/CSRF tests, attachment/PDF/preparation history,
Mongo outage/recovery, process restart, worker exclusivity/fence loss and TERM.
Existing focused unit/regression suites may use their documented test doubles;
they are run separately from actual HTTP acceptance with real jwt/bcrypt modules.
No broad /app-dependent conftest is loaded.

The preparation API stores frozen inputs/history and streams/regenerates PDFs;
do not interpret tests as proof every original live PDF is stored byte-for-byte.
Hardware/browser passkey enrollment, real mail/provider configuration, live data
schema compatibility, restored data reconciliation, hosted reverse proxy/TLS,
capacity and final governed artifact acceptance remain separate acceptance areas.

## CI and retained boundaries

EXIT-2C has at most 25 additional aggregate standard Ubuntu runner minutes,
inside the original 45-minute authorization (prior conservative debit 10).
Later runs are capped at the remaining approved allowance; every attempt needs an explicit ledger
and fresh quota/storage/no-overage check. No automatic retry or runtime artifact
upload. The governed build's proposed 10 minutes are reserved for a later decision.

Final documentation-only delivery may skip new push/PR workflows to respect
the budget. Required checks remain pending rather than waived or called passing.
No merge, Ready for Review, deployment, release lease, real restore, DNS/OAuth/
webhook mutation, paid resource or Emergent mutation is part of this candidate.

Support metadata request was sent after duplicate/account checks, Gmail message
ID `1a076921d10b2c44`, SENT. It explicitly does not authorize export/restore,
access changes, upgrade or deployment. Ownership/backup/storage/DNS and live
source identity remain open evidence gates, not assumed from application code.

## Evidence still required from owners

| Gate | Required non-secret evidence | Holder / next action |
| --- | --- | --- |
| Live source identity | Deployment-to-source/artifact identity matching all healthy replicas | Emergent deployment administration/support; GitHub baseline is not live proof |
| Production DB vs Preview | Deployment ID -> database/cluster ID, provider and administrator | Emergent support request already sent; no independent Atlas account assumed |
| Export/restore authority | Current account role and documented consistent export/restore procedure, executor | Emergent/provider owner; independent later authorization is still required |
| Actual backup | Successful backup ID, source, consistency timestamp/timezone, retention/expiry, format | Emergent/provider; an upgrade offer is not backup evidence |
| Persistent files | Volume/bucket manifest, managing account, copy and restore procedure | Emergent/provider; no customer files read or downloaded |
| Domain | Registrar account controlling mezansalla.com, nameservers and DNS operator | Owner's domain-details page, read only; no DNS changes |

The previously exposed connection value was neither used nor repeated in EXIT-2C.
Its prior appearance in a browser tool result does not establish public disclosure,
DB ownership or permission to connect. No live credential/encryption rotation was
performed. Any containment assessment remains separate and service-preserving.

## Bounded preparation integration fix

The real start-file route passed `permissions` to the installed full-assignment
wrapper, whose baseline signature did not accept that keyword. This produced
HTTP 500 after actual authenticated attachment upload. The candidate accepts
and forwards the unchanged permission set; it does not relax allocation or role
checks. The acceptance sequence requires HTTP 409 for an incomplete allocation,
then completes only the synthetic fixture and checks successful/idempotent start.

The test uses a pre-seeded ready batch and a legacy one-line PDF. It does not
prove draft/finalize/allocation UI, physical-piece QR rendering, every tenant
role combination, or byte-identical archival PDF storage. These are explicit
coverage limits, not pass claims based on a ping or successful import.

Exact callback ownership and red/green evidence: [WORKER_CALLBACKS.md](WORKER_CALLBACKS.md).
Final direct-worker-first bounded drain is locally tested only; timeout retains
the claim and requires external supervisor escalation for uncooperative tasks.
