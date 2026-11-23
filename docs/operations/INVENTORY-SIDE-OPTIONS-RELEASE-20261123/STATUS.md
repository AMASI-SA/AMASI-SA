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
