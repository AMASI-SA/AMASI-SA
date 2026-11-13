# Campaign hover — combined source checkpoint, 2026-11-13

## Scope and authorization
Owner authorized continuation, merge and governed publication in the conversation. Production base inspected: `b64121c265d8d6e158df0d9fb88debc52c95317d`. No production mutation, provider campaign write, accounting write, deployment or live data reconciliation was performed in this checkpoint.

## Combined source
Integrate PR1903 (homepage and independent Salla commerce) into PR1905 (New York/Riyadh hourly spend). Preserve all unrelated production changes and the current release intent bytes. The second parent is the exact PR1903 head `547b22a88774a59a48029e81d5ee0eadb6f97df5`; the first parent is PR1905 head `bedd1eb910281642f554bbeeed59bad76d41b1cd`.

## Repairs discovered during review
- PR1903 had only replaced the first Mongo `$in` predicate. Raw-only Salla UTM orders without spend could still be excluded. The query now derives campaign-ID predicates from the existing safe Salla attribution projection, independently of spend.
- Two Snapchat accounts cannot be resolved by a one-account fallback. Read the already-synced campaign catalogue for exact account binding, without a provider call.
- A requested full-day window did not prove returned hours were complete. Validate each elapsed UTC hour from account midnight, including 23/25-hour New York DST days. Missing, invalid, conflicting or future hours cannot produce a trusted full spend amount.
- Reader now requires matching midnight, account timezone, elapsed coverage and complete-hour evidence before using the local spend in CPA/ROAS. Commerce remains visible independently.
- Preserve source accessibility labels expected by homepage tests. Prevent tap on the badge from navigating the enclosing order link. Format update time in the account timezone.
- Remove tenant-unscoped module-global financial cache from the browser. Component state remains prefetched, and the server owns the bounded tenant-scoped snapshot cache. No hover/focus/tap fetch, new cron or polling added.

## Validation
Python syntax compiled locally. Isolated local contracts passed: 16 hourly coverage cases and 19 query-aware reader cases; local dependency doubles were used where packages/project files were unavailable. These are NOT substitutes for the repository CI. Exact combined-commit CI and browser regression still required.

## Release/acceptance gates
1. Run full CI on combined source, including existing source isolation, frontend accessibility and release readiness.
2. Confirm the active provider sync path returns dense HOUR rows and handles partial/paginated failures. Do not infer missing hours as zero.
3. Compare real NY and Riyadh campaign spend/FX and Salla order IDs/quantities to the respective account-local window; measure warm/cold read latency.
4. Freeze reviewed V5 source A and intent-only B before production merge/publish. Never merge source A alone and substitute an older intent.
5. Check `/app` release guard lease before any terminal changes. Governed Emergent publish requires explicit runtime evidence and three production probes. Source merge/CI is not deployment proof.
