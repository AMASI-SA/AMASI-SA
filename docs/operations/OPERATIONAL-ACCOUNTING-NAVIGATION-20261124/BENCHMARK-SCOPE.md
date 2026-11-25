# Proposed actual-data A/B procedure

No actual-data run or performance result exists yet.

Use exact parent frontend c6d05c7814e27e2f22a8c82b5bfe4cf177c17fbe and candidate9a851854132bd71be922c4aae24b4cc20a546e41 with the same candidate backend (this PR changes frontend only), frozen imported dataset, actual owner permissions, private API/worker/Mongo, viewport, browser and observer bytes. Keep one visible tab; alternate A/B and B/A blocks. Source-serving Vite and copied-backend timing are not Production performance.

## Immediate finite cohort

- Main views: home, assets, log, parties, reviews, position, reports, tools.
- Reports: summary, profit, providers, accounts, suppliers, employees, couriers, drivers, advertising, other, expected, reconciliation.
- Operational specialized destinations: provider-fees, provider-statements, financial-position, customer-returns, customer-exchanges, access, shipping-review, provider-history.

Other settings/inventory/legacy links are unverified. In particular /shipping-settings-v2 calls /api/settings, outside the reviewed runtime and complete data projection; it must not count as ready or successful. Do not claim every Tools link or the entire workspace was covered.

## Separate the observations

Do one complete functional coverage pass per arm before repetitions. Check actual successful read and visible data/empty/error state separately from URL/shell timing. Reviews and Tools initial screens are navigation-only. Specialized data readiness is unknown to the passive observer.

Document-cold/backend-warm means a fresh document, settled actual auth/bootstrap and common starting view, then the first measured target visit, with matching backend priming/browser cache/Vite transform conditions. Reload does not prove a cold backend. Warm means the same transition repeated in one document after completed target reads, retaining all filters, currency, table page and viewport.

Keep Reports-mounted tab switching separate from leaving/re-entering Reports. Summary/profit/reconciliation share backend summary and can correctly reuse its response; absence of a new request is not itself failure. Keep the base pathname and daily-movements pathname as distinct contexts.

Focus A/B repetitions on summary→couriers, couriers→drivers, ready drivers→assets, and observed pending report→assets/log. Complete broader required route samples before claiming the all-route p95 target. At least20 valid observations per route/cache/from-to/condition group are required to calculate p95; this threshold is not a statistical-confidence guarantee. Report failures, unfinished reads, cohort size and sample count, not just percentiles.

A pending-exit case requires the real pending status visible before a trusted navigation click. Record obsolete-content absence plus a separate authorized network/runtime observation of demand release and stopped polling. The passive panel cannot prove HTTP methods, cancellation acknowledgement or absence of writes. Do not introduce artificial delays and label them actual-workload results.

If courier/driver cohorts exceed12 rows, do a separate functional page-two/return label check with unchanged data; pagination is not a recorded route transition.

## Current observer limits

shell_two_raf_task_proxy is two animation frames plus a task, not measured paint or proof of interactivity. Specialized shell matching can precede financial-position inner-view commitment; verify the selected view independently. Other buttons, pagination, browser history, ledger/resource routes and summary CTA actions are not covered by its click handler.

Actual movements/plural statements/dynamic resource reads collapse into generic operational resource categories; onboarding fee reads are omitted. Resource timing does not establish request cancellation or method. Export per block before full-document navigation and check dropped counters. Filter sanitized rows by from, to, arm, cache procedure and pending/readiness condition, since default aggregation is destination-oriented.

Do not accept timeout/error/incomplete routes as responsiveness success. Backend queue/build/Mongo timing is copy-only; missing Production indexes/load and the auth relay round trip must remain explicit. A controlled same-copy frontend comparison is still useful.
