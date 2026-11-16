# PR #1955 — Ready refresh recovery publication evidence

**Evidence only. Never merge this branch as application source.**

Application PR: https://github.com/AMASI-SA/AMASI-SA/pull/1955  
Evidence branch: `codex/ready1955-publication-evidence-20261116`

**Publication and official live verification completed. Live Production changed by this task: yes.** Platform version **`231aae8`** completed at **`2026-11-16T03:41:00+03:00`** (`00:41 UTC`, UI minute precision). The one bound verifier returned `verified:true` with three sequential probes; its result was saved at `2026-11-16T00:56:42.399185+00:00`. Fresh status immediately afterward was `active:false`. Final local and remote Git were exact M with the accepted tree and clean tracked files at `2026-11-16T00:56:52.341376+00:00`.

## Exact published release

| Binding | Accepted value |
| --- | --- |
| Production branch | `hotfix/prod-snap-meta-final` |
| Source A3 | `1accaa42e132ca1232ce86b3122971b48169ca81` |
| A3 tree | `be16191c9c2498a942fa7974455c6c24e6fac005` |
| Sole source parent/base J2 | `2589f440106a7c5d463ee03eac3617d113319673` |
| Intent-only B; sole parent A3 | `dd4fc24dbef4b2d6e4abafac5ee5ee47d14e802b` |
| Normal merge M; ordered parents J2, B | `b05cd3c9b25ce22df1b2dd06c23d97606bd632c6` |
| Identical B/M tree | `aded5cb29b69b5bb09b7386932ad9fc5c35d1f44` |
| Intent Git blob | `bf8f2ac59543bbde3e1c73fa0a43a768ca9a7f22` |
| Intent SHA256 | `0d90b88def88e0c84d595376d050e80f3e18493bf9ca71284382af1af8754899` |
| Runtime release ID | `rg5-255b2b13ed893324729b31c21409fa1be3be98d8c536485f712424d70bab4b4e` |

B and M differ from A3 only in `release/release-intent-v5.json`. Independent clean-clone validation accepted the exact raw Git objects, source topology, intent bytes and unchanged official source/intent validators. A3 retains J2's inherited intent unchanged.

## Shipped behavior and remaining scope

An explicit one-order refresh in mobile Orders or Assembly, or the owner web Orders action, now retries the existing guarded local preparation continuation after a successful authoritative source refresh. The continuation remains limited to eligible direct/mixed preparation, with current approval, source, committed allocation, stage and live actor authorization checks. It uses provider readback with dispatch disabled; successful source refresh remains distinguishable from preparation that is still pending. Repeated confirmations revalidate current scope under the operational-owner transaction.

The separately reported received-garment source/approval representation guard remains unresolved. This release does not override that guard or automatically mark Ready, invent receipts, complete services, or perform a case-specific business-state repair. No private order/customer identifiers or payloads belong in this public evidence ledger.

## Accepted source, CI and package evidence

- **A3:** all 13 required exact-head workflows passed. Review run `43775290295`, job `133000104970`, passed 35 focused, 329 Android, 828 review/provider, 124 source-snapshot and one conflict case, with the mandatory zero-skip/failure/error gates satisfied.
- **B:** all 13 required exact-head workflows passed. Review run `43776446789`, attempt 1 / job `133003708652`, exercised actual MongoDB 8.0.12 transactions and the same suites; the focused group also recorded 35 subtests. The separate TikTok Creator workflow was inapplicable by its exact branch condition and skipped; mandatory test gates retained their no-skip requirement. Complete receipt: `evidence/ci-B-final.json`, SHA256 `94f0644693d8706d7c8cd51d56455aa56128cd64c2e16ccc992c8c60e9068ba5`.
- **B host acceptance:** Readiness `43776446819`; actual Host20.20.2 job `133003993603` and Host24.19.0 job `133003993626` passed fresh-clone adapter, isolated-package/identity and real HTTP JSON-MIME checks. Downloaded evidence ZIP digests matched GitHub. Both proof objects were equal: raw-file SHA256 `325543cb75ef5de23df3886adca21ebebc8434b5255b6c1773cd3c25ebd6688a`; canonical JSON SHA256 `7f42de559eaccc3c8c9129017639ed5bffaae6d84600047ebf61a9fd295e6fdd`.
- **M:** Security `43779331347`, CodeQL `43779331479` and Readiness `43779331359` all passed on attempt 1. Both actual M host package objects matched accepted B, with package and HTTP steps successful. Receipt: `evidence/ci-M-final.json`, SHA256 `56f7b5fe720f352ba09a09fbe4d5765fda282b9f7d42788a24ed473108406b69`.

### Preserved first B timing failure

Operational Balance run `43776446776`, attempt 1 / job `133003708822`, had **4963 passed / 1 failed**. The existing process-CPU ratio check at 4000 distinct issues failed although output equality passed: old CPU `0.2859268099999923`, new CPU `0.24774235800001065`, required `new < old * 0.65`. Preserve `evidence/ci-B-operational-attempt1-failure.json` and its original job evidence.

After source/log review and one isolated local diagnostic, root requested exactly one failed-job rerun on unchanged B. Attempt 2 / job `133006178250` passed at `2026-11-15T23:58:21Z`: **4964 passed**, then **16 passed +18 subtests**, with the required no-skip/failure/error gate. Artifact `14186977242` has ZIP SHA256 `2f3337727ab90a92d64d2524b7597f7fd7414ddad21e6cfc199f530b0c324517`. No source, test or threshold changed. The timing cause remains unproven; attempt 1 is retained and is not conclusively labeled flaky. No further rerun was requested.

## Original handoff and publication completed

1. PR #1951's owner completed its own three-check verification and closed its lease. Root independently observed the original guard inactive at `2026-11-16T00:12:53.324590+00:00` before the normal PR #1955 merge. No prior lease was reused or cleared by this task.
2. After all M gates passed, the reviewed rehearsal helper ran **once**. Its SHA256 remained `f795b6ee49cafbef56745e11e85f85ef476d9cd7eae529338ffc7f316a6298d7`. It fast-forwarded original J2 to exact M and completed the official adapter rehearsal. Root accepted the result at `2026-11-16T00:27:45.345339+00:00`: exact source/tree/intent/runtime/remote, package proof equal to B and both M hosts, and 120 unrelated untracked records unchanged. Backend configuration preservation was metadata-only; its contents were never read, hashed or copied. Receipt: `evidence/original-rehearsal-acceptance.json`, SHA256 `15da45c9099fe2479a7da810dccc2186ee018920fc5d43e8c37a042142b6815c`. The receipt's inactive state describes this completed rehearsal, before prepare.
3. Official prepare succeeded **once** at `2026-11-16T00:29:28.806882+00:00`, actor `chatgpt-ready1955-20261116-root`, binding exact M/A3/J2/runtime. Private full prepare receipt SHA256: `0aeb8938261cb3e02dfd172bf289cc30215aebf0c254323a70c32970127c9bc5`. Sanitized `evidence/prepare-summary.json` SHA256: `73337055a6ffcc091728884851e25aa512e5faa545c6798f22f8af11d9184806`.
4. The reviewed bound wrapper, SHA256 `ad0dac23730c82ad7901b23c2c6fc39dedf734177e4feea60ad28fe5178844e5`, ran prepublish **once**, returning `ok:true`, `ready_to_publish:true` and exact M/runtime. Root then clicked **Publish changes once** for `mezansalla.com`. The UI displayed new version `231aae8` and explicit deployment completion at the timestamp above.
5. The same wrapper's verify phase was launched **once**, bound to that prepare digest and the observed platform version/completion. It completed successfully, closed only its matching lease, and returned the final result and fresh status recorded below. It was not interrupted or duplicated; no concurrent status or lease replacement was performed.

## Final live acceptance

| Final evidence | Accepted observation |
| --- | --- |
| Official verifier outcome and completion UTC | `verified:true`; official result saved `2026-11-16T00:56:42.399185+00:00`; original wrapper returned `ok:true` |
| Exact verified source/deployment/runtime and consecutive check count | Exact A3/J2/M/runtime above; protocol5; three sequential probes; Frontend artifact tree `8017e720f266ed5d6b0141d83312512e8052396c7da65e22557dce1c8fe79fcb` |
| Fresh guard status after verifier completion | `active:false` at `2026-11-16T00:56:42.439185+00:00`; no incomplete lease directory |
| Sanitized final verification/closure receipt path | `evidence/official-verify-final.json` |
| SHA256 of the exact final receipt bytes | `b3f2258f5bdb1d4b38c4e79308a88f423d79b366f93f55ab8f0cc157a2c21836` |
| Final local HEAD, tree and tracked-clean observation | HEAD=M; tree=`aded5cb29b69b5bb09b7386932ad9fc5c35d1f44`; `tracked_clean:true`; observed `2026-11-16T00:56:52.341376+00:00` |
| Final remote production HEAD and observation UTC | M, via original read-only `git ls-remote`; collection completed `2026-11-16T00:56:52.903535+00:00` |

The verifier ran once from `2026-11-16T00:45:05.526246+00:00` until the official result above. It checked 590 public Frontend requests per probe, for 1770 Frontend requests across three probes plus three health requests. The accepted result includes the exact Backend and Release Control source proofs, JSON/MIME, shell cache and service-worker retirement payload observations. It recorded two boot identities, `2026-11-16T00:41:14.884325+00:00` and `2026-11-16T00:40:33.434593+00:00`; both follow prepare, and both passed the official identity checks. This is release identity and artifact acceptance at observation time, not a business-order eligibility test or an ongoing stability guarantee.

The full original official result remains private at `/tmp/ready1955-publication-20261116/verify/result.json`: 33743 bytes, SHA256 `a537e801c6490612cbe1d4f49d6a9bd3db71ce1bfd1d5e1eb7618dab5cbbaf61`. The fresh status receipt is 22 bytes, SHA256 `595219792dd54a38a8ace80a6a7fc33b928ea74f0dd1bdfac6a2b708dbe0dd1e`. The sanitized final receipt was reconstructed from the original and hash-matched before acceptance. It retains identity, source-summary, cache, timing and final Git facts while preserving the full request list through the original-result digest and its separate canonical digest.

An independent reviewer compared the compact source proofs to the accepted intent, reconstructed the exact 590-request list and full official result, and matched its 33743 bytes and SHA256 exactly. The reviewer also reconstructed and hash-matched the 22-byte inactive status receipt. See `evidence/final-independent-review.json`. This review made no additional live requests.

No duplicate verifier, publication, prepare, prepublish or rehearsal was run. No lease was forcibly cleared. No further publication step is pending. Final collection was read-only with respect to application and order state. A fresh post-publication UI read still showed the received garment blocked by its separate source/approval match guard; no Ready action or receipt/service completion was performed.

`evidence/ready1955-release-proof.jpg` is a faithful crop of the observed deployment management page showing the domain and new published version; SHA256 `1b5e55489cd82f393cb8da9bdfbb4bf4b96c8731f404145a197b2d2e901d6bfe`. The complete internal screenshot, billing UI, customer/order payloads and credentials are excluded.

Issue #1006 is full. PR #1955 and this evidence-only branch are the continuation anchors. The received-garment representation-proof investigation remains separate and unresolved.
