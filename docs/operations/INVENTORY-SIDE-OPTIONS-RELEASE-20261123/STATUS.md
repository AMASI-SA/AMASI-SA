# Inventory side-option classification — authorized release integration

## Authorization and state

The owner explicitly requested **ادمج وانشر واختبر** on 2026-11-23. Merge, publication and read-only live acceptance are authorized for the inventory option classification change described in PR2084. Source integration is in progress; no publication success is claimed by this checkpoint.

- Repository: `AMASI-SA/AMASI-SA`
- Release source branch: `codex/inventory-side-options-release-20261123`
- Latest reviewed Production Git base J: `76efd2c3aa1563939ee3e27a9ae03f27fc1a3d43`
- Prior feature base: `c2d244a3349013c06f3fb7824070bfa12bd0bfe0`
- Complete reviewed feature source: `e412bc630de7d7e8eaf7dd03f6c1d09e59c18367`
- Source provenance PR: https://github.com/AMASI-SA/AMASI-SA/pull/2084
- Prior final evidence: `23f2029d28839b56f00b17ccfd467a03d0f034cb`, branch `codex/inventory-side-options-evidence-20261123`
- Exact release source/intent/merge identities will be recorded in the canonical Issue1006 handoff and post-freeze evidence, without amending a frozen source.

## Integration and invariants

J contains PR2086's accepted-statement and weekly payment-fee changes. Its 25 changed paths have no overlap with the complete 42-path PR2084 source interval. An independent semantic review found no incompatibility at their existing operational-finance boundary. The complete source patch was applied to an isolated J worktree; no old branch ancestry or intent commit was imported.

Before this release-status and dedicated acceptance-test addition, the combined tree is exactly `fba9d030ce20f867854de7c596fad7ee02a44a17`, matching an independently calculated Git-tree oracle. The full source patch SHA-256 is `768c46d7ab83d7bd43c06eca9f7b1130ac53fc68aacafd1f938a5b4b8d50e404`. All upstream paths remain byte-exact. J's inherited intent blob must remain `819743a5a6d29a9c6b50502bc3aebd8f8f850220` throughout source preparation.

The feature classifies each stable product option ID as physical specification, actual customization, or a customer side addition. Gift questions, text and notes do not block stock readiness or create deferral. Customer answers, costs, conditional relationships, required physical dimensions, draft amounts, completed-command replay, original invoice/receipt identities and existing supplier-piece history remain protected. No valuation gate, financial writer, customer-order stock activation or Salla definition is changed.

## Verification plan

The original feature passed 6,810 operational backend tests with90 subtests, 1,699 frontend tests, real Mongo policy/receipt/payment cases, stock/G47/accounting/security/CodeQL and independent source reviews. Those runs are provenance, not proof of this new integrated SHA.

At integrated source checkpoint `d195ef4395be11fd9ee630479bb2cdfa94130e59`, focused backend preservation tests passed **415 tests**, exit 0, including option policy, purchase configuration, supplier plans and upstream dashboard/statement/weekly fees. JUnit SHA256: `843283e41821662b24fa2a1edc4e324882c9408c2d59dd53c179b8bf3a80a0f9`. The unchanged product source also passed **281 frontend tests in 14 suites**, exit 0, covering inventory fields/classification/drafts/purchases and six upstream dashboard/demand/fee suites. JSON report SHA256: `c05ad5c8f3268be0f85c22d2d652ea7a1a8a504055ddecd06d39098150b3129d`. Groups overlap prior acceptance and are not additive unique totals. Existing deprecation/proxy warnings remain visible.

The dedicated synthetic browser addition preserves the seven purchase/receiving journeys and adds an eighth journey with three screenshots for the actual option-role editor on desktop/mobile. It verifies stable-ID gift question/text/note classification, locked and required physical size, dependency warnings, unchanged quantity/unit price/tax, draft reload and exactly one policy-only PUT with no payment or receiving call. Workflow acceptance requires all eight scenarios and ten screenshots. Syntax and synthetic build passed; this build is not a governed release artifact. Executable Chromium acceptance is still pending on this new combined source; the local environment's existing sandbox/socket restriction was not bypassed or mistaken for a browser pass.

Independent read-only review accepted the four acceptance-harness edits: selectors and API/revision fixtures match the real components; original seven scenarios and financial session checks are preserved; no concrete blocker found. This review does not replace executed browser evidence.

This release still requires its own exact-source CI operational and release gates, including real isolated Mongo persistence/receiving coverage and GitHub's sandboxed browser fixture. Prior individual source CI is provenance only.

After the current foreign handoff completes and the original shared guard is freshly inactive, follow AGENTS.md: governed two identical clean builds, external candidate and original validators, new intent-only B, actual Node20 and Node24 clean-clone adapter/package/runtime proof, normal merge with exact B tree, safe host staging/rehearsal, prepare/prepublish, one Re-publish changes, newer explicit success, original three-probe verification and final inactive guard. Do not squash or rebase frozen A/B.

## Current shared-host boundary

Actual Emergent terminal inspection found `/app` at clean J and the original guard **active:true** for foreign actor `codex-tabby-weekly-fees-2086-20261123`, source `1725672eeccb1272f183c376b9267e363a028325`, deployment J, protocol5, prepared `2026-11-23T15:41:37.354423+00:00`. The foreign PR2086 body also describes its serial publication handoff. Preserve its lease, outputs and ownership. This historical read does not replace fresh checks before our release actions.

Later original status reads reached the 30-second and 60-second timeouts without returning a new state. No inactive state is inferred from those timeouts, and no foreign process/lease was stopped or cleared. Read-only host `lslocks` subsequently showed Python PID102870 holding the operation FLOCK and PID103995 waiting; the lease file still identifies PR2086. This observation explains the blocked status read, but does not authorize killing a process, releasing a lock or treating the foreign release as complete.

Our task has made no `/app` source/output changes, release lease, Production merge, publication, policy save or live financial/stock write. Isolated source work may proceed; our release rehearsal, merge and publication wait for the current handoff's verified completion and fresh inactive guard. No competing publication reservation is created by this status.

## Next safe action

Finish and checkpoint the isolated source/acceptance changes, run proportionate source tests and record the exact SHA in Issue1006. Re-check PR2086's completion, current Production ref and the actual original guard before entering the release boundary. If Production advances, preserve it and prepare a fresh combined source/intent rather than publishing an obsolete candidate. Live acceptance opens inventory entry, classification, purchase entry and awaiting-receipt pages without saving real business records.

## Evidence branch checkpoint

This directory is evidence only on `codex/inventory-side-options-evidence-20261123`; never deploy this branch. Current recoverable source is `45c8fb4ea91e5af8523971fbfa2270ee96552224`. Ordinary source CI run45213472517 is in progress on that exact checkpoint. Final source release/Node20/24/publication verification remain pending.

## Current boundary — previous blocker superseded

Read-only verification of PR2086's pinned retained wrapper/stdout confirms original `verified:true`, `checks:3`, exact prior source/deployment/runtime/artifact and final inactive record, with stderr 0. Captured stdout SHA256 `f4b57349a8da68c70c497ec80992c01cfab42cad4231d3b70bd1ea45297339a6`. The original actual shared guard was separately freshly read at `2026-11-23T16:18:06.833684+00:00` and returned exactly inactive; shared HEAD and remote Production are clean J. No verifier or publication was replayed. See `evidence/FOREIGN-HANDOFF-READONLY-20261123.json`. Earlier blocked status observations remain historical.

Own exact-source ordinary CI run45213472517 has passed frontend106 suites/1699 tests, canonical worker4 tests, and browser job137828193226. Inventory8/8 scenarios and10screenshots pass; all61 source hashes match45c. Independent reviewer inspected all10 images and root inspected the3 new role screens; no blocking visual issue. ZIP artifact14836858244 SHA256 `e894f96efd86b7c9538d600ae62c5866b4badddc00262c48d8bf7d54acf89c5f`. Raw browser evidence, source manifest and independent/root reviews are under `evidence/source-45c-browser/`. Backend job remains running at this checkpoint.

Next: retain45c as source candidate A, execute the original two-clean-build/candidate path in the isolated release checkout, review the external intent and create intent-only B. Exact-B PR gates and Node20/24 rehearsals, normal merge and actual host publication still required. A/B must not be rewritten after freeze. Current serial publication coordination now belongs to this owner-authorized inventory release preparation; no actual own lease yet, and no shared source/output changes.

## Frozen A/B and final release PR

[PR2089](https://github.com/AMASI-SA/AMASI-SA/pull/2089) now contains immutable A `45c8fb4ea91e5af8523971fbfa2270ee96552224` and intent-only B `499b98735b5cc4eae79d380e4f8d5ec5477175e5`; B tree `4816e3468b3fcbcde78ab799760201fcfb2e7171`, sole parent A. J..A has no intent history; A..B changes only the intent. Original governed two-clean-builds, frontend verification, external freeze, independent candidate/manifest review and original B validators all passed. Full records and logs are under `evidence/frozen-A-B/`.

Runtime `rg5-a762a47336e81b005a6fdfe509695c01a46b991478e27d6dc421913e94c94762`; frontend artifact `5895551a52ba294d373076988d0d1ba75e39855ab28cbd7a7a76c0a4038834e4`. Exact candidate bytes are committed as B intent blob `5111041b1b1bd7c7f919f13ddcf9dc1f25f71df7`, SHA256 `ae25d2846eadb9e512508d5658d33e49e7c4a43222d8393b605ca0e52842487b`. No squash/rebase/source amendment after this freeze.

Exact-A ordinary run45213472517 is fully successful: backend6810+90subtests, demand106, shipping16+18subtests, frontend1699/106suites, canonicalworker4 and inventorybrowser8/8 with10captures. These groups overlap prior runs and are not summed into a unique test count.

PR2089 was marked ready for review to enable remaining required transition gates; this is not merge or deployment approval evidence. Exact-B readiness45216427337 and operational45216427435 plus all applicable PR checks are running. Node20/24 clean-clone artifacts must be independently accepted. Next: finish those gates, record artifacts and final checks, then ordinary merge with ordered parents[J,B] and exactBtree. Production has not changed by this task; no own lease or host source/output changes.

## Exact-B readiness accepted

Release readiness run [45216427337](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45216427337) succeeded on exact B. Actual clean-clone adapters on Host Node20.20.2 and24.19.0 passed. Independent review accepted four GitHub ZIP digests, both archive checksums, every generated member and all original intent/source/artifact/proof/runtime validators. All389 generated file paths, bytes, lengths and modes match. Full frontend1801-file and backend1333-file package proofs were independently recomputed and match exactly. Root reviewed the report/script and recomputed four ZIP hashes, actual complete generated file hashes/modes, exact B intent bytes and proof hashes. No blocker.

Durable evidence, original package/environment records, generated manifests and compressed exact adapter/runtime logs are under `evidence/readiness-B-45216427337/`. The archive container hashes differ while their complete extracted payloads are identical; each archive matches its own recorded hash.

Accounting and CodeQL gates passed; remaining exact-B operational/stock/resumable gates are monitored independently. PR2089 is unmerged; no D, own lease, host source/output change or publication. Preserve the serial handoff and fresh-check production/guard before a normal merge. Helper bundle preparation is disabled until actual D.
