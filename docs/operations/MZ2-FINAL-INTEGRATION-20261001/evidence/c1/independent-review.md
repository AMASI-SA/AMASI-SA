# Independent C1 rich shipping backend review

Scope: newly authorized rich_shipping_approval only, on C:/Users/amasi/mz2-c-rich-shipping-20261001.
Baseline:2cce72f04a9f41e4e732ac7495c24ab3ca577f31.
Checkout HEAD at review:378e320009b8c3366d3a4379e5504a1c911c6e23 plus current uncommitted C1 implementation.
Frontend remains outside this review while another agent edits it. No source edits, commit, push, production call or service start by this reviewer.

## Finding discovered and resolved

Medium / high confidence: Stage7 readiness originally continued returning ready=true after a selected rich contract's evidence was revoked, even while the actual fee correctly failed409. Original cause was accounting_shipping_native_routes.readiness selecting the approved rate without current evidence validation.

Independent real-Mongo HTTP reproduction used unique db mz2_track_f_626862e425b8427fbf24026c802af2c7 and actual source-review, contract-approval, revoke, context and accrue-fee routes. Before and after revocation, Stage7 returned ready=true/reasons=[]; accrue-fee returned409 shipping_evidence_changed_since_approval. Process exit0; fixture cleanup removed the unique database; strict NoLegacy listener passed.

Root added shared require_current_rich_contract (backend/accounting_shipping_native_rich_contracts.py:63) to both readiness (:47) and the fee consumer (:219). It revalidates the existing contract model, owner/courier and wrapper/version identity/date/reviewer agreement, required evidence purposes and current source bytes via the explicit Native authority.

Fresh independent reproduction on corrected source, unique db mz2_track_f_457deab6d4c4417d8f975b44904ab4cb:
- Before revocation Stage7 ready=true.
- After revocation ready=false with shipping_evidence_not_approved.
- Actual fee remains409.
- Exit0, fixture cleaned, NoLegacy listener passed.

This finding is closed in the reviewed source. It needs the committed source's ordinary regression/CI attribution for final delivery.

## Independent transaction race evidence

Deterministic real-Mongo probe used unique db mz2_track_f_8d457079bd3841d8bbb529517d73ab2e. The existing _post function was wrapped only to pause scheduling, then the unchanged original function executed; no success value, journal implementation or evidence authority was mocked.

Sequence:
1. Actual Native approve and delivered recognition.
2. Start new fee; pause immediately before its actual journal while its Mongo transaction already owns the setup-document pin.
3. Start actual evidence-revocation route with exact CAS version; verify it cannot finish while fee transaction is held.
4. Release original journal execution; fee200 then revocation200.
5. Recognize another order and attempt fee using now-revoked terms:409.
6. Exactly one fee event exists; strict NoLegacy monitor passes; unique DB removed.

This proves the observed serial ordering: a financial transaction already holding setup finishes before revocation, and later postings fail closed. It does not claim every possible scheduling interleaving was tested. Exit0.

## Source/contract findings

No remaining actionable backend finding in this bounded review.

- Existing writer preserved: accrue_fee -> _post -> post_journal_v2 inside atomic_owner. The change selects existing rich calculator economics; no new ledger engine or alternate writer.
- Existing immutable ShippingContractInput/Version and require_shipping_contract_charges are reused; courier_cod_fee_rules unchanged. Prepaid/postpaid describes payment-to-carrier metadata and does not introduce a wallet or silently net COD.
- The rich branch emits existing separate shipping expense/VAT and courier_cod_commission/VAT identities, then the unchanged courier payable. accounting_mz2_reports.py:360 already recognizes this commission expense identity.
- Fee key, delivery effective time, posted replay, COD recognition, settlements and flat calculation remain the existing behavior. Earlier posted replay happens before current-evidence checks, preserving recorded history after revocation.
- save_rich_setup authorizes fresh exact owner/action permission before replay; explicit review permission applies to owners as well. Draft/approval/review/revoke metadata, request fingerprint and audit commit through one owner setup CAS.
- Rich requests are owner-local and action-namespaced; changed payload hash rejects. Concurrent exact retries may encounter409 CAS conflict, then identical replay returns the original result.
- NativeEvidenceAuthority resolves only persisted owner/courier/purpose-approved records; public requests cannot supply approved_by/state/source hash. Actual retained source bytes, SHA and size are checked.
- Native fee pins the same setup document used by approval/revocation and the actual original bytes in its transaction. The final pin uses the actual journal group ID; zero-cost events retain the contract link without inventing a journal.
- A failure during pin or downstream event insert rolls back the same actual journal transaction; no source file or review grants write-control authority.
- Default _approved_service remains503; optional authority is a server-side Native call argument, not an environment/payload override. The dormant rich Legacy service remains guarded423/unregistered.
- Source comparison against baseline shows unchanged accounting_shipping_contract_gate, accounting_shipping_contracts, courier_cod_fee_rules, accounting_atomic, accounting_write_control, accounting_writer_transition, accounting_ledger_v2, operational_atomic and production_release_guard.
- The new retained-original download resolves the exact owner, requires explicit review permission, validates bytes, and returns attachment/no-store/nosniff; no public source URL is introduced.
- No Native callback calls the dormant assert_file_unlinked/general_ledger helper or old rich post_txn_group service. The three independent probes' command listener saw zero retired financial collection accesses.

## Test review and limits

Inspected new tests cover exact monetary legs independent of the calculator implementation, tier endpoints/uncovered gap, separate tax inclusion, prepaid metadata/non-COD, paused423/no controls changed, explicit reviewer permission, foreign original/courier/purpose, forged request fields, changed draft/replay, revocation/tamper/delete, concurrent approval/posting and rollback after actual journal. These are meaningful assertions; this reviewer did not count the agent's full suite execution as an independent run.

Only the three documented probes were executed by this reviewer. They reused the existing unique disposable fixture at mongodb://127.0.0.1:27130/?replicaSet=mz2c with PYTHON_DOTENV_DISABLED=1 and backend/test imports. No service was started or production environment used. Full suites, frontend behavior, committed source CI and final C acceptance remain root-owned work.

## Reviewed working-file identity

SHA256, exact working bytes at end of review:
- backend/accounting_shipping_native.py:34ffb062aff8706cd03e31bd5b142c545b17316132b6e681a7c8c74501ef4b37
- backend/accounting_shipping_native_routes.py:2ab2f6a44be1063886cce665025ca02bf54487ada6a7f3245fe0ee2cb71eb94d
- backend/accounting_shipping_native_rich_contracts.py:bde3ab1e9b9171cb4552f66ab4234445777aafbe2aa20e9b92a61f818f78809a
- backend/accounting_shipping_native_contract_evidence.py:375e7d4bc0901eb317b94518b75d3483ff54132eaa78b5444ed6ed2cf3032b2f
- backend/accounting_shipping_evidence.py:c949db899268a1dad522ec251bd7a8ef4e1fe8e94a21ac2b7f35b671640bef72
- backend/accounting_shipping_native_setup.py:9bb26a5387a69525ca95c95a90fa19f9f24c8ed8b061180d0040b3fbf6fb6039

Later edits need their own attribution. This is bounded backend verification, not a release-ready declaration. Production financial writes=0; Merge/Deploy/Opening Post/Activation=NO; write-control unchanged.
