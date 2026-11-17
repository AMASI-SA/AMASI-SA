# Courier delivery history recovery — 2026-11-17

## WIP: independently reproduced acquisition failures

- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/courier-delivery-history-recovery-20261117`.
- Verified production source base: `8df479ef24b4f8847c26ba18d48e3248f7b673cf`.
- Scope: visible-request courier evidence acquisition, fair bounded progress,
  and explicit matching of an existing pending order. Preserve delivery proof,
  source version, owner, waybill, cutover and financial guards.
- Production changed: **no**. No release lease, merge or deployment performed.

## Evidence

The existing native history verifier accepts a synthetic reproduction with a
single original delivered shipment, no shipment delivery timestamp, and a
complete same-carrier dispatch/delivery history. No date parser or proof guard
change is justified by that reproduction.

New synthetic regressions in
`backend/tests/test_operational_balance_courier_acquisition_progress.py` reproduce:

1. An expired invalid prefix repeatedly consumes all four selection slots and
   prevents a later never-attempted delivery from being inspected.
2. Due candidates are chosen in insertion order rather than oldest retry order.
3. One native transport timeout abandons the remaining bounded order allowance.
4. A hanging bank adapter consumes the complete visible acquisition budget and
   prevents courier acquisition from starting.

Root independently ran the following against unchanged production source:

```bash
PYTHONPATH=backend:backend/tests PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 \
  /tmp/mezan-courier-test-venv/bin/python -m pytest --noconftest \
  -p no:cacheprovider -q --tb=line \
  backend/tests/test_operational_balance_courier_acquisition_progress.py
```

Result: **4 failed in 1.99 seconds, exit 1**, at the four intended assertions.
The tests use in-memory Mongo emulation and mocked provider reads; they are not
real-Mongo or production evidence. Existing waybill acquisition baseline:
**14 passed in 1.62 seconds**.

## Implementation ownership and next action

Root owns backend production changes. A test-only agent authored the new
regressions. A separate frontend agent owns the explicit per-order matching
control and focused frontend verification. No shared production file has
multiple writers.

Next: preserve this RED checkpoint remotely, implement fair atomic selection,
per-order transport isolation, per-source time allocation and truthful CAS
outcomes; expose a scope-bound optional order target under the existing visible
refresh lease; rerun regressions and review the integrated diff independently.

Preserve the four-order cap, per-order fifteen-minute cooldown, cancellation,
authorization and compare-and-set guards. No background recovery, manual
delivery assertion, financial mutation shortcut, or loosened shipment matching.

## Continuity

Issue #1006 has reached its comment limit. Do not overwrite its historical
content or repeatedly attempt an append. This task status and the forthcoming
task PR serve as the continuation record, with exact pushed checkpoint SHAs in
the PR description. No PR or CI run exists for this checkpoint yet.
