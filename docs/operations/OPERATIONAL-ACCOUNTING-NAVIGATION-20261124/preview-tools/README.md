# Private PR2095 Preview navigation observer

This package is temporary diagnostic tooling outside product source. It has not
measured a real browsing session. It does not establish navigation acceptance,
financial accuracy, or representative-data performance on its own.

Source adapter reviewed against:

- Candidate HEAD: `9a851854132bd71be922c4aae24b4cc20a546e41`
- Candidate tree: `436199d239a75910d94d441e9eb47408aea80080`
- Exact local checkout: `/workspace/scratch/cea53c309ef8/mezan-navigation`

`sha256-manifest.json` identifies these observer files. The Vite plugin also embeds
the actual observer module SHA-256 and validated source HEAD in each export. Parent
task owns the durable evidence checkpoint; this package must not be deployed or
merged into product source.

## Files

| File | Purpose |
| --- | --- |
| `observer.mjs` | Passive browser observer and a visible Arabic diagnostics panel |
| `observer.css` | Styles scoped to the diagnostics panel |
| `preview-observer-plugin.mjs` | Private Vite serving plugin; external same-origin assets only |
| `observer.test.mjs` | Narrow Node assertions for sanitization and percentile calculation |
| `sha256-manifest.json` | Exact byte lengths and SHA-256 hashes |

## Include from an external private Vite config

Keep the diagnostic directory and config outside the clean candidate checkout.
Append the plugin to the existing private runtime config, preserving the product
Vite config, CSP, authentication, and existing authorized backend configuration.
For example, inside that private config:

```js
import previewObserver from '/tmp/OWN-PREVIEW/preview-tools/preview-observer-plugin.mjs';

// Append to the configuration already obtained from the product Vite config.
config.plugins = [...(config.plugins || []), previewObserver({
  sourceRoot: '/tmp/mezan-pr2095-preview-20261125/source',
  expectedSourceSha: '9a851854132bd71be922c4aae24b4cc20a546e41',
  previewOrigin: 'https://salla-analytics.preview.emergentagent.com',
  arm: 'candidate',
})];
```

Start that private Vite process with `MEZAN_PREVIEW_PERF_ENABLED=1` and its external
`--config` path, using the already pinned toolchain. Do not add this code to the
product config or product HTML. This plugin supports Vite **serve** only. It
validates the configured HEAD and rejects a tracked dirty checkout. The allowed
remote host is an explicit HTTPS `*.preview.emergentagent.com` host; local loopback
is also supported. The browser observer separately requires the same host.

The two injected elements are an external module script and an external stylesheet.
There is no inline code, CSP relaxation, API proxy, authentication code, backend
configuration, fixture, or financial data import here. Vite/React Fast Refresh
configuration remains the private runtime owner's responsibility. Measurements
from a source-serving Vite runtime must be labelled as such, separately from
measurements of an optimized release build.

### If the Preview ingress rewrites the asset Host

The diagnostic middleware rejects an unknown upstream Host with the fixed body
`Preview host required`. If that exact response is observed on either diagnostic
asset, temporarily set `debugAssetHost: true` in the external plugin options and
restart only the private Vite process. It logs at most four diagnostic asset
requests: the fixed path, GET/HEAD/other method label, parsed Host hostname, and
allow/deny decision. It never logs other headers, query parameters, credentials,
raw URLs, or application API requests.

Inspect that local server log. Pin **only the observed, verified ingress hostname**
with `assetRequestHosts: ['OBSERVED-HOSTNAME']`, then set `debugAssetHost: false`
and restart the same private process. The list accepts at most four literal DNS
hostnames, with no wildcards, URLs, ports, or production domain. This changes only
the diagnostic asset middleware's explicit Host allowlist. The browser's exact
`data-allowed-host` remains the configured public Preview hostname. Forwarded-host
headers are never used to authorize a request. Do not infer or guess the ingress
hostname, broadly allow all hosts, or weaken the browser host check.

For a baseline arm, use an independently clean, immutable authorized baseline
checkout and its own exact HEAD; set `arm: 'baseline'`. The DOM adapter was reviewed
against the candidate only. Verify the baseline has the same relevant semantics
before comparing it. Never identify a baseline as the candidate HEAD.

## Use the visible panel

1. Open the authorized Preview session and reach a supported finance page through
   the normal existing login. Check that this is the intended source and dataset.
2. Expand **قياس المعاينة**. Leave the cache label **غير مصنف** unless the test
   procedure independently establishes a cold or warm state. The label is an
   operator annotation; the observer does not clear or inspect application caches.
3. Click **بدء القياس**. Use genuine visible navigation links and report tabs.
   Measurement starts at the trusted click event timestamp. It neither drives
   navigation nor synthesizes clicks or requests.
4. Include a navigation away while **جارٍ قراءة التقرير…** is visibly pending.
   The sample records that source state. Later-completed ResourceTiming entries
   may also establish retrospective request overlap at that click.
5. Click **إيقاف القياس**, then **عرض JSON** or **تنزيل JSON**. These controls only
   display or download the observer's in-memory sanitized results. Export before
   reloading or closing the document; nothing is persisted automatically.

Only follow existing read-only navigation for this task. The observer is not a
write blocker and cannot prove that the rest of the application issued no writes.
No test requires adding a record, recalculation, posting a transaction, updating a
provider, or changing an access policy. Do not manufacture data or authentication
to obtain a measurement.

## Supported vocabulary

All eight main destinations are allowed: `home`, `assets`, `log`, `parties`,
`reviews`, `position`, `reports`, and `tools`. The 12 report tabs are `summary`,
`profit`, `providers`, `accounts`, `suppliers`, `employees`, `couriers`, `drivers`,
`advertising`, `other`, `expected`, and `reconciliation`. The saved report data for
some tabs can be shared; absence of a new request is not itself an error.

The adapter matches `#finance-main`, selected navigation, breadcrumb, destination
heading, and selected report tab. It also recognizes a fixed set of existing
specialized finance paths for shell timing. Their data readiness remains unknown.
IDs and arbitrary query parameters are never retained. Resource/account ledger
routes and financial entry forms are not included. Button-driven navigation other
than the known report tabs, browser Back/Forward without a captured click, reload,
and initial document loading are not navigation samples.

## What an export measures

| Field | Meaning and limit |
| --- | --- |
| `click_t` | Trusted event timestamp relative to the document's performance time origin; an invalid timestamp excludes the sample |
| `input_queue_delay_ms` | Delay from event timestamp to this capture listener; not total click latency |
| `url_observed_ms` | First passive observation of the expected allowlisted URL after the click; not exact `pushState` time |
| `shell_committed_ms` | First matching destination shell observation, including selected navigation/heading/tab |
| `shell_two_raf_task_proxy_ms` | Matching shell, two animation-frame callbacks, then one task; an explicitly labelled response proxy, not a guarantee of paint or functional interactivity |
| `data_ready_proxy_ms` | Separate observation of known data/empty-state DOM markers; not data completeness, freshness, or accounting correctness |
| `source_report_pending_at_click` | Whether the source displayed the report loading status when clicked |
| `navigation_long_task_count` / `navigation_long_task_overlap_ms` | Observed Long Tasks overlapping the click-to-shell-proxy interval, if the browser supports them |
| `retrospective_completed_resource_overlap_count` | Completed, retained, same-origin allowed API timing entries that spanned the click; not an inventory of all outstanding requests |

No request is intercepted or created. ResourceTiming does not disclose HTTP
methods, bodies, headers, or exact abort/cancellation semantics. Some aborted or
cross-origin resources will be missing. An entry for a read-demand URL does not
prove a DELETE, cancellation acknowledgement, or absence of POST. Check those
requirements independently through an authorized existing network/log surface.

Only fixed route keys (`view`, `report_tab`), fixed API categories, timings, counts,
viewport, capability flags, and source/observer identities are exported. Request
IDs are replaced with a fixed redacted category. Credentials, tokens, account
names/IDs, arbitrary URL parameters, raw DOM content, request bodies, and headers
are not exported or stored. The observer does not access cookies, local storage,
session storage, or application objects.

## Valid samples, bounds, and comparison

Shell statistics use samples with a valid event timestamp, visible document, and
completed shell proxy. A hidden document invalidates its active sample. URL and
data proxy counts are reported separately. A failed/missing selector stays absent;
it never becomes a fabricated zero. Visible errors are counted separately and can
coexist with a responsive shell. Review failure/timeout/error samples alongside
successful timing percentiles to avoid survivorship bias.

Groups are per destination and operator cache label. `n` is the count of valid
values for that particular metric. p50 is the median; p95 uses nearest rank and is
withheld below 20 valid observations in that group. Twenty is a minimum display
gate, not a claim of statistical confidence. Do not pool different destinations or
compare unmatched datasets, builds, hardware, background activity, viewports, or
cache procedures as if they were equivalent. No automatic performance pass/fail
or claimed improvement is produced.

Memory retains at most 300 navigation samples, 2,000 resource entries, and 1,000
Long Tasks. Oldest entries are dropped with explicit counters. Dropped entries,
browser resource-buffer limits, and paused recording can make overlap evidence
incomplete. Resource response-status and Long Tasks support vary by browser.
Unsupported entry types yield `null` with an explicit reason, never a measured
zero. Long Task interval metrics also stay `null` until the shell proxy finishes.
The observer stops per-frame checks after the shell settles or 15 seconds and
ends an unfinished active sample after 120 seconds. Mutation observation continues
passively while a sample is active. DOM query scan time is reported; this is only
part of instrumentation overhead. Panel rendering/export and browser observers
also cost time. Use the same instrumentation on comparable arms and retain these
limitations in any benchmark report.

## Local sanity checks

From this directory:

```sh
node --check observer.mjs
node --check preview-observer-plugin.mjs
node observer.test.mjs
```

These checks validate syntax and pure-function sanitization/statistical examples.
They do not execute the application's DOM, authenticate a session, collect real
timings, verify backend isolation, or satisfy end-to-end acceptance.

The three commands above passed with exit 0 during implementation. A separate
local plugin check also accepted the clean exact candidate, rejected a mismatched
HEAD and a production origin, and verified that the two injected asset tags have
external URLs and no inline content. Independent static review found one issue
(unsupported performance APIs yielding measured-looking zeros); it was corrected
to explicit null values and re-reviewed with no remaining material finding in the
assigned instrumentation surface. Browser verification remains outstanding.

The ingress follow-up check passed locally: an explicit internal hostname was
accepted, unknown/malformed hosts and forwarded-host-only attempts were rejected,
unsafe hostname options were rejected, GET/HEAD restrictions were preserved,
diagnostic output was bounded to four sanitized entries, and the browser host
attribute remained the configured public Preview hostname. This used local
middleware calls, not remote traffic; the real ingress hostname must be observed
before adding it to the private runtime config.

## Current private runtime layout

`runtime.config.mjs` is the concrete external config for this continuation.
Place this tooling directory beside the exact `source` checkout under the
candidate-owned temporary directory. It imports the unmodified product Vite
config and appends the observer, retaining its CSP, origin allowlist and client
configuration. HMR is disabled for this measured source-serving runtime.
Set `NODE_ENV=production` to avoid the development React refresh preamble,
`MEZAN_PREVIEW_PERF_ENABLED=1`, and the existing authorized Preview backend origin.
Start with the already verified pinned Node executable and Vite `--config` pointing
to this file. Never put database URLs, cookies, passwords or other secrets in
frontend variables or this config. The exact private process launch and observed
integration result are recorded separately in the parent task checkpoint.

### Verified ingress mapping

The browser's request for the observer asset reached this task's Vite process
with `Host: salla-analytics.cluster-9.preview.emergentcf.cloud`, observed through
the bounded host-only diagnostic on 2026-11-25. The concrete private config now
allows exactly that hostname for the two static diagnostic assets and disables
`debugAssetHost`. The browser origin remains exactly
`https://salla-analytics.preview.emergentagent.com`. This host is already within
the unchanged product Vite allowlist. No forwarded header grants access.
