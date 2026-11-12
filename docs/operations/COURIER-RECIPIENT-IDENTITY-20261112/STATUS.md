# Store-courier saved recipient identity follow-up

## Scope and acceptance

Repository `AMASI-SA/AMASI-SA`; branch
`codex/courier-recipient-identity-20261112`; starting production merge
`0af4a3da57072a4995a2ebd0ca76720d17a80ee6` (PR1890).
The user's existing authorization covers fixing, publishing, and actually
trying the previous affected completed orders in Mezan. No new shipment,
handoff confirmation, customer-data repair, advertising/account change or
financial write is part of this follow-up.

Acceptance requires the actual store-courier preview to open for both reported
orders after a separately verified protocol-v5 publication. Synthetic route
success is necessary evidence, not a claim that live printing works. Physical
paper printing cannot be inferred from a browser preview.

## Prior publication and live evidence

PR1890 published as `d73f584`; the observed green wording was
`Deployment finished Nov 12, 04:02 AM` (Asia/Riyadh). Official verification
passed three probes for source `da0d2e2fb82a7848bcd398ca292ba93e1a880681`,
deployment merge above and runtime
`rg5-8c4fb8eb704af7c47020a751629bd3af71672fbf26ff02bdbde8b9d969f9ffcc`.
Following guard status was `active:false`.

Google November 11 live acceptance passed in both the yellow card and executive
SPEND cell: the known amount is explicitly incomplete, and financial totals
remain unknown. Both courier attempts still failed with the generic history
recovery message. No courier preview success is established yet.
Full prior-release evidence is recoverable on documentation-only branch
`codex/google-courier-live-evidence-20261112` at
`10975ab0a6b53d6cd4e0558d4f2ba2221aa4aa5e`. Never deploy that branch or edit
the immutable PR1890 source/intent pair.

## Confirmed saved-input defect

Fresh native order, child-list and history payloads passed the current pure
page, child, history and destination checks. That replay used the fresh order
as the saved order and therefore did not test the real saved recipient guard.
Existing owner-only Order Engine DTO reads then confirmed, for both affected
orders, nonempty matching buyer/recipient names and the same Saudi mobile
identity represented as nine subscriber digits versus twelve digits with
country prefix `966`. Gift flags are false. Only these shape/equality facts
are retained; no real identifiers, contacts or provider payloads enter Git.

The DTO mapper selects the same saved buyer identity fields used by
`recipient_is_independent`; recipient enrichment calls the same recipient
resolver as the print path. The literal phone comparison marks those equivalent
forms independent, and `_destination` rejects the saved recipient before
issuing its ID-free order QR. The correction is bounded to the saved
recipient check in this history recovery: equal names plus strictly valid
equivalent Saudi phone formats. Other recipients, gifts, malformed values,
current carrier evidence and every final race/source fence remain guarded.

Fresh native buyer phones were also observed as integers, so accepted phone
inputs include positive exact integers and strings. Booleans, floating-point
numbers, objects, letters, punctuation, control/format characters and
non-ASCII digits cannot supply the new equivalence proof. Raw selected fields
are checked before the existing resolver's stringification and trimming.
The shared recipient policy and the fresh-provider guard are unchanged.

## Recipient and integrated verification

The synthetic actual-route regression failed eight equivalent-string cases
with `recipient_address_unavailable` before the fix, while 22 negative cases
passed. Six native-integer cases then failed against the string-only draft,
before support for positive exact integers was added. The final recipient
file contains 37 cases, including the native integer combinations and an
outer-control case that the ordinary resolver would trim.

The writer's complete history/recipient/race/pagination run passed **173**, zero
skipped, exit 0, 11.37 seconds. Root then ran the integrated seven suites below:
**231 passed**, zero skipped, exit 0, 14.88 seconds. They include the existing
generic, explicit-completion, history and race paths. The two new test files
are wired into the existing Shipping Print Boundary path triggers, scope
classification and mandatory courier test step; no gate is removed or weakened.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests python -B -m pytest --noconftest -p no:cacheprovider -q --tb=short --asyncio-mode=auto backend/tests/test_courier_history_recipient_identity.py backend/tests/test_courier_history_pagination_contract.py backend/tests/test_courier_history_identity_recovery.py backend/tests/test_courier_history_recovery_races.py backend/tests/test_salla_generic_shipping_mode.py backend/tests/test_courier_generic_identity_recovery.py backend/tests/test_courier_explicit_completion_recovery.py
```

Frozen helper SHA256:
`d80e8f27bd9c0e19a25c6feee7f32a2ae5ee7e182e8e9fec3c4befcb07aed3db`.
Recipient test SHA256:
`3815d9c0909787dc2762a28c6a8f8b56473a068096a219d90089caaa46fc2e22`.
Pagination test SHA256:
`eabc19d70d71721205337df6e12a9265c13a666156c3254b6e8593ca0a4d38b5`.
Root reviewed the final runtime diff and source immutability assertions.

## Query contract correction

The recovery requested `per_page=100` for lists and histories. Salla's
[published pagination contract](https://docs.salla.dev/pagination) caps it at
60; existing compatibility reads use 50. The two requests now use 50 without
changing complete-page/total/next-page/unique-child checks. A response that
spans pages still cannot authorize this bounded recovery.

This is a documented request-contract mismatch, not proof that the provider
rejected 100 in the actual failed attempts. The synthetic boundary deliberately
rejects unsupported sizes to verify the caller contract. Four web/mobile and
scoped/global route cases failed with 409 before the correction and passed
afterward, exit 0, 2.09 seconds. An earlier invocation omitted the repository's
asyncio mode and failed fixture setup; it was an invocation error, not RED
product evidence. Independent identity-plus-pagination suites passed
118 tests, zero skipped, exit 0, 6.70 seconds, with unchanged input hashes.

## Current state and next safe action

Implementation and integrated tests are complete; final independent recipient
and workflow review is in progress. No new source A/intent B, merge or
publication is claimed. Production changed by this follow-up: **no**.

Finish the independent review and checkpoint exact source. Re-read Issue1006, production ref
and official shared guard before a fresh governed A/B build and serial release.
Preserve financial PR1889, Android work and unrelated platform files. Following
the authorized publish, require three exact runtime probes/inactive lease and
repeat the actual previews. Do not treat the generic current error as a proven
transport error: the browser blocks DevTools, Swagger is routed to the SPA,
and the application currently discards the machine code in its frontend error.
