# Post-deployment campaign snapshot refresh — source only

Base: 8d25f7a6fa9a310b319b753c32457e70987f7c51. Prior live guard verification passed; financial matching still has unresolved cases.

Confirmed defect: both pages watched only the campaign-ID set. A new order in the same campaign did not refetch the report. Backend TTL alone cannot refresh retained component state.

Fix: a shared bounded hook observes order IDs, status, amounts, quantities and totals; a new order revision gets a new tenant-scoped server snapshot. Repeated identical lists coalesce, old responses cannot overwrite the latest state, and no request is triggered on hover. A three-minute freshness bucket is noticed only through existing visible order refresh; no new polling/cron is added.

Tests: same campaign 30 to 31; unchanged lists; no independent polling; quantities/cancellations; delayed result; tenant isolation and request coalescing. Exact CI must pass before readiness.

Unresolved separately: TikTok visible name but no trusted campaign ID; Google campaign/account mapping; screenshot timezone America/Los_Angeles conflicts with earlier owner description America/New_York; missing Snap account-local spend. Do not guess campaign IDs from names/products or change account timezones. Read-only live evidence is needed.

No production merge/deployment, /app or release lease, provider sync, database or accounting/ad write. Preserve the accepted partial-spend policy. This patch does not claim the attribution/timezone issues fixed.

## Resumed source verification — 2026-11-14 (Riyadh)

The owner requested completion, direct Mezan verification and the previously
authorized update. Continuation is PR #1907; Issue #1006 has reached GitHub's
2,500-comment limit, so this task record and the PR retain the handoff.

The screenshot's reported day-start failure was superseded by exact-source
evidence. Do not change account timezones or alter `_window` without a fresh
reproduction. The USD Snapchat account's observed timezone is
`America/Los_Angeles`.

Recovered the exact nine-file final patch on task head
`5c08685d42811c1642a7acb2459d5225b5c27620`, using the original guarded scripts
from `a18ce6265325a979d3c797c9610c24e9de742e81` and
`442f2596635915edc604f36c58690d211a58a5db`. No hash guard was changed.
The resulting binary patch exactly matches `proposed.patch` in artifact
`14076642787`, run `43507119296`: SHA256
`13a6e8fbbd751cebb4e73f3719811117f7420f6469cc8b3024749800754c1391`.
The downloaded ZIP independently matches SHA256
`8ab8c05f63a3813ce8c6362836d5d0a86de644386c18d8fb91faad31c43ef6b0`.
That historical run passed 77 backend, 28 focused frontend and 3,693 full
frontend tests; its checkpoint push failed because its runner token lacked
workflow-write permission. A failed push is not a failed acceptance test.

Recovery checkpoint: `d1a0297e`. Latest production base
`53acc03a178362530f2cb45206fe11a4fb724d88` merged without conflict into the
isolated task checkout; integration checkpoint
`23903722c7e0a8a518e24875ca32301220084c5f`. All unrelated production changes
are preserved. No source from the isolated ops branches is merged.

Fresh integrated verification:

- Backend five-file campaign/native/Snapchat acceptance: 77 passed, exit 0.
- Frontend eight-file campaign/OrdersV2 acceptance: 28 tests / 8 suites passed,
  exit 0. Existing React test-environment warnings remain nonfatal.
- `git diff --check`: passed.
- Independent source reviews and final integrated CI/build remain pending.

Completed behavior: same-campaign new orders invalidate the snapshot; canonical
OrdersV2 totals/items participate in revisions; stale responses cannot overwrite
new results. Existing native Google/TikTok campaign snapshots and Snapchat v3
account-local daily facts supply bounded read-only evidence. Exact Salla UTM
labels without verified campaign IDs show commerce only, explicitly using the
store day, with spend/CPA/ROAS unavailable. No fuzzy name-to-ID matching, new
polling, provider calls, schedule changes or fabricated missing values.

Live baseline was inspected in Mezan: the existing card still rejects
Riyadh-day Snapchat spend for the Los Angeles account. This is baseline
evidence, not acceptance of the new source.

Production changed by this continuation: **no**. No shared `/app` changes,
lease, publication, provider/database/accounting/advertising writes. Next:
complete reviews and exact-source verification, freeze a new governed v5 A/B
pair against the latest production base, then serialize the already-authorized
publication and verify the actual runtime and cards.

### Final source preparation on the current production base

The recovered WIP and its ancestry remain preserved locally, and the original
guarded scripts/artifact remain remotely recoverable. The final source branch is
`codex/campaign-hover-final-20261114`, built directly above production
`53acc03a178362530f2cb45206fe11a4fb724d88`. Its initial scoped snapshot is
`c1d94301a69f835c0a4c842812d8f4d9a6267849`. This avoids importing an old intent
through merge history; the unchanged source-A classifier requires no intent-file
commits since J. No release guard or acceptance requirement was relaxed.

Independent frontend review found a real upstream integration defect:
`campaign_enrichment` inferred an advertising ID from any text of length 24+.
An actual OrderDTO containing a long Arabic TikTok UTM name reproduced the
failure before the UI. The minimal fix removes only that arbitrary-length rule;
explicit IDs and inferred numeric/UUID IDs remain supported. Six focused cases
cover label preservation and correct explicit identities; the existing Meta
lookup assertion now checks the tenant-scoped query rather than lookup order.
The regression was red (5 failures / 1 pass), then all 8 enrichment tests passed.
The normal source acceptance workflow includes this module and suite.

The combined final backend acceptance (six suites including enrichment) passed
85 tests, exit 0. Independent backend review also passed 53 existing campaign
tests and confirmed the native writer schemas, tenant/account/day boundaries,
FX evidence, incomplete-value semantics and bounded read-only queries. Focused
frontend acceptance passed 28 tests / 8 suites. Full integrated frontend and
fresh CI remain required before final readiness.

The existing Security Gate exposed GHSA-vvf4-9x63-8xvv in dnspython 2.9.0.
Both deployed dependency pins now use the maintainer's fixed version 2.9.2;
`backend/requirements.txt` and `scripts/release_backend_requirements.lock`
remain byte-identical. Source advisory:
https://github.com/rthalley/dnspython/security/advisories/GHSA-vvf4-9x63-8xvv.
The strict audit is unchanged; no exception or ignored vulnerability was added.

Direct Git push from the scratch shell has no configured GitHub write
credentials. Save the exact reviewed tree through the connected GitHub API,
verify tree equality, then use its canonical remote source SHA for all governed
builds and the intent. Do not claim a local commit was uploaded until the remote
branch has been read back. Production publication and live acceptance remain
pending; no shared workspace, lease or business data has changed.
