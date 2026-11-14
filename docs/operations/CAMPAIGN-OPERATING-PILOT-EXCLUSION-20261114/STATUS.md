# Campaign operating disabled pilot follow-up — current production base

This source replaces the frozen PR1927 candidate after Production advanced to PR1928 merge `3e9dc3b25e41c663e001822a666323c802e5ec5a` at2026-11-14T18:47:59Z. PR1927 A5/B5 and all successful source/intent/host/ready-transition evidence are preserved on their branch and the separate continuation branch. No PR1927 merge, original build, lease or publication occurred. Do not deploy or rewrite that obsolete intent.

## Minimal behavior change

Project existing status/payroll_enabled/accrual_policy fields and omit only a contract whose canonical normalized salary history is empty and whose three fields explicitly identify disabled pilot payroll. Valid salary revisions and a valid legacy effective baseline continue to contribute despite old pilot markers. Contributing contracts with missing costs stay incomplete. Existing identity, duplicate, currency, calendar, limits, deadlines and recurring-cost checks remain unchanged.

The exact reviewed source/test blobs are reused: b0576e365eb2a5b4f1e105e2a1be17f1d2393655 and5ec985aa2307f07d05b87486f59c9fb764205ed7. The observed root cause is documented in the continuation checkpoint without private identifiers or salary/campaign values.

## New base and verification

Base J6: `3e9dc3b25e41c663e001822a666323c802e5ec5a`, tree `355fdae884cf4a849b0acdf1c29081730556c6b0`. Preserve all merged Salla and financial source-read recovery files exactly and retain its inherited intent blob `097b7fa75d0ab840d1c8396d98ccfb279e64f777` throughout source-A history. Source changes only this status plus the reviewed operating module and its tests.

Root reviewed the11 new-base changed paths; none overlap the pilot files. The operating reader retains its independent bounded query path and payroll/recurring policies. Independent compatibility review and exact-new-source CI are required before a new intent freeze. Prior A5/B5 all11 workflows, both host adapters and ready checks passed; these are historical evidence, not substitutes for the new A/B gates. Five synthetic regression cases previously passed as part of253 backend tests, with50 frontend tests/9suites.

## Release protocol and ownership

After all source checks, create a new reviewed intent-only B, pass its exact checks and both actual host adapter/package/JSON route proofs, then a normal tree-equivalent merge and exact-merge push gates. Before original writes/rehearsal, read actual official status and use the official operation lock; never touch another active lease or running writer. Production changes may supersede old release plans; read the latest durable records and actual original state rather than inferring completion from time.

PR1928's owner has announced its original rehearsal at18:50:51UTC; PR1923's earlier successful J5 rehearsal and pre-prepare failure remain its owner's evidence. Neither is a release receipt for this source. Final success requires an actual official3-probe verification and live operating allocation/net-profit arithmetic.

No private data, business-data changes, manual sync, new worker, advertising/budget writes or paid agent actions. Preserve original files and unsaved buffer. Issue1006 is full; use the replacement PR, this status and `codex/campaign-hover-profit-release-handoff-20261114` for continuity.
