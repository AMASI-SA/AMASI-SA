# Supplier categories and invoice numbering release, revision 2

User authorizes server release only, without enabling financial or material writers. APK work remains with the other conversation.

## Preserved inputs

- Original reviewed source candidate: `f7cf104f99593dea1f229b17eb9ea0a908e867c7`, PR1517. All applicable CI checks passed, including supplier, operational, stock, build and security checks. This candidate was not merged or published.
- Latest Production base J: `219c4db4a45cbfc2bc5499909c0fcad5e3ddb318`, PR1524. Subsequent Production changes to ads remain intact.
- Current branch: `codex/supplier-invoice-release-r2-20261026`.
- Original supplier source and both invoice-numbering source commits from PR1508 were cherry-picked with attribution onto J: `86a905459`, `95ba9215c`, `e8cdde898`.
- Changed supplier/invoice application files are byte-identical to the previously verified candidate. Prior local tests: supplier71, invoice61, frontend12 PASS. Fresh current-HEAD CI is required before merging.

## Live access and lease

The user supplied `/app` guard output `active:false` on 2026-10-26; the same result was read directly from the code-server terminal through Windows UI Automation. Native browser tools remain unavailable, but desktop accessibility can access the visible browser and terminal. A dedicated Terminal2 was created; never interrupt Terminal1 or another conversation's terminal. Switching chats/tabs or concurrent user typing can invalidate focus: re-observe and verify the terminal before typing. An attempted direct input collided with user typing; only read-only status commands were involved, and the final explicit status succeeded.

No source changes in shared `/app`, no lease acquired/cleared, no publish attempted. No production business writes. No frozen intent B yet. Issue1006 rejected updates due to GitHub's 2500-comment cap; this document and the release PR preserve continuation.

Next: push/review this source candidate, pass current CI and retain its candidate intent. Recheck live lease/publishing state before adapter rehearsal or remote changes; freeze intent-only B without rewriting A, pass reviewed checks, merge preserving A/B, prepare/prepublish and publish. Completion needs visible newer Deployment Succeeded and official three-probe identity verification followed by inactive lease. Preserve Preview's separate runtime/DB/session and all unrelated files. Do not activate financial/material/advertising writers or publish APK.
