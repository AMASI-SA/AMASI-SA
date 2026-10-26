# Independent Snapchat account display

User steering,2026-10-26: separate Snapchat advertising accounts in cards,
hourly charts and advertising expense details in the executive profit summary.
Do not display USD and Saudi accounts as one Snapchat amount.

Task branch:`codex/snap-account-dashboard-20261026`.
Starting reviewed Production:`33c2f1ca440954e4cce0c52e96a8f2c3e6673678`
(TikTok1533, including merged Snapchat1527). Own worktree:
`/workspace/scratch/2f1d2cc290e6/mezan-snap-accounts`.

## Required behavior

- Each authorized enabled Snapchat account has an independent card, native
  currency/amount, SAR equivalent, quality, source observation time and graph.
- A shared SAR axis uses each account's actual SAR hourly/daily values. Never
  plot native USD on that axis, invent hourly allocation or fill future hours.
- A known USD account remains visible when Saudi account evidence is missing.
  Valid observed Saudi0 is provisional and retains its own source/lag wording.
- Executive expense details have separate account spend rows. Global finance
  and profit continue using the validated complete portfolio once. Unknown
  spend or conversion evidence cannot become0 or computed client profit.
- Account-level orders, sales and ratios require account attribution. Do not
  copy provider totals into account rows or allocate by spend share.
- An explicit account array, including[], suppresses aggregated Snapchat
  display. Legacy consumers without this contract remain compatible.
- Identity is accountID, not currency. Owner/disabled isolation, two accounts
  sharing a currency, selected-range races and null gaps need regression proof.

## Bounded implementation

Existing owner-scoped single-account reports feed an allowlisted account
display contract through the platform-spend route. Reuse the same reads;
introduce no account query/provider refresh or independent financial reader.
Each object contains account_id/display_name/currency/timezone,
projection_timezone,total_native,total_sar,daily_sar,daily_state,hourly_sar,
and safe quality flags/reasons/actual observed_at. Amounts/points remain gated
by that account's evidence. Aggregate authority and source finality stay intact.

Frontend cards/chart normalize the contract and render account series
independently. Executive details consume the same-filter platform-spend display
snapshot already attached by the existing page read. Canonical financial JSON,
read oracles and frozen executive reducers remain unchanged. Root owns integration, executive display, verification and publication;
the initial scoped authors reviewed the contract before disconnection. No ad/budget activation is a test.

## Previous release and current state

PR1527 merged sourceCE/intentB to ProductionM39f; all45final checks passed,
and actual clean-clone Emergent rehearsal passed92.51s. Root did not publish
that release: first official build refused derived backend __pycache__.
Retry input timed out, and Code Server session expired. Secure browser sign-in
was completed and positively verified. Fresh read found the initial failed
official result unchanged, no cleanup record, `/app` already at current33c,
and shared guard inactive. No own prepare/prepublish/lease or final publish
click occurred. Preserve other conversations' newer changes and releases.

This new user goal supersedes publishing the old CE artifact. It requires a
fresh source/intent pair directly on current reviewed Production, exact CI,
actual clean-clone rehearsal, governed release and live account-display plus
original hourly-chart/executive-profit acceptance. No new source tests have
run yet. This initial checkpoint is WIP, not release approval.

Workspace disconnected during implementation, and the browser tool is now
unavailable. Root recovered the executive table/page source and regression
cases directly against immutable GitHub base33c; these are preserved in this
explicit WIP checkpoint. The prior agents could not resume under the available execution mode. Root
reconstructed backend and cards/chart source against the approved contract and
immutable base, and preserved the complete source remotely. Byte-identical
recovery of uncommitted workspace files is not claimed. No new local tests or live
publication are claimed. Exact remote CI and governed deployment remain
required; the latter is blocked until the execution/browser environment returns.

The complete reconstruction contains independent account cards, native amounts
and SAR equivalents, common-SAR-axis account curves, per-account observation
and quality, and separate executive expense rows bound to the existing saved
same-filter platform snapshot. Global financial calculations/reducers and
canonical financial JSON remain untouched. The actual JavaScript normalizer
passed a direct isolated smoke check for knownUSD/unknownSAR, observedSAR0,
future-null and explicit-empty behavior with zero native calls.

Initial remote checkpoint23aebf52 CI confirmed backend/account-display and
executive unit tests. Three workflow failures traced to one existing mobile
header expectation (combined Snapchat column) and one exact JSX-call grep.
These were aligned to the required separate account presentation while
retaining keyboard scroll-region and real runtime behavior checks. New
cards/chart normalizer regressions and mobile account-region assertions are
included; fresh complete-source CI is now required. No deployment is possible
while the execution/browser environment remains unavailable. No release lease,
new intent or merge is authorized by this checkpoint alone.
