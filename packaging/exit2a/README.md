# EXIT-2A isolated Linux rehearsal

This package tests the real application against disposable synthetic MongoDB.
It is not a deployment entrypoint or Production backup/restore package.
See [RESULTS.md](RESULTS.md) for source identity, CI evidence, dependency changes,
measured isolation, ownership gaps and acceptance limits.

Baseline: `1de6118484ac4fe1d0981e230618dbb573d8c58c`.
Branch: `codex/exit-2a-portable-linux-package`.
Passing runtime code: `d379c62a7432873e310edd8459da1cae05f17d0c`.

## Run contract

Use an existing isolated Linux Docker host with permission and an established
compute budget. From the repository root:

```sh
bash packaging/exit2a/run_linux.sh
```

Build downloads use official Python/Debian/Docker sources. Runtime containers
have no external interface: application and tmpfs Mongo share a loopback-only
namespace. Do not supply credentials, mount host configuration, publish ports,
change the network or point this harness at an existing DB. The harness refuses
nonempty synthetic data and cleans its own temporary containers.

Only `web` is supported through entrypoint.py; worker/migration commands fail.
The entrypoint checks exact public synthetic environment, absence of .env files
and loopback-only interfaces before importing server. Its restricted lifecycle
performs a bounded Mongo ping and suppresses original initializers/workers.
HTTP is restricted to tested health/Auth/order routes. One import-time bank
review index call is skipped only with the explicit rehearsal flag; normal
server entrypoint and index behavior are retained.

## Scope of proof

Clean install/pip check, real imports without emergentintegrations, two boundary
tests, Mongo import/startup profiling, task checks with auto_send=true, synthetic
Auth/orders, seven existing PDF tests and two real restart/TERM cycles passed.
OTP delivery, complete Production security middleware, continuous dependency
readiness, live providers and all routes/templates are outside this acceptance.

The original manifest is unchanged. The portable manifest excludes only
emergentintegrations, adds missing PDF requirements and pins additional resolved
dependencies. Bundled fonts/QR assets remain; system DejaVu fonts are added.
No fake module or arbitrary mirror is used.

## Governed build and delivery

The unmodified baseline first passed its existing governed Linux A/B build.
That does not authorize the changed candidate. Release Intent/Guard are
unchanged; no operational lease was invoked. New intent review and any
Production packaging/roles require a separate decision.

The checkout excludes historical AUDIT/reports/test_reports/memory directories;
all tracked backend/frontend/scripts build inputs and all 687 frontend intent
hashes were checked. This is not a full repository backup.

Five Linux jobs consumed about 7m28s (conservative: 10 of 45 authorized minutes).
Do not rerun CI/upload artifacts without checking quota and remaining budget.
Final delivery changes documentation only and skips additional push/PR CI;
required checks remain pending, not accepted.
