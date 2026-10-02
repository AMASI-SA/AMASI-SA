"""Native rich shipping contracts through real HTTP and isolated Mongo.

Reuses Track F's replica fixture and its zero-Legacy command monitor. Evidence
is retained through the real source-file service and approved through the real
review route; no evidence authority or financial writer is replaced for success.
"""
import asyncio
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from accounting_ledger_v2 import read_reporting_entries_v2
from accounting_shipping_evidence import LINKS
from accounting_shipping_native_contract import SETUP, EVENTS
from accounting_shipping_native_routes import BASE, install_shipping_native_routes
from accounting_source_files import preserve_original
from test_mz2_shipping_native import db, OWNER, CUT, source


REVIEW = "accounting.shipping.contracts.review"


def terms(**changes):
    result = {
        "courier_id": "smsa", "payment_mode": "postpaid", "shipping_cost": "17.25",
        "shipping_cost_vat_inclusive": True, "shipping_vat_percent": "15",
        "commission_vat_inclusive": True, "commission_vat_percent": "15",
        "effective_from": CUT, "effective_to": None,
        "evidence_ref": "synthetic retained signed tariff", "source_kind": "contract",
        "cod_fee_tiers": [
            {"min_amount": "50", "max_amount": "1000", "min_inclusive": True,
             "max_inclusive": True, "commission_percent": "0.01", "fixed_fee": "2"},
            {"min_amount": "1000", "max_amount": "3000", "min_inclusive": False,
             "max_inclusive": True, "commission_percent": "0.02", "fixed_fee": "5"},
            {"min_amount": "3000", "max_amount": None, "min_inclusive": False,
             "max_inclusive": True, "commission_percent": "0.03", "fixed_fee": "0"},
        ],
    }
    result.update(changes)
    return result


@pytest_asyncio.fixture
async def api(db):
    # Remove only this disposable fixture's flat rate so rich terms are the
    # unique authority. Production replacement of an approved rate is not used.
    await db[SETUP].update_one({"_id": OWNER}, {"$set": {"contracts": []}})
    await db.users.update_one({"id": OWNER}, {"$set": {"accounting_permissions": [REVIEW]}})
    actor = {"id": OWNER}

    async def current_user():
        return actor

    app, router = FastAPI(), APIRouter()
    install_shipping_native_routes(router, db, current_user)
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://isolated") as client:
        yield SimpleNamespace(db=db, client=client, actor=actor)


async def context(api):
    response = await api.client.get(BASE + "/rich-contracts")
    assert response.status_code == 200, response.text
    return response.json()


async def mutation(api, path, fields, *, expected=200, payload=None):
    payload = payload or {
        "request_id": "rich-test-" + uuid4().hex,
        "version": (await context(api))["version"],
        "confirmed": True, "reason": "isolated explicit contract review",
        **fields,
    }
    response = await api.client.post(BASE + path, json=payload)
    assert response.status_code == expected, response.text
    return response.json(), payload


async def draft(api, **changes):
    result, payload = await mutation(api, "/rich-contracts/drafts", {
        "context": "delivery", "terms": terms(**changes),
    })
    assert result["draft"]["status"] == "draft"
    return result["draft"], payload


async def review(api, purpose, *, owner=OWNER, file_id=None, **changes):
    file_id = file_id or "rich-original-" + uuid4().hex
    await preserve_original(api.db, owner, file_id, ("SYNTHETIC SOURCE / " + purpose + " / " + file_id).encode())
    return await mutation(api, "/contract-evidence/review", {
        "courier_id": "smsa", "file_id": file_id, "purpose": purpose,
        "confirmation": "APPROVE_MZ2_SHIPPING_EVIDENCE", **changes,
    })


async def approve(api, prepared, proofs, *, expected=200, **changes):
    return await mutation(api, "/rich-contracts/approve", {
        "draft_id": prepared["id"], "draft_hash": prepared["hash"],
        "contract_evidence_id": proofs["contract"]["evidence_id"],
        "shipping_tax_evidence_id": proofs["shipping_tax"]["evidence_id"],
        "commission_tax_evidence_id": proofs["commission_tax"]["evidence_id"],
        "confirmation": "APPROVE_MZ2_SHIPPING_CONTRACT", **changes,
    }, expected=expected)


async def approved_contract(api, **changes):
    prepared, _ = await draft(api, **changes)
    proofs = {}
    for purpose in ("contract", "shipping_tax", "commission_tax"):
        result, _ = await review(api, purpose)
        proofs[purpose] = result["evidence"]
    result, payload = await approve(api, prepared, proofs)
    return result, payload, prepared, proofs


async def recognize(api, number="1"):
    response = await api.client.post(BASE + "/recognize-courier", json={"order_number": number})
    assert response.status_code == 200, response.text
    return response.json()


async def fee(api, evidence_id, *, expected=200):
    response = await api.client.post(BASE + "/accrue-fee", json={"evidence_id": evidence_id})
    assert response.status_code == expected, response.text
    return response.json()


async def rows(api):
    return await read_reporting_entries_v2(api.db, user_id=OWNER, effective_before="2030-01-01T00:00:00Z")


async def all_documents(api):
    # Unique synthetic database only; sorting keeps comparisons deterministic.
    return {name: sorted(await api.db[name].find({}).to_list(None), key=lambda row: str(row.get("_id")))
            for name in await api.db.list_collection_names()}


@pytest.mark.asyncio
async def test_rich_approved_contract_posts_original_separate_economics_once(api):
    result, approval_payload, _, proofs = await approved_contract(api)
    assert result["contract"]["kind"] == "rich"
    version = result["contract"]["contract_version"]
    assert version["verification_status"] == "approved"
    assert version["approved_by"] == OWNER
    assert version["payment_mode"] == "postpaid"
    assert len(result["contract"]["evidence_snapshot"]["items"]) == 3
    assert {p["purpose"] for p in proofs.values()} == {"contract", "shipping_tax", "commission_tax"}
    saved = await context(api)
    assert saved["contracts"] == [result["contract"]]
    assert len(saved["contract_evidence"]) == 3

    repeated, _ = await mutation(api, "/rich-contracts/approve", {}, payload=approval_payload)
    assert repeated["state"] == "already_saved"
    assert repeated["contract"] == result["contract"]
    await source(api.db, "1", value="1000.00")
    delivery = await recognize(api)
    before = await rows(api)
    accrued = await fee(api, delivery["evidence_id"])
    repeated_fee = await fee(api, delivery["evidence_id"])
    assert repeated_fee["state"] == "already_posted"
    assert repeated_fee["txn_group_id"] == accrued["txn_group_id"]
    actual = [r for r in await rows(api) if r["txn_group_id"] == accrued["txn_group_id"]]
    assert sorted((r["entity_type"], r["entity_id"], r["side"], Decimal(r["amount"])) for r in actual) == sorted([
        ("expense", "shipping", "debit", Decimal("15.00")),
        ("tax", "input_vat", "debit", Decimal("2.25")),
        ("expense", "courier_cod_commission", "debit", Decimal("10.43")),
        ("tax", "input_vat", "debit", Decimal("1.57")),
        ("courier", "smsa", "credit", Decimal("29.25")),
    ])
    assert all(r["entry_type"] == "shipping_fee_accrual" for r in actual)
    assert all(r["effective_at"] == actual[0]["effective_at"] for r in actual)
    assert actual[0]["effective_at"].startswith("2026-09-03T12:00:00")
    after = await rows(api)
    for entity in ("bank", "revenue"):
        assert [r for r in after if r["entity_type"] == entity] == [r for r in before if r["entity_type"] == entity]
    assert await api.db[EVENTS].count_documents({"kind": "fee"}) == 1
    assert await api.db[LINKS].count_documents({"link_kind": "journal"}) == 3
    assert await api.db[LINKS].count_documents({"link_kind": "journal", "link_id": accrued["txn_group_id"]}) == 3
    statement = await api.client.get(BASE + "/statements/courier/smsa")
    assert statement.status_code == 200, statement.text
    assert statement.json()["cod_receivable"] == "1000.00"
    assert statement.json()["payable"] == "29.25"


@pytest.mark.asyncio
@pytest.mark.parametrize("value,payable,commission_net,commission_vat", [
    ("50.00", "19.75", "2.17", "0.33"),
    ("1000.00", "29.25", "10.43", "1.57"),
    ("1000.01", "42.25", "21.74", "3.26"),
    ("3000.00", "82.25", "56.52", "8.48"),
    ("3000.01", "107.25", "78.26", "11.74"),
])
async def test_actual_native_tier_boundaries(api, value, payable, commission_net, commission_vat):
    await approved_contract(api)
    await source(api.db, "1", value=value)
    delivered = await recognize(api)
    result = await fee(api, delivered["evidence_id"])
    assert result["costs"]["payable_total"] == payable
    assert result["costs"]["cod_commission"] == commission_net
    assert result["costs"]["cod_commission_vat"] == commission_vat


@pytest.mark.asyncio
async def test_uncovered_tier_cannot_post_a_zero_commission(api):
    await approved_contract(api)
    await source(api.db, "1", value="49.99")
    delivered = await recognize(api)
    before = await all_documents(api)
    await fee(api, delivered["evidence_id"], expected=409)
    assert await all_documents(api) == before


@pytest.mark.asyncio
async def test_independent_tax_flags_and_prepaid_mode_preserve_cod_and_payable(api):
    await approved_contract(api, payment_mode="prepaid", commission_vat_inclusive=False)
    await source(api.db, "1", value="1000.00")
    delivered = await recognize(api)
    result = await fee(api, delivered["evidence_id"])
    assert result["costs"]["shipping_net"] == "15.00"
    assert result["costs"]["shipping_vat"] == "2.25"
    assert result["costs"]["cod_commission"] == "12.00"
    assert result["costs"]["cod_commission_vat"] == "1.80"
    assert result["costs"]["payable_total"] == "31.05"
    response = await api.client.get(BASE + "/statements/courier/smsa")
    assert response.json()["cod_receivable"] == "1000.00"
    assert response.json()["payable"] == "31.05"


@pytest.mark.asyncio
async def test_non_cod_delivery_uses_no_cod_tier_or_sale(api):
    await approved_contract(api)
    await source(api.db, "1", value="1000.00", payment_method="credit_card")
    before = await rows(api)
    response = await api.client.post(BASE + "/courier-delivery-fee", json={"order_number": "1"})
    assert response.status_code == 200, response.text
    costs = response.json()["fee"]["costs"]
    assert costs["payable_total"] == "17.25"
    assert costs["cod_commission"] == "0.00"
    assert costs["cod_commission_vat"] == "0.00"
    after = await rows(api)
    assert [r for r in after if r["entity_type"] in {"bank", "revenue"}] == [r for r in before if r["entity_type"] in {"bank", "revenue"}]
    assert not any(r.get("sub_account") == "cod_receivable" for r in after)


@pytest.mark.asyncio
async def test_zero_rich_cost_has_no_journal_or_false_journal_evidence_link(api):
    zero_tiers = [{**row, "commission_percent": "0", "fixed_fee": "0"} for row in terms()["cod_fee_tiers"]]
    approved, _, _, _ = await approved_contract(api, shipping_cost="0", shipping_vat_percent="0",
        commission_vat_percent="0", cod_fee_tiers=zero_tiers)
    delivered = await recognize(api)
    before = await rows(api)
    result = await fee(api, delivered["evidence_id"])
    assert result["state"] == "posted"
    assert result["txn_group_id"] is None
    assert result["costs"]["payable_total"] == "0.00"
    assert await rows(api) == before
    assert await api.db[LINKS].count_documents({"link_kind": "journal"}) == 0
    assert await api.db[LINKS].count_documents({"link_kind": "contract", "link_id": approved["contract"]["id"]}) == 1
    assert await api.db[EVENTS].count_documents({"kind": "fee"}) == 1


@pytest.mark.asyncio
async def test_setup_approval_while_paused_cannot_post_or_change_control(api):
    await api.db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": True}})
    before = await api.db.mz2_atomic_owners.find_one({"_id": OWNER})
    settings = await api.db.settings.find_one({"user_id": OWNER})
    await approved_contract(api)
    response = await api.client.post(BASE + "/recognize-courier", json={"order_number": "1"})
    assert response.status_code == 423, response.text
    assert await api.db.mz2_atomic_owners.find_one({"_id": OWNER}) == before
    assert await api.db.settings.find_one({"user_id": OWNER}) == settings
    assert await api.db[EVENTS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_owner_without_explicit_review_cannot_approve_evidence_or_contract(api):
    prepared, _ = await draft(api)
    proofs = {}
    for purpose in ("contract", "shipping_tax", "commission_tax"):
        result, _ = await review(api, purpose)
        proofs[purpose] = result["evidence"]
    await api.db.users.update_one({"id": OWNER}, {"$set": {"accounting_permissions": []}})
    before = await all_documents(api)
    await approve(api, prepared, proofs, expected=403)
    _, _ = await mutation(api, "/contract-evidence/review", {
        "courier_id": "smsa", "file_id": proofs["contract"]["file_id"], "purpose": "contract",
        "confirmation": "APPROVE_MZ2_SHIPPING_EVIDENCE",
    }, expected=403)
    assert await all_documents(api) == before


@pytest.mark.asyncio
async def test_foreign_original_and_forged_review_metadata_fail_closed(api):
    foreign = "foreign-original-" + uuid4().hex
    await preserve_original(api.db, "another-owner", foreign, b"FOREIGN SYNTHETIC ORIGINAL")
    before = await all_documents(api)
    fields = {"courier_id": "smsa", "file_id": foreign, "purpose": "contract",
              "confirmation": "APPROVE_MZ2_SHIPPING_EVIDENCE"}
    await mutation(api, "/contract-evidence/review", fields, expected=409)
    for field, value in (("approved_by", OWNER), ("state", "approved"), ("source_sha256", "a" * 64)):
        await mutation(api, "/contract-evidence/review", {**fields, field: value}, expected=422)
    assert await all_documents(api) == before


@pytest.mark.asyncio
async def test_reviewer_downloads_exact_original_and_foreign_tampered_sources_fail(api):
    file_id, foreign = "review-original", "another-owner-original"
    content = b"SYNTHETIC ORIGINAL FOR AUTHENTICATED MANUAL REVIEW"
    await preserve_original(api.db, OWNER, file_id, content)
    await preserve_original(api.db, "another-owner", foreign, b"FOREIGN ORIGINAL")
    before = await all_documents(api)
    response = await api.client.get(BASE + "/contract-evidence/files/" + file_id)
    assert response.status_code == 200, response.text
    assert response.content == content
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert response.headers["content-disposition"].startswith("attachment;")
    denied = await api.client.get(BASE + "/contract-evidence/files/" + foreign)
    assert denied.status_code == 409, denied.text
    assert await all_documents(api) == before
    await api.db.accounting_source_files.update_one({"user_id": OWNER, "file_id": file_id}, {"$set": {"content": b"TAMPERED"}})
    tampered = await api.client.get(BASE + "/contract-evidence/files/" + file_id)
    assert tampered.status_code == 409, tampered.text
    assert tampered.json()["detail"]["code"] == "shipping_evidence_original_hash_mismatch"
    await api.db.users.update_one({"id": OWNER}, {"$set": {"accounting_permissions": []}})
    denied = await api.client.get(BASE + "/contract-evidence/files/" + file_id)
    assert denied.status_code == 403, denied.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["revoke", "tamper", "delete", "purpose"])
async def test_evidence_invalidated_after_approval_cannot_post(api, failure):
    _, _, _, proofs = await approved_contract(api)
    ready = await api.client.get(BASE + "/context")
    assert ready.status_code == 200, ready.text
    assert ready.json()["stages"]["7"]["ready"] is True
    delivered = await recognize(api)
    proof = proofs["shipping_tax"]
    if failure == "revoke":
        await mutation(api, "/contract-evidence/revoke", {
            "evidence_id": proof["evidence_id"], "revision": proof["revision"],
            "confirmation": "REVOKE_MZ2_SHIPPING_EVIDENCE",
        })
    elif failure == "tamper":
        await api.db.accounting_source_files.update_one({"user_id": OWNER, "file_id": proof["file_id"]}, {"$set": {"content": b"altered"}})
    elif failure == "delete":
        await api.db.accounting_source_files.delete_one({"user_id": OWNER, "file_id": proof["file_id"]})
    else:
        await api.db[SETUP].update_one({"_id": OWNER, "contract_evidence.evidence_id": proof["evidence_id"]},
            {"$set": {"contract_evidence.$.purpose": "commission_tax"}})
    before = await all_documents(api)
    blocked = await api.client.get(BASE + "/context")
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["stages"]["7"]["ready"] is False
    await fee(api, delivered["evidence_id"], expected=409)
    assert await all_documents(api) == before


@pytest.mark.asyncio
async def test_wrong_purpose_evidence_cannot_approve_contract(api):
    prepared, _ = await draft(api)
    proofs = {}
    for purpose in ("contract", "shipping_tax", "commission_tax"):
        result, _ = await review(api, purpose)
        proofs[purpose] = result["evidence"]
    before = await all_documents(api)
    await approve(api, prepared, proofs, expected=409,
                  shipping_tax_evidence_id=proofs["contract"]["evidence_id"])
    assert await all_documents(api) == before


@pytest.mark.asyncio
async def test_employee_reviewer_has_only_explicit_review_authority(api):
    prepared, _ = await draft(api)
    reviewer = "synthetic-contract-reviewer"
    await api.db.users.insert_one({"id": reviewer, "role": "employee", "created_by": OWNER,
        "is_active": True, "accounting_permissions": ["accounting.shipping.view", REVIEW]})
    api.actor["id"] = reviewer
    proofs = {}
    for purpose in ("contract", "shipping_tax", "commission_tax"):
        result, _ = await review(api, purpose)
        assert result["evidence"]["approved_by"] == reviewer
        proofs[purpose] = result["evidence"]
    result, _ = await approve(api, prepared, proofs)
    assert result["contract"]["contract_version"]["approved_by"] == reviewer
    before = await all_documents(api)
    await mutation(api, "/rich-contracts/drafts", {"context": "delivery", "terms": terms()}, expected=403)
    response = await api.client.post(BASE + "/recognize-courier", json={"order_number": "1"})
    assert response.status_code == 403, response.text
    assert await all_documents(api) == before


@pytest.mark.asyncio
async def test_evidence_approval_replay_does_not_create_new_review_or_overwrite_actor(api):
    result, payload = await review(api, "contract")
    before = await all_documents(api)
    repeated, _ = await mutation(api, "/contract-evidence/review", {}, payload=payload)
    assert repeated["state"] == "already_saved"
    assert repeated["evidence"] == result["evidence"]
    await mutation(api, "/contract-evidence/review", {}, payload={**payload, "purpose": "shipping_tax"}, expected=409)
    assert await all_documents(api) == before


@pytest.mark.asyncio
async def test_changed_hash_of_still_draft_contract_is_rejected_before_approval(api):
    prepared, _ = await draft(api)
    proofs = {}
    for purpose in ("contract", "shipping_tax", "commission_tax"):
        result, _ = await review(api, purpose)
        proofs[purpose] = result["evidence"]
    before = await all_documents(api)
    result, _ = await approve(api, prepared, proofs, expected=409, draft_hash="a" * 64)
    assert result["detail"]["code"] == "shipping_contract_draft_hash_mismatch"
    assert await all_documents(api) == before


@pytest.mark.asyncio
async def test_another_couriers_review_cannot_authorize_this_contract(api):
    await mutation(api, "/couriers", {"courier_key": "other-courier", "name": "Other courier",
                                      "salla_carrier_keys": ["other-canonical-source"]})
    prepared, _ = await draft(api)
    proofs = {}
    for purpose in ("contract", "shipping_tax", "commission_tax"):
        result, _ = await review(api, purpose, courier_id="other-courier" if purpose == "contract" else "smsa")
        proofs[purpose] = result["evidence"]
    before = await all_documents(api)
    await approve(api, prepared, proofs, expected=409)
    assert await all_documents(api) == before


@pytest.mark.asyncio
async def test_evidence_revocation_does_not_rewrite_posted_fee_on_replay(api):
    _, _, _, proofs = await approved_contract(api)
    delivered = await recognize(api)
    posted = await fee(api, delivered["evidence_id"])
    proof = proofs["contract"]
    await mutation(api, "/contract-evidence/revoke", {
        "evidence_id": proof["evidence_id"], "revision": proof["revision"],
        "confirmation": "REVOKE_MZ2_SHIPPING_EVIDENCE",
    })
    before_rows = await rows(api)
    repeated = await fee(api, delivered["evidence_id"])
    assert repeated["state"] == "already_posted"
    assert repeated["txn_group_id"] == posted["txn_group_id"]
    assert await rows(api) == before_rows
    assert await api.db[EVENTS].count_documents({"kind": "fee"}) == 1


@pytest.mark.asyncio
async def test_concurrent_same_approval_and_fee_are_idempotent(api):
    prepared, _ = await draft(api)
    proofs = {}
    for purpose in ("contract", "shipping_tax", "commission_tax"):
        result, _ = await review(api, purpose)
        proofs[purpose] = result["evidence"]
    payload = {"request_id": "concurrent-approval-request", "version": (await context(api))["version"],
        "confirmed": True, "reason": "concurrent exact request", "draft_id": prepared["id"],
        "draft_hash": prepared["hash"], "confirmation": "APPROVE_MZ2_SHIPPING_CONTRACT",
        **{key + "_evidence_id": value["evidence_id"] for key, value in proofs.items()}}
    responses = await asyncio.gather(*[api.client.post(BASE + "/rich-contracts/approve", json=payload) for _ in range(2)])
    # A CAS conflict may be retried only with the identical request. It must
    # return the saved approval instead of creating a second approved version.
    successful = []
    for response in responses:
        assert response.status_code in {200, 409}, response.text
        if response.status_code == 409:
            response = await api.client.post(BASE + "/rich-contracts/approve", json=payload)
        assert response.status_code == 200, response.text
        successful.append(response.json()["contract"])
    assert successful[0] == successful[1]
    assert len((await context(api))["contracts"]) == 1
    delivered = await recognize(api)
    fees = await asyncio.gather(*[fee(api, delivered["evidence_id"]) for _ in range(2)])
    assert fees[0]["txn_group_id"] == fees[1]["txn_group_id"]
    assert await api.db[EVENTS].count_documents({"kind": "fee"}) == 1


@pytest.mark.asyncio
async def test_changed_approval_request_and_stale_draft_hash_cannot_mutate(api):
    result, payload, prepared, proofs = await approved_contract(api)
    before = await all_documents(api)
    await mutation(api, "/rich-contracts/approve", {}, payload={**payload, "reason": "changed request intent"}, expected=409)
    await approve(api, prepared, proofs, expected=409, draft_hash="a" * 64)
    assert await all_documents(api) == before


@pytest.mark.asyncio
async def test_flat_contract_cannot_overlap_approved_rich_contract(api):
    await approved_contract(api)
    before = await all_documents(api)
    await mutation(api, "/rates", {
        "party_type": "courier", "party_id": "smsa", "context": "delivery",
        "effective_from": CUT, "effective_to": None, "currency": "SAR",
        "delivery_fee": "1.00", "cod_fixed_fee": "0.00", "cod_percent": "0",
        "vat_percent": "0", "vat_included": False,
        "vat_treatment": "gross_expense_no_input_vat", "contract_reference": "overlapping explicit tariff",
    }, expected=409)
    assert await all_documents(api) == before


@pytest.mark.asyncio
async def test_failure_after_real_journal_rolls_back_evidence_links_and_event(api, monkeypatch):
    await approved_contract(api)
    delivered = await recognize(api)
    before = await all_documents(api)
    import accounting_shipping_native as native
    actual_post = native._post

    async def fail_after_journal(*args, **kwargs):
        await actual_post(*args, **kwargs)
        raise HTTPException(409, detail={"code": "synthetic_failure_after_real_journal"})

    with monkeypatch.context() as patch:
        patch.setattr(native, "_post", fail_after_journal)
        result = await fee(api, delivered["evidence_id"], expected=409)
        assert result["detail"]["code"] == "synthetic_failure_after_real_journal"
    assert await all_documents(api) == before
    result = await fee(api, delivered["evidence_id"])
    assert result["state"] == "posted"
    assert await api.db[EVENTS].count_documents({"kind": "fee"}) == 1


@pytest.mark.asyncio
async def test_failure_after_actual_source_pins_rolls_back_journal_and_links(api, monkeypatch):
    await approved_contract(api)
    delivered = await recognize(api)
    before = await all_documents(api)
    import accounting_shipping_evidence as evidence
    actual_pin = evidence.pin_bundle

    async def fail_after_pins(*args, **kwargs):
        await actual_pin(*args, **kwargs)
        raise HTTPException(409, detail={"code": "synthetic_failure_after_actual_pins"})

    with monkeypatch.context() as patch:
        patch.setattr(evidence, "pin_bundle", fail_after_pins)
        result = await fee(api, delivered["evidence_id"], expected=409)
        assert result["detail"]["code"] == "synthetic_failure_after_actual_pins"
    assert await all_documents(api) == before
    result = await fee(api, delivered["evidence_id"])
    assert result["state"] == "posted"
    assert await api.db[LINKS].count_documents({"link_kind": "journal", "link_id": result["txn_group_id"]}) == 3
