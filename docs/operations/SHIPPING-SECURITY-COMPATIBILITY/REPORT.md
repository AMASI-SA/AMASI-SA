# AMASI_SHIPPING_PHASE1_SECURITY_COMPATIBILITY_GATE

Independent Draft [#1332](https://github.com/AMASI-SA/AMASI-SA/pull/1332).
PRODUCTION RELEASE = BLOCKED. This is source-level remediation and isolated
verification, not production acceptance. Exact final HEAD/TREE and fresh CI
outcomes are recorded in the final PR evidence comment; this report does not
treat an earlier green revision as proof for a later commit.

## Source and integration

- BASE: Production `200e66c7a6e60f04bd58fae716b3981ad07ce3d1`, freshly fetched
  from `origin/hotfix/prod-snap-meta-final` during this review.
- #1327: `0b706dee251eb2c2facfdb5e20c40872d78e2158`, applied first as
  `58185b36` with its net patch. #1331:
  `5402325b3a40b41cd7c3fffbf066a8934d600332`, applied second as
  `cacb3d34e3bbc2afd65c04ffcc1424daf73726a9`. Both applied cleanly.
- Security implementation checkpoint:
  `ba63797ced34645df49e2d7e911f49a77e97c11c`, TREE
  `c57f8757137b27387735c5dd251911c490d43c7e`.
- Branch: `codex/shipping-security-compatibility`. Original PR branches untouched.
- Production Release Intent remains byte-identical. No release preparation,
  merge, deployment, production restart, Android edit or live provider test.
- `FILES.txt` lists the full integration delta. `SECURITY_FILES.txt` separates
  the new security work from imported #1327/#1331 changes. The imported
  `qoyod_auto_unified/live_source.py` change only participates in canonical
  order-writer serialization/invalidation; financial rules were not changed.

## Root causes and PDF resource isolation

#1331 parsed native PDF data synchronously in the request process. Its 2 MiB
input cap, eight-page cap and text cap did not bound native CPU, allocation or
event-loop blockage. An asyncio timeout alone cannot interrupt synchronous
native code executing in that loop.

Downloads retain the original HTTPS/public-address/DNS-pinning checks, no
redirects, no proxies or credentials, PDF MIME restriction, 2 MiB streaming cap
and eight-second fetch timeout. Analysis now runs in a disposable Linux Python
process (`-I`), before any persistence. The child installs hard and soft OS
limits **before importing PyMuPDF or reading the PDF**:

| Resource | Enforcement |
| --- | --- |
| Address space | RLIMIT_AS = 512 MiB |
| CPU time | RLIMIT_CPU = 2 seconds |
| Parent parser wall deadline | 5 seconds including spawn/communication |
| Request cleanup grace | At most 1 additional second, subject to scheduler delay |
| Files/processes | CORE=0, FSIZE=0, NOFILE=32, NPROC=0 |
| Admission | Two slots per event loop/process, no waiting queue |
| IPC | At most 2 MiB PDF input, bounded tracking header, 129-byte result read |

The slot covers download and parse. Timeout/cancellation kills the process
group and reaps the child. A separately owned cleanup supervisor handles late
spawn/reap; its slot remains quarantined until cleanup is proved. Repeated
cancellation cannot release capacity prematurely. Even an `OK` result is
rejected if cleanup cannot be proved within the caller grace. Unsupported
platforms fail closed instead of parsing in the web process.

The original signature/EOF, encrypted/repaired rejection, page range 1..8,
exact AWB boundary matching and text limit are preserved. Hashing and insertion
occur only after successful validation and cleanup. Existing order/shipment
fences still run before ready publication. Parser crash/timeout tests assert no
stored document, `ready=false`, and removal of cached label URL.

**Limits of this design:** RLIMIT_AS bounds address space, not an aggregate
container memory budget. Two slots are per ASGI worker, not a fleet-wide parser
cap; aggregate allowance scales with worker count. This is resource/process
isolation, not seccomp, container or filesystem/network isolation against a
native-code exploit. NPROC is not reliable for privileged users. A permanently
stuck cleanup deliberately loses capacity until operational recovery; this
task performed no production restart. Production must independently validate
Linux limits, worker count, container headroom and alerting.

## Capability URL security

The 300-second TTL is unchanged. A 32-byte random bearer capability is looked
up by SHA-256 and binds immutable bytes/hash to owner, order, carrier, shipment,
AWB, provider source URL and observed shipment status. Issuance remains
authenticated; Build44's subsequent download intentionally has no API auth
header. Thus owner binding selects the correct owner's evidence, **not the
identity of whoever possesses the URL**. Workflow URLs still contain the
usable token; storage is not exclusively hashed.

Every accepted download checks current local status, refreshes provider facts
using GET only, compares all identity fields, reloads document bytes/hash and
expiry, then applies the final canonical status fence. An additional synchronous
expiry check after the last await closes the previously uncovered window where
the token could expire while waiting on that fence.

Repeated bearer use is bounded using Mongo claim/CAS admission: two shared
download slots, 45-second lease, one-second global slot cooldown, five-second
per-token cooldown and at most four admitted attempts over its lifetime.
Refresh is capped at 12 GETs / 30 seconds. Stale claim release cannot clear a
successor. A duplicate 429 neither starts more provider reads nor revokes the
first legitimate download. Other known-document failures revoke cached ready
state. Crash recovery waits for lease expiry; it never issues a provider POST.
This admission is an additional document-read guard, not a reconciliation
worker redesign or an infinite-rate guarantee under stalled storage.

Application log redaction covers URL paths (including encoded slashes),
structured token fields, args (including HTTPX URL objects), extras, stack and exception text. It preserves
uvicorn's access-log tuple and chains existing logging factories. A post-extra
`Logger.makeRecord` pass fixes an audit-discovered gap for later-added handlers.
Protected responses, including errors, receive `no-store, private`,
`no-referrer` and `nosniff`. Unexpected pre-response exceptions become generic
non-200 JSON and a fixed log message.

**Unproved outside the application:** deployed reverse-proxy/CDN access logs,
APM direct capture, viewer history and OS sharing. Their independent audit is a
release blocker; the repository filter does not certify those layers.

A lost/interrupted/expired link requires an explicit authenticated refresh and
a new GET validation; an expired download returns an error, never a new AWB.
The four-attempt/cooldown policy can produce 429 and must be checked on a real
viewer. A copied link remains a bearer until expiry/admission rejection. Bytes
already downloaded or handed to an external viewer/printer cannot be revoked.
Fresh local fences and provider GETs do not prove a remote order cannot change
after the final read; no remote atomicity claim is made.

## Carrier and ordering policy retained

- External SMSA/iMile printing accepts only current shipment `created` plus
  confirmed provider order `completed` and complete local assembly.
- Terminal/unknown shipment states, multiple ambiguous shipments, changed
  carrier/identity/AWB and older/conflicting observations fail closed.
- Store courier retains its existing internally generated document path and
  canonical carrier/order checks. No new external AWB is introduced.
- The public completion/AWB POST remains disabled. Reconciliation remains
  GET-only with original attempt identity, version/claim CAS and lease checks.
  This is not a claim that every unrelated shipping route in the repository is
  POST-free; those routes were not re-enabled or redesigned here.
- Original delivered races, reverse/late webhooks, physical/virtual stock and
  canonical writer tests remain in the focused Linux matrix.

## Isolated evidence and failures

Initial Linux CI at `69ac6ae7` passed phase1/outbox: 130 tests + 25 subtests,
including all 17 then-existing PDF isolation cases. Independent audit then
added three cleanup/cancellation cases and fixed structured-extra logging.
The final matrix requires **20 PDF cases executed on Linux**, never accepting
the Windows skips as resource-limit proof.

Coverage includes real child parsing of complex eight-page PDF, nine-page and
encrypted rejection, damaged PDF, HTML, wrong/prefixed/suffixed AWB; real
600 MiB allocation refusal under 512 MiB, real CPU spin termination, wall
timeout with responsive loop heartbeat, spawn cancellation, double cancellation,
quarantined capacity and crash/timeout no-publication routes. Resource probes
exercise OS limits directly; they are not claimed to exhaust every malicious
PDF class. All fixtures are local/synthetic.

Capability tests: eight sanitizer/header tests, four admission/lease tests and
two final-expiry/concurrent-download route tests. Existing route, parser,
carrier, worker, CAS and terminal-policy suites are retained. The focused CI
blocks external test DNS/socket calls and uses disposable loopback Mongo
Replica Set plus standalone refusal coverage. One existing redundant memory
rollback skip is allowed; its real-Mongo equivalent must execute.

Failures are retained in the record:

1. Initial Linux Release Readiness failed because #1331 added
   `PyMuPDF==1.28.2` without updating `scripts/release_backend_requirements.lock`.
   The reviewed dependency copy now matches runtime requirements exactly;
   no release gate/test was weakened and Release Intent was not regenerated.
   Linux Backend preflight passed at `ba63797c`.
2. Local Windows combined run: 31 PASS, 22 FAIL, 15 Linux-only SKIP,
   19 passed subtests. Failures are in release-adapter POSIX permission/symlink
   contracts (Windows modes 0666/0777 and missing symlink privilege). They are
   not silently converted to passes. Linux CI is the acceptance environment.
3. Early local Ready benchmark invocation failed module lookup; rerun with
   explicit backend/test paths passed. Only the successful run is timed below.
4. CI at `dac29a36` ran 237 Ready/review tests successfully but the strict
   execution gate rejected 39 skipped Production TikTok compatibility cases:
   their explicit `TEST_TIKTOK_MONGO_URL` was missing. The matrix now supplies
   the same isolated Replica Set under that fixture's required variable name.
   The tests and no-unexpected-skip guard are unchanged. Fresh CI is required.
5. A final logging probe reproduced a token leak in HTTPX's lazy `%s` URL
   object argument (ordinary strings were already covered). Explicit URL-object
   sanitization now covers `%s`, `%r` and nested structured extras. The new
   regression failed before the fix and passed afterward; final Linux CI must
   include this eighth capability test.

No external Salla/carrier request was used. Zero POST is proved within the
reconciliation/load and guarded route fixtures, not by sampling production.
No real inventory or accounting database was contacted.

## Load and Ready measurements

`LOAD.json` records 2,000 requires_attention orders / 100 owners per case on an
isolated Mongo 8.0.12 Replica Set. Its source and test hashes are included.

- Sixteen simultaneous workers: one job accepted, fifteen no-op; immediate
  cooldown performs no additional candidate query/provider GET.
- Eight admitted jobs over 105 seconds of simulated business time: 16 GET,
  zero POST, max concurrent provider calls 1. Local job samples ~394–444 ms.
- Sixteen read attempts: 32 GET, then no further reads; backoff 60..3600 s.
- Protected stock/order/ledger collection hashes unchanged and command
  listener records zero writes to those six collections after fixture seeding.
- **Fairness limit:** oldest-16 candidate window can hide 99 owners behind
  one clustered owner's cooldown; observed at 15/30/45 seconds. No cross-owner
  latency guarantee. Safety bounds pass; bulk drain time is not certified.

`READY.json` reuses the existing benchmark against this task's port 27945 /
`shippingSecurity` replica set (only URI/output overridden). At `ba63797c`:
Ready samples 78.318 / 69.776 ms with zero provider I/O; same-owner writer while
GET is held 9.432 ms; worker cycle 91.046 ms / two GET; cooldown 2.648 ms.
Explicit owner hold 201.006 ms made same-owner writer wait 211.558 ms while a
different owner completed in 23.280 ms. These are single synthetic samples,
not production percentiles, throughput or SLOs. Successful PDF publication is
tested separately, not represented by the in_progress load fixture.

## Production and Android compatibility

Production order/shipping behavior is covered by original suites and canonical
writer tests. Production TikTok management tests were included to check the
fresh-base integration; its runtime files and Release Intent are preserved.

#1330 `4616ed11557c85daee0d49613c4fd6e654720f70` applied cleanly to a separate
temporary index with `--cached --3way --check` (17 paths). It was **not adopted**.
Its required displayed-review `approval_token` conflicts with Build44 #269,
which sends only `expected_revision`; new approval would reject with
`review_approval_required`. Textual compatibility does not equal rollout
compatibility. No approval guard was relaxed.

Exact Android #269 source `5680e7b704d98643dde22fd8164d3bd89ed2db7c` retains
Ready/carrier-label routes and unauthenticated PDF capability download. Fresh
source-contract runs passed: Ready operations 27; Ready transport 15; shipping
root fixes 23; carrier-print flow 4 plus store-recipient check; store-courier
auto-ready 8. Zero real network calls. This establishes source-contract
compatibility for this integration, not Samsung/printer acceptance or #1330
approval compatibility. See `ANDROID_PLAN.md` for APK identity, expiry/retry,
terminal-state, viewer, barcode, paper-size and physical printer checks.

## Remaining gates before any production decision

1. Independent review of exact final tree and CI, including resource isolation
   and changed bearer admission behavior.
2. Hosting-level proxy/CDN/APM redaction and cache/header evidence.
3. Linux process limits, worker-count memory/CPU headroom, parser slot
   exhaustion monitoring and sustained representative capacity evidence.
4. Owner decision on the disclosed candidate-window fairness limit.
5. Samsung/device/real-printer acceptance using the controlled plan.
6. Separate coordinated decision if #1330 approval is to be included.
7. A future independently authorized release protocol/intent for the reviewed
   final source. Current Production intent is preserved, not reusable evidence
   that this new source has been released.

Stop after report and Draft review. SOURCE TEST PASS is not PRODUCTION READY.
