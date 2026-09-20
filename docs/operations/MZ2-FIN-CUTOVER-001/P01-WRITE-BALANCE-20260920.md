# P01 transactional write-balance isolation — verification in progress

Frozen #1109 and #1110 source/intent pairs and evidence remain unchanged.
Accepted #1110 source: `163874f5ffcab10a5f3dc883a1c2acd56c414bf9`.

## Change and invariants

`accounting_mz2_balances.read_mz2_write_balances` consumes the accepted MZ2 report eligibility reader inside the same active owner SessionDatabase. Raw databases, wrong owners and ended sessions reject. Settlement source receivable, refund execution/payable, and advance cancellation/payable/execution use this balance. Explicit required execution accounts require approved opening/zero evidence even without prior activity. No global legacy balance policy changed.

The user explicitly approved a different execution provider when supported by its documented execution. The existing accountant workflow now requires an uploaded document, execution reference and canonical provider refund ID for that case. Approval associates the original case with the executor without equating provider payment IDs. Existing contradictory provider evidence rejects; the original provider's confirmed execution rejects; mixed approved channels and duplicate refund/advance/recognition identities remain blocked. Proof bytes remain private. No fee/tax reversal was added.

## Fresh evidence

- 13 real Mongo sentinel/session/identity tests PASS at `a3048a8d28b9414162d2685c7cc66a7465575cb0`, 6.34s, exit 0.
- A previous-source reproduction on frozen #1110 A returned HTTP 200 instead of required 409 with only 10 eligible bank units, 40 requested and a large untagged legacy debit. The same test passes on the correction. Baseline journal `749fbcf9-aa0c-41ee-8b79-fd44f08c6ddd` was created only in a disposable synthetic test DB.
- Affected Mongo run at `dcdc630276cd48aa62cde2091ca6953d464cac7c`: 87 PASS / 2 fixture failures. The historical fixture moved its opening three hours before its approved cutover; fixed to the same instant represented at +03:00. The original-rate refund fixture lacked approved opening evidence; added explicit synthetic evidence. Both failed tests rerun PASS at `20412b039` (exit 0). No production guard was weakened.
- Settlement unit suite: 9 PASS. Economic-date unit suite: 3 PASS.
- Refund/advance frontend suites: 6 PASS across 2 suites, 2.873s, exit 0.
- New sentinel suite added to the existing governed real-Mongo workflow.

First attempted integration run was invalidated when the previous Preview Mongo process on 27018 stopped during another task's deployment. It is not acceptance evidence. Current test-owned replica `p01write` on loopback27028 and standalone27029 use private paths outside `/app`; all tests use new `mz2_atomic_test_<uuid>` databases. No existing acceptance database was written or dropped. Source is remotely checkpointed; temporary logs are supplementary, not the continuation source.

## Sentinel coverage

| Requirement | Actual assertion |
|---|---|
| A settlement inflation | Large untagged provider debit cannot authorize settlement; qualified sale allows one balanced 2-leg settlement. |
| B bank inflation | Untagged bank debit and accounts.current_balance cannot fund refund; qualified settlement permits 40 and leaves payable75. |
| C negative legacy | Large legacy credits cannot block qualified execution; same-case unqualified liability does not alter reconciliation. |
| D provider execution | Legacy provider funds reject; eligible provider event succeeds. Different-provider document case also proves target-only funding. |
| E case liability | Refund payable and advance/cancellation/payable ignore unqualified rows with the same case account. |
| F pre-cutover | Correct operation tag with an earlier accounting instant cannot fund a payment. |
| G unsupported/malformed | Unsupported source or invalid economic date fails closed with no persisted delta. |
| H owner | Same target IDs tagged for another owner cannot fund this owner's payment. |

Rejections compare complete nonempty collection snapshots, including journals, groups, drafts, audit and counters. Fixture initializes the permanent owner coordination row before snapshots; transaction revision changes must still roll back. Success checks assert balanced operation legs and duplicate approval creates no new journal. Additional tests prove own uncommitted ledger visibility while another connection sees zero, followed by full abort; root/wrong-owner/ended-session refusal; canonical-ID whitespace duplicates and preexisting noncanonical drafts.

## Remaining before candidate handoff

Fetch a fresh Production J after these fixes, integrate accepted source + write correction into a new worktree, explicitly resolve overlaps, run affected checks on that integration, regenerate the complete symbol census, freeze new A and its direct intent-only B, nine governed CI on each, clean-clone rehearsal, and final isolated Preview write/report smoke. These are not yet claimed complete.

P01 IN_PROGRESS; P02 LOCKED. Production readiness, backup/restore verification and owner decisions on tax/effective date/cutover/timezone/permissions/activation remain independent. No merge, publish, Production write, or Production configuration change was performed by this task.
