# Connected A+B source integration proof

This fixture serves the actual `AccountingOnboarding` component with its default
HTTP client against the real Track A router. The only authentication seam is a
synthetic user already inserted by the existing real-Mongo test fixture. It does
not import `backend/server.py`, load deployment secrets, or contact a live system.

Use a disposable loopback Mongo replica set. The existing fixture creates a
UUID-named databases and drops them on graceful server shutdown. Initial synthetic
accounts/evidence are seeded with the existing canonical services, then the test
owner is paused. Browser interaction must change only onboarding sessions; a
complete fingerprint of every other collection is compared at the end.

From repository root, with Python test dependencies and frontend dependencies
installed, set `PYTHONPATH=backend:backend/tests` (semicolon on Windows),
`PYTHON_DOTENV_DISABLED=1`, and `MZ2_TEST_MONGO_URI` to the local replica set.
Set `MZ2_AB_DIST` to an absolute output directory outside tracked source.

1. `node scripts/testing/mz2_onboarding_ab/build.cjs`
2. `python -m uvicorn scripts.testing.mz2_onboarding_ab.server:app --host 127.0.0.1 --port 18761`
3. Set `MZ2_AB_ORIGIN=http://127.0.0.1:18761`, `MZ2_AB_OUTPUT` to an artifact
   directory, and run `node scripts/testing/mz2_onboarding_ab/browser.cjs`.
4. Gracefully stop the fixture server.

Install Playwright in the test environment or set `PLAYWRIGHT_MODULE` to its
installed module. `MZ2_BROWSER_CHANNEL=msedge` selects installed Edge on Windows;
otherwise Playwright Chromium is used. No fixture build uses the governed
deployment build entry point or generates a release intent.

Twelve executable scenarios cover create/list/get, cutover save, financial
save/reopen/explicit zero, CAS/idempotency, evidence-backed N/A, bank mapping,
per-account valuation, preview/review lock, readiness hard holds, 16-stage RTL
mobile navigation, and zero financial side effects. Negative HTTP cases use a
separate Node client so intentional 409/422 responses are distinguished from
unexpected browser console/page errors. No post, transition, approval, or
activation endpoint is invoked.


## Expanded domain acceptance

The original twelve browser scenarios remain unchanged. Eleven additional real-HTTP
scenarios use `/expanded` in the same loopback fixture, with an independent UUID
Mongo database and the actual production routers. This is API contract acceptance
alongside browser acceptance; it does not claim each supplemental operation was
performed through its corresponding UI editor.

| Stage | Meaningful exercised behavior |
| --- | --- |
| 1 | UI explicit cutover save, server readback and reload |
| 2 | UI canonical bank balance save/reload with explicit zero |
| 3 | UI exact provider/bank mapping and persisted selection |
| 4 | HTTP exact employee identity; separate salary payable, advance and custody |
| 5 | UI evidence-backed N/A; HTTP separate native supplier payable and advance |
| 6 | HTTP native person creation/readback, empty-name rejection and receivable |
| 7 | **Full draft persistence BLOCKED (C_NEW_SCOPE_REQUIRED).** Existing Track F API subset passes: courier, effective flat rate and intended bank binding; replay and stale-CAS rejection |
| 8 | HTTP courier COD receivable and payable remain separate in preview |
| 9 | HTTP driver COD receivable and fee payable remain separate in preview |
| 10 | UI per-account inventory valuation and evidence reconciliation; no physical approval |
| 11 | UI existing fee-policy selection persisted in the provider section |
| 12 | HTTP confirmed Track E binding and exact separate wallet/payable identities |
| 13 | HTTP paid-invoice prepaid calculation, explicit selection/replay and opening line |
| 14 | HTTP five typed liability/receivable/tax facts, no netting; unsupported deposit rejected |
| 15 | UI review lock; supplemental combined-domain preview/review and exact entries |
| 16 | **Live approval BLOCKED.** The expected locked-state assertion passes: Smoke B, owner authorization, physical approval and activation holds |

The Stage 7 active HTTP endpoints are delivered and executable, but the full
onboarding draft remains C_NEW_SCOPE_REQUIRED. Rich economics already exist in
accounting_shipping_contracts and a retained review-candidate service; they are
not an active native path. That service blocks draft/approval with unconditional
423, registers no endpoints, lacks approved evidence authority (503), and retains
a Legacy posting sink. Active Track F deliberately uses a separate flat rate with
one VAT treatment. OpeningCourierEditor cannot map its full prepaid/postpaid,
tier and independent-VAT draft losslessly to it. The new negative HTTP assertion
proves all six unsupported fields reject with422 and the complete persisted
native setup is unchanged. No guard, model, writer or production UI changed.

Stages 1-6 and 8-15 pass the isolated setup/API assertions named above, with
Stage 10 limited to planned valuation (physical approval unperformed). Stage 7
full draft and Stage 16 live business approval remain blocked. The JSON results
explicitly retain `business_uat=BLOCKED` and these acceptance limits even when all
23 executable assertions pass. Production Smoke B remains environment-blocked.

The original database fingerprint excludes only onboarding sessions. The second
fingerprint excludes exactly sessions, external-person setup, Track F setup,
fee-policy setup, prepaid selections and typed opening facts. Operational source
records, canonical financial accounts, advertising bindings, settings, write
controls, journals, inventory and all other collections must remain unchanged.
Synthetic prerequisite records are created before that baseline. Both database
generators are registered with `AsyncExitStack` before setup, ensuring cleanup even
on startup failure. Graceful shutdown drops both databases.

No opening-draft handoff is attempted because the owner is paused. No financial
post, activation, transition or physical approval endpoint is called. These
23 scenarios do not certify production Smoke B, activation, or a live release.

## Opt-in C1 rich-contract browser acceptance

Set `MZ2_AB_RICH_SHIPPING=1` for both `build.cjs` and `server.py`, then run
`browser-c1.cjs` instead of `browser.cjs`. With the flag absent, the original
23-scenario fixture, permissions, and assertions are unchanged. The historical
Stage 7 limits above describe that original acceptance baseline; this separate
C1 mode exercises the now-delivered rich-contract metadata route.

The opt-in main fixture grants the synthetic accountant explicit shipping view,
rule management, and contract-review permissions. It seeds a confirmed canonical
courier through the native setup service and immutable original bytes through the
existing source-file service before taking the baseline. It never seeds approved
evidence or a rich contract. The browser uses the actual editor and default HTTP
transport to save terms, download retained bytes, explicitly review contract and
tax evidence, approve terms, reload them, and revoke evidence. It also checks
mobile width and that readiness fails closed after revocation.

Only onboarding sessions and native shipping setup are excluded from the C1
fingerprint. Every other collection, including originals, financial accounts,
write controls, journals, operational sources, and inventory, must remain exactly
unchanged. No financial post, opening handoff, activation, or transition is called.
This is C1 source/UI acceptance, not production Smoke B or full 16-stage business
UAT. Desktop/mobile images and the HTTP/result record are written under
`MZ2_AB_OUTPUT`; failed assertions remain failures in that record.
