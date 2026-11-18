# Supplier receipt Ready policy — publication handoff

## Current state

**Platform published; official verification attempt 2 is running. Final acceptance is pending.**

Task: `SUPPLIER-RECEIPT-READY-20261118`. Repository: `AMASI-SA/AMASI-SA`.
Canonical release PR: https://github.com/AMASI-SA/AMASI-SA/pull/1984 .

The owner explicitly authorized publishing the supplier Ready fix and then authorized continuation after the proposed takeover of the stalled balances pre-lease handoff. A newer reviewed Snapchat/dashboard release appeared before shared-source mutation; this task preserved it as its direct base and waited for its official publication, three-probe acceptance, inactive guard, and completed-owner handoff.

This is an **evidence-only branch**: `codex/ready-supplier-receipt-latest-handoff-20261118`. Never merge or deploy this branch. The source and intent are frozen on `codex/ready-supplier-receipt-latest-20261118`; neither was rewritten after freezing.

## Exact release identities

| Item | Value |
| --- | --- |
| Reviewed base J | `8d7591f2908bd58aa006470236e693a1d40155fa` |
| Source A | `17262103506180b417f7ad5b88e518faf5ed4d5e` |
| A tree | `73813740fe206b83aea5a355f85e56d24c23b7a0` |
| Intent-only B | `4529555e9c301277370df4cb0e60ceb8f69e45c3` |
| Production merge D | `557c6c52857b0a09feccacd0cdf9bf6ce9d4476e` |
| B / D tree | `8e40056c8d18e6108c76fcc8569fb57e048f8f43` |
| Intent Git blob | `936a0a4534af90b9d48a199ae84f0913e17ab7fe` |
| Runtime release | `rg5-717a2715d8c7db60ea64885f2daab9b3b269c0daa2d284c118537041470c7323` |
| Frontend artifact tree | `d25fc1f9052d3af82cf4cbf3c79c7dd19ccd0be842c9c22bb5d1a1de8f79b8f8` |
| Build metadata SHA-256 | `3a1c50ebfbc5108d2bf9421f3b230f176f90dc732b240d8159f4f67a99c5c046` |
| Reproducibility proof SHA-256 | `addd1a3b705d88e168c42c0cd725ea6df68050fab72cba508776c010977304e8` |
| Backend identity SHA-256 | `d60f2f26910ff4017047046adcd2df5147e9c21351e1fe7a299cd7395a74e29e` |
| Emergent published version | `9f62e46`, replacing `1c7c3f5` |

B has A as its sole parent. D is an ordinary merge with parents J then B, and exactly B's tree. A is an ancestor of D; A→D changes only `release/release-intent-v5.json`. Root and an independent reviewer verified these properties against the remote repository.

Candidate Readiness run: `44208344775`; artifact: `14385291213`. Candidate ZIP SHA-256: `4860489fe80eb9dfd197423d2e4d95be3df8d8c847e299fcc6d3cd88ef03d9cf`. Candidate JSON SHA-256: `d32d52c4535b4008a104632e3c191725d656ac42a7a9b0a8082f82bfa5743224`. The exact A/J candidate was independently validated twice before B was frozen.

## Completed behavior and source scope

A physically received supplier product can become Ready while its current Salla order is `in_progress` without component/source-approval prerequisites. Shared search and transaction predicates use the same policy.

Actual receipt, completed required services, configured employee custody, current canonical product/unit membership, holds/instructions, and exact whole-order coverage remain required. Delivery and printing retain provider identity, actor/claim ownership, source fingerprints and idempotency safeguards. Healthy inventory is consumed once; unavailable pre-write component evidence is recorded as a deferral. A write failure cannot be converted into a successful deferral.

Seven runtime files implement the change:

- `backend/supplier_receipt_ready_policy.py`
- `backend/preparation_piece_operations.py`
- `backend/stock_component_consumption_service.py`
- `backend/assembly_completion_delivery.py`
- `backend/assembly_status_policy.py`
- `backend/fulfillment_carrier_label.py`
- `backend/order_engine/amasi_shipping_compat.py`

The source also contains eleven regression-test modules, the Review CI workflow update, and the frozen task STATUS record. Those nineteen runtime/test/workflow blobs match the prior reviewed supplier source exactly; the task record is the twentieth source file. J's twelve newer dashboard paths have zero overlap and were preserved. B changes only the release intent. No new Android APK is required.

## Accepted source and CI verification

Local bounded policy acceptance: **205 tests +24 subtests**, zero skips, exit 0.

Exact B after ready-for-review checks: **24 workflows —23 successful and one conditional TikTok skip; 53 checks —43 successful and ten conditional skips; no pending, failed or cancelled current checks.** Conditional job skips are separate from acceptance-test skips.

| Suite | Result |
| --- | --- |
| Review main | 1029 tests +596 subtests |
| Preparation refresh recovery | 35 tests +35 subtests |
| Android operations | 348 tests +59 subtests |
| Source snapshot recovery | 124 tests +28 subtests |
| Review acceptance guard | 1 test |
| Operational Stock Eligibility | 4844 tests +7 subtests |
| Operational Balance backend, attempt 2 | 5163 tests |
| Balance shipping SSOT/calculator | 16 tests +18 subtests |

All listed XML acceptance gates verified zero skipped, failed, or errored cases.

Review run `44209391496`, artifact `14385991629`. Stock run `44209391506`, artifact `14385926579`. Balance run `44209391718`, successful attempt-2 backend job `134490625969`, artifact `14387135335`.

Balance attempt 1 had 5162 passed and one failed worker-entry precondition in the inherited deadline test. The test sets a 0.15-second whole-request deadline before the collector reaches its worker. Its relevant test, runtime, collector, index, fixture and workflow blobs were unchanged from J; A's same suite had passed. Root retried only the failed backend job once, with no source, test timing or gate change. Attempt 1 and artifact `14385841856` remain preserved.

Readiness run `44209391677` passed both clean-clone Host Node 20 and 24 rehearsals, governed Node 22.23.2 / Yarn 1.22.22, complete artifact/proof equality, isolated Backend/Frontend package verification, and exact metadata route checks. Backend package: 1241 files; frontend package: 1536 files. Both isolated verification flags and backend health verification passed.

## Actual host and one publication

All times below are UTC unless explicitly labeled Riyadh.

1. PR1969 finished its official three-probe verification at 00:59:34.914052, exited 0, then reported inactive guard and completed ownership handoff.
2. Root's fresh preflight at 01:05:00.737812 observed inactive guard, exact J, correct production branch, clean tracked source and all 120 unrelated untracked paths. Inventory SHA-256: `2a0200fc4ebb665ebc1fec275ef8db46870809282ef7fcc40a931e631f74eb04`.
3. Shared `/app` safely fast-forwarded to D at 01:07:05.051929 after another inactive-guard check. Source remained clean and the inventory was preserved.
4. The actual configured `cd /app/frontend && yarn build` adapter passed on its first attempt, 01:08:46.757464–01:10:00.498211, exit 0. GitHub markers were absent; reviewed origin and bytecode prevention were inherited. Official frontend and isolated package verification passed. Generated outputs remained ignored/untracked, and their exact hashes matched reviewed CI.
5. Prepare passed at 01:12:15.464196, actor `SUPPLIER-RECEIPT-READY-20261118`, protocol 5, exact A/D/runtime. Prepublish exited 0 and returned `ready_to_publish:true` with the same identities.
6. Root used Manage→Republish→free Publish changes **exactly once**. UI recorded Deployment Started Nov18 04:14 AM Riyadh, then packaging, building, cloud installation, an explicit **Deployment successful** notification, restored Republish, and new live version `9f62e46`.
7. No paid chat, purchase, auto-top-up, customer/order mutation, or financial-data write was performed.

Publication proof: [publication-20261118.jpg](publication-20261118.jpg), SHA-256 `988270abdae46da04b0a85753a7dce0cc8b1acae3a8cadccb3f3971dad1e87a4`.

## Official verification attempts

### Attempt 1 — preserved refusal

The official verifier ran 01:24:15.366493–01:24:17.572014 and exited 1. Backend identity checks passed, but the first canonical `/build-meta.json` request with `Accept: application/json` returned previous J's exact metadata hash:

`05130a632647192a628ef09189f62d22248c6d1afdde31b454ec5a61c5f4da9c`

The JSON MIME and object checks had passed; the byte hash correctly failed. The verifier did not reach later metadata variants or three rounds, and did not remove the lease. First-attempt stdout/stderr/exit/result files remain intact.

### Convergence diagnosis

At 01:29:05.240547, the guard's unchanged read-only transport returned HTTP 200, valid JSON MIME, 493187 bytes, source A, and the expected new metadata hash for all four canonical/cache-busted × application/json/*/* variants. Responses declared no-cache/no-store/must-revalidate/max-age=0. Current health reported exact A/runtime with all required identity/source flags true. Observed boot: `2026-11-18T01:24:17.389680+00:00`.

Independent review confirmed the first hash was exactly J's metadata. J and D share identical frontend asset bytes; metadata release-source identity differs. Current delivery converged, but the precise reason for the old first response—cache propagation or frontend rollout routing—was not established. No expected hash or guard was relaxed.

### Attempt 2 — currently running

Root launched the unchanged official verifier once more, wrapper PID `186843`, after the fresh four-variant convergence evidence. Evidence directory:

`/tmp/supplier-ready-1984-release/verify-attempt2`

The wrapper retains `verify.json`, `verify.stderr`, `verify.exit`, timestamps, and `verification-result.json`. Only after the official command exits will it run guard status and require `active:false`, exact clean D and preserved inventory. The first attempt remains in the parent evidence directory.

## Exact next safe action

Read only the attempt-2 completion evidence. While its wrapper runs, **do not run a competing guard/status/verifier, alter shared source/build, clear a lease, or click Publish again**.

Completion requires official exit 0, `verified:true`, `checks:3`, exact A/D/runtime and frontend identity, followed by inactive guard, unchanged clean D and the preserved untracked inventory. Then update this evidence document and PR1984 with final acceptance, mark the useful live administration page, complete the UI task, and report publication to the owner.

If the wrapper exits without accepted completion, inspect its retained evidence before deciding any next action. Do not assume publication success from the platform notification alone.

Issue1006 is saturated; PR1984 and this remotely recoverable evidence branch are the active continuation. Historical checkpoints remain in Git history and superseded PR1982. Production was changed by this task: **yes, version9f62e46 was published; official final acceptance remains pending.**
