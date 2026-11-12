# PR1891 release evidence: saved courier recipient identity

## Current checkpoint: publication finished; official verification running

**All 11 applicable exact-B workflows PASS. PR1891 is merged and its single
authorized publication shows the new green completion state. Official
production verification is RUNNING; lease closure and actual courier preview
acceptance remain PENDING.** No successful PR1891 runtime verification or
resolution of the two live preview failures is claimed at this checkpoint.

The release owner clicked Publish changes once at 2026-11-12 02:08 UTC. The
new observed UI wording is `Deployment finished Nov 12, 05:18 AM`
(Asia/Riyadh), published version `0cdea89`. The official guard verification is
running in the owner's Terminal 2, with output directed to
`/tmp/mezan-pr1891-verify.json`; its result has not yet been recorded. The
previous completed verification remains the PR1890 baseline below.

Repository: [AMASI-SA/AMASI-SA](https://github.com/AMASI-SA/AMASI-SA).
Source branch: `codex/courier-recipient-identity-20261112`.
PR: [1891](https://github.com/AMASI-SA/AMASI-SA/pull/1891).
Continuation ledger: [Issue 1006](https://github.com/AMASI-SA/AMASI-SA/issues/1006).
The [immutable source checkpoint](https://github.com/AMASI-SA/AMASI-SA/blob/361978f854cecf572a8f3d3f0074f41fd91fcfd0/docs/operations/COURIER-RECIPIENT-IDENTITY-20261112/STATUS.md)
contains the defect and red/green history.

This file belongs to documentation-only branch
`codex/google-courier-live-evidence-20261112`, based at
`10975ab0a6b53d6cd4e0558d4f2ba2221aa4aa5e`. **Never deploy this branch or its
documentation checkpoints.** The release owner controls Git checkpoints,
merge, shared-workspace handoff, leases and publication. This evidence update
does not change source, tests, refs, provider data or release state.

## Immutable source and candidate identities

| Role | Commit | Root tree |
|---|---|---|
| Production base J, PR1890 merge | `0af4a3da57072a4995a2ebd0ca76720d17a80ee6` | `066dbee7956fe29e91e1acdae9ff77b7ef2645c2` |
| Reviewed source A | `361978f854cecf572a8f3d3f0074f41fd91fcfd0` | `ea42e438788dccfaea6c180115959fd718fb4583` |
| Intent-only B | `d6d7f3920d0b0454e22318a19b65808165d2fcd1` | `3949c7392232f21a46fa2cd0c87721a79d774116` |
| PR1891 deployment merge M3 | `e0f00c6ec47536aaaa28ac3fa580f3d6abc02c96` | `3949c7392232f21a46fa2cd0c87721a79d774116` |

Independent local Git reads confirm that B has A as its sole parent and the
A-to-B diff changes only `release/release-intent-v5.json`. The release owner
reported that the official B Git/intent contract passed with a clean worktree.
The separate independent B-contract review also passed. The owner reports a
normal merge with parents J/M2 and B; M3's complete tree equals B. Source A
and intent-only B remain immutable.

| Bound candidate value | Evidence |
|---|---|
| Runtime release ID | `rg5-efde36a8ffed59bbfbfaa1521401fe29a3d59923a59102cd0e34305a38a0e33c` |
| Intent bytes | `1268690` |
| Intent SHA256 | `bf3fa174cd942743bbf1c6b2ac9264fa57d75b0c00dc241aaf93a64b23ca8f2c` |
| Local and source-A CI candidate | Byte-identical |
| Separate independent manifest review | PASS: all 1175 backend, 273 release-control and 1149 frontend-source records |

The first local bootstrap omitted `MEZAN_RELEASE_BOOTSTRAP_INTENT=1` and was
correctly rejected for selecting the stale retained intent. That failed
attempt is not successful build evidence. The corrected governed bootstrap,
verification and freeze passed. The matching local and CI candidate bytes and
the separate independent manifest review establish candidate consistency;
they do not establish a deployed runtime.

## Reviewed behavior and invariants

The saved-recipient correction is limited to historical courier recovery.
Equal names after trimming/casefolding and strictly equivalent compact ASCII
Saudi phone forms can prove that the saved buyer and receiver are the same
recipient. Positive exact native integers and strings are supported; booleans,
floats, objects, foreign numbers, non-ASCII digits, controls and conflicting
identity cannot supply that proof. Gifts and genuinely independent recipients
remain blocked. The shared recipient policy and fresh-provider separation
guard are unchanged.

The two recovery list/history requests now use `per_page=50`, within the
[published pagination limit](https://docs.salla.dev/pagination) of 60.
Complete-page, total, next-page and unique-child checks remain unchanged.
This corrects a request-contract mismatch; it is not evidence that the actual
failed production requests were rejected specifically for requesting 100.

Successful recovery still returns an ID-free order QR, performs no provider
POST, and preserves the canonical source and superseded shipment IDs. Final
provider rereads, local material/workflow fences, cancellation inbox and
current authorization checks remain in force. No source repair, shipment
reactivation, driver handoff or physical printing is performed by these tests.

## Fresh tests and independent workflow review

| Verification | Result |
|---|---|
| Root integrated seven suites | 231 passed, zero skipped, exit 0, 14.88 seconds |
| Independent focused four suites on exact A | 173 passed, zero skipped, exit 0, 11.20 seconds |
| Focused breakdown | 37 new recipient, 4 new pagination, 114 existing identity and 18 race cases |
| Independent behavior/workflow review | PASS; no blocking finding |
| Source whitespace and worktree | `git diff --check` passed; reviewed worktree clean |

Independent command, from the source worktree with the existing isolated
Python test environment:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests \
/workspace/scratch/81f2839b514c/courier-test-env/bin/python -B -m pytest \
  --noconftest -p no:cacheprovider -q --tb=short --asyncio-mode=auto \
  backend/tests/test_courier_history_recipient_identity.py \
  backend/tests/test_courier_history_pagination_contract.py \
  backend/tests/test_courier_history_identity_recovery.py \
  backend/tests/test_courier_history_recovery_races.py
```

The root's integrated command also includes the existing generic-shipping,
generic-identity-recovery and explicit-completion suites. These totals overlap;
they are not additive. The focused route fixtures use synthetic provider data
and database fixtures, so they do not replace mandatory real-Mongo CI or live
acceptance.

The workflow parsed as YAML and its scope classifier parsed as Python AST.
Each new file occurs in the path triggers, owned test prefixes and mandatory
courier pytest step. No gate, existing test or failure condition was removed
or weakened. The shared recipient resolver and existing identity/race files
were byte-identical to the production parent.

Reviewed SHA256 values matched before and after the independent run:

| File | SHA256 |
|---|---|
| `backend/order_engine/store_courier_history_recovery.py` | `d80e8f27bd9c0e19a25c6feee7f32a2ae5ee7e182e8e9fec3c4befcb07aed3db` |
| `backend/tests/test_courier_history_recipient_identity.py` | `3815d9c0909787dc2762a28c6a8f8b56473a068096a219d90089caaa46fc2e22` |
| `backend/tests/test_courier_history_pagination_contract.py` | `eabc19d70d71721205337df6e12a9265c13a666156c3254b6e8593ca0a4d38b5` |
| `.github/workflows/shipping-print-boundary.yml` | `ccd76a756d762d1bc66ca7ee6f58b1f9768fc61f0548eaeda6d56e369002087a` |

## CI, shared handoff and publication chronology

- Source-A [Release Readiness 43137013902](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43137013902)
  supplies the candidate compared byte-for-byte with the local freeze.
- Exact-B [Release Readiness 43137930260](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43137930260)
  passed. All **11 applicable exact-B workflows passed**. TikTok Creator was
  skipped by its scope rule; that skip is not a test pass or a waived courier
  gate. The earlier running-CI checkpoint is superseded by these terminal
  results.
- Before the shared handoff, a fresh official guard status was inactive and
  tracked files were clean at production J/M2. The owner's fast-forward-only
  transition to M3 preserved all 120 unrelated untracked platform files
  byte-for-byte.
- The configured frontend `yarn build` completed successfully in **68.54
  seconds**. The official prepare and prepublish commands passed for actor
  `codex-courier-recipient-identity-20261112`, exact source A, deployment M3 and
  the runtime release ID above.
- One authorized Publish changes action occurred at **2026-11-12 02:08 UTC**.
  The newer green `Deployment finished Nov 12, 05:18 AM` (Asia/Riyadh) and
  published version `0cdea89` were then observed. This records the actual UI
  wording, without inventing a literal `Deployment Succeeded` message.
- Official verification is **RUNNING**. Await its three exact-identity probes
  and the subsequent inactive guard status before claiming verified release
  completion. Then repeat both actual courier preview trials. A preview does
  not prove physical paper printing.

## Prior production acceptance remains the baseline

PR1890 remains the last recorded verified publication: version `d73f584`,
green `Deployment finished Nov 12, 04:02 AM` (2026, Asia/Riyadh), three official
verification probes and subsequent `active:false`.

Google November 11 acceptance **PASS** is retained: both the yellow card and
executive SPEND cell show the available **96.48 SAR** as incomplete, while
financial totals/profit remain incomplete. No forced sync or advertising
account change was needed. Courier acceptance remains **FAIL / OPEN** for both
actual refresh trials; no preview or physical printing success is claimed.
See the preserved [PR1890 release record](../GOOGLE-COURIER-LIVE-ACCEPTANCE-20261112/RELEASE.md).

Production publication changed by PR1891 at this checkpoint: **yes**, as shown
by the newer completed publication/version above; exact runtime verification
and live courier acceptance are still open. No real order IDs, customer
identifiers, names, phone values, credentials or provider payloads are included
in this record.
