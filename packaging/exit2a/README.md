# EXIT-2A checkpoint: preparation, not release acceptance

Baseline: `1de6118484ac4fe1d0981e230618dbb573d8c58c` on
`hotfix/prod-snap-meta-final`, freshly verified unchanged on 2026-09-06.
Work branch: `codex/exit-2a-portable-linux-package`.

The first Linux execution must check out that exact, unmodified baseline with
its existing intent and run `python scripts/emergent_deployment_adapter.py build`.
Do not invoke the application, freeze-intent, prepare, or prepublish. Preserve
the existing GitHub environment: the adapter directly invokes the governed
toolchain, so no dispatcher marker spoofing or removal is necessary.

GitHub Billing Overview / Actions for personal account AMASI-SA was read on
2026-09-06: 0/2,000 minutes used, 0/0.5 GB storage used, $0 billable after
discounts. The owner limits the entire task to 45 aggregate runner minutes
across all jobs/attempts, standard Ubuntu only, no automatic retries or paid
overage. The first baseline attempt is capped at 20 minutes; charge its actual
duration against the aggregate before any further job. Evidence upload is
limited to a log at most 1 MiB and one checksum file, retained for two days.
Recheck usage before another run. No Docker/WSL installation on the user's PC.

## Source audit and exclusions

`audit_source.py` parses tracked Python source without importing it, records
dynamic loader and startup registration sites, and compares every frontend
intent member by content hash. It does not establish runtime import success.
The working copy excludes top-level AUDIT, reports, test_reports and memory.
Those exclusions must not be represented as a full repository backup. Build
scope completeness is independently checked across backend/frontend/scripts.
The tracked frontend `.env` was inspected without printing its content: it
contains comments only and no variable assignments. Its exact bytes are present.
Runtime images must exclude all `.env` files and use synthetic configuration.

## Dependency and startup findings

- `emergentintegrations==0.1.2` remains unchanged pending the dynamic-load audit
  and actual import tests; no substitute or fake module is allowed.
- The supplier-management package dynamically loads its local sibling Python
  file through `spec_from_file_location` and `exec_module`. Include both files.
- PDF imports PyMuPDF and qrcode, missing from the current manifest. fontTools
  supplies optional font coverage checks; decide and pin it for the portable
  package before testing. Keep existing pins for other packages.
- Startup handlers exist in server.py, both advertising schedulers, and
  product_google_taxonomy_ai_pilot.py. The last one starts a resumable worker
  after readiness; disabling only the two advertising scheduler flags is unsafe.
- An explicit opt-in rehearsal boundary must be validated before dotenv and
  application imports, prevent every startup initializer/worker, restrict HTTP
  routes to tested synthetic flows, and run in an environment without egress.
  This boundary has not been implemented or tested at this checkpoint.

## Overlap reviewed before runtime changes

PR #1003 at `d68e5fb20837ae935aa7755df436c26368e784a3` changes server Mongo
construction/readiness/auth and Qoyod polling. PR #1013 at
`6aecb923f617979106b856fe6e55e718d5c52bd8` adds server middleware and a global
startup index call. Neither changes the proposed first-import rehearsal
validation seam or the beginning of `on_startup`; future integration must still
test shared server behavior. Neither branch has been modified or incorporated.

## Acceptance remaining

Linux baseline build; portable locked install and pip check; real imports;
disposable Mongo with auto_send=true fixtures; zero startup writes/workers;
environment-enforced egress denial; Auth/orders/PDF; restart/shutdown; candidate
CI and Draft PR. Modified source will invalidate the existing release intent:
report the candidate SHA and request a separate intent decision, never waive it.

Emergent UI is now accessible. Home shows Salla Analytics as Published; View Info
shows salla-analytics, job ab0374e5-2a04-4e34-b24c-447b0238a858, live link
https://salla-analytics.emergent.host and custom domain mezansalla.com. Machine
type Large is workspace metadata, not verified Production resource sizing.
The displayed most recent GitHub export branch is not live SHA evidence.
Production database mapping/provider/owner/backup authority, backup/storage
metadata and domain/DNS ownership remain unknown. No Production data, provider calls,
release lease, intent change, deployment, DNS or paid resources are authorized.
