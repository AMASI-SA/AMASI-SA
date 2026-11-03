"""Owner-confirmed native fees from the settings preview through operational recognition."""
from datetime import date

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from accounting_onboarding_ssot import FeePolicyCreate, create_fee_policy, list_fee_candidates, list_fee_policies
from operational_balance_engine import reconcile
from operational_payment_evidence import normalize_payment
from operational_provider_fees import provider_projection
from test_onboarding_ssot_contracts import DB, run
from tests.test_accounting_onboarding import api, mongo_db, request, fingerprint, OWNER
from test_operational_balance_order_page_recognition import state, raw_document, normalize, T1, T2, BORN, KEY

DAY = date(2026, 10, 5)


def saved(db, **changes):
    payload = dict(provider="salla", percentage="2", fixed_amount="1", vat_treatment="exclusive", vat_rate="15",
                   currency="SAR", effective_from="2026-01-01", evidence="fixture contract")
    return run(create_fee_policy(db, "shop", "reviewer", FeePolicyCreate(**{**payload, **changes})))


def payment(method, plan=None):
    raw = {"payment_method": method, "payment": {"paid_amount": "100", "paid_at": T1, "reference": "p1", "status": "captured"}}
    if plan:
        raw["payment"]["plan_id"] = plan
    p, errors = normalize_payment(raw, "SAR", "100", BORN, T2)
    assert p and not errors
    return p


def test_same_day_rail_and_plan_rates_resolve_without_cross_charging():
    db = DB()
    mada = saved(db, payment_method="mada", percentage="1")
    visa = saved(db, payment_method="visa", percentage="3")
    plan = saved(db, provider="tamara", plan_id="12", percentage="7", fixed_amount="0")
    other = saved(db, provider="tamara", plan_id="4", percentage="4")
    with pytest.raises(HTTPException, match="overlap"):
        saved(db, payment_method="mada", percentage="5")
    with pytest.raises(HTTPException, match="overlap"):
        saved(db, plan_id="crossed-plan")
    rates = run(list_fee_policies(db, "shop"))
    for method, rate, fee in [("مدى", mada, "2.30"), ({"slug": "visa"}, visa, "4.60"), ("tamara", plan, "8.05")]:
        r = provider_projection(payment(method, "12" if method == "tamara" else None), rates, DAY)
        assert (r["policy_id"], r["estimated_fees"]) == (rate["id"], fee)
    assert provider_projection(payment("tamara", "4"), rates, DAY)["policy_id"] == other["id"]
    with pytest.raises(ValueError, match="method_required"):
        provider_projection(payment("salla"), rates, DAY)
    with pytest.raises(ValueError, match="plan_required"):
        provider_projection(payment("tamara"), rates, DAY)
    assert run(list_fee_policies(db, "other")) == []


def test_dated_capture_uses_riyadh_payment_date_and_undated_page_rejects_a_rate_change():
    db = DB()
    old = saved(db, provider="tamara", effective_to="2026-10-05")
    new = saved(db, provider="tamara", percentage="4", effective_from="2026-10-06")
    rates = run(list_fee_policies(db, "shop"))
    p = payment("tamara")
    p["captures"][0]["captured_at"] = "2026-10-05T21:30:00Z"
    order = {"id": "1001", "order_number": "123", "created_at": BORN, "updated_at": T2, "currency": "SAR", "payment": p}
    s = reconcile(state(), {"orders": [order], "fee_policies": rates}, T2)
    assert s["engine"]["provider_reports"][KEY]["policy_id"] == new["id"]
    assert s["engine"]["obligations"][KEY]["confirmed"] == "100.00"
    assert not s["movements"] and s["openings"] == {}
    page, problems = normalize({**raw_document("100", "0"), "updated_at": T2})
    assert not problems
    order["payment"] = page
    unknown = reconcile(state(), {"orders": [order], "fee_policies": rates}, T2)
    assert KEY not in unknown["engine"]["provider_reports"]
    assert unknown["engine"]["obligations"][KEY]["confirmed"] == "100.00"
    # A temporary specific tariff wholly inside a wider window also precludes choosing the broad rate.
    broad = {**old, "provider": "salla", "effective_to": None}
    narrow = {**new, "provider": "salla", "payment_method": "mada", "effective_from":"2026-10-03", "effective_to":"2026-10-04"}
    with pytest.raises(ValueError, match="transaction_date_required"):
        provider_projection(payment("mada"), [broad, narrow], DAY, earliest_date=date(2026,10,1))


def test_component_schedule_can_be_created_and_keeps_nonrefundable_fees_and_vat():
    db = DB()
    terms = dict(vat_treatment="exclusive", vat_rate="15", cancellation_fee_treatment="recalculate")
    components = [dict(terms, id="refundable", percentage="5", fixed_amount="0", refund_fee_treatment="recalculate"),
                  dict(terms, id="retained", percentage="2", fixed_amount="1", refund_fee_treatment="retain")]
    policy = saved(db, provider="tabby", components=components, percentage="7")
    p = payment("tabby")
    p["refunds"] = [{"id":"refund", "amount":"100", "currency":"SAR", "status":"executed"}]
    result = provider_projection(p, [policy], DAY)
    assert result["estimated_fee_net"] == "3.00" and result["estimated_fee_tax"] == "0.45"
    assert result["refunded_fee_net"] == "5.00"
    with pytest.raises(ValidationError, match="total_mismatch"):
        saved(db, provider="emkan", components=components, percentage="6")
    with pytest.raises(ValidationError, match="tax_rate_required"):
        saved(db, vat_rate=None)


def test_preview_is_scoped_and_read_only_and_never_supplies_missing_default_or_duplicate_rate():
    class PreviewDB(DB):
        allowed = DB.allowed | {"settings"}
    db = PreviewDB()
    assert run(list_fee_candidates(db, "shop")) == []
    fee = dict(name="مدى", commission_percent="2", fixed_fee=0, vat_percent=15)
    db["settings"].rows = [{"user_id":"foreign","payment_methods":[dict(fee,name="تابي")]},
                           {"user_id":"shop","payment_methods":[fee,dict(fee,name="mada"), dict(fee,name="تمارا"),
                            dict(fee,name="إمكان",fixed_fee=None),dict(fee,name="تحويل بنكي")]}]
    previews = run(list_fee_candidates(db,"shop"))
    assert previews == [{"provider":"tamara","payment_method":None,"label":"تمارا","percentage":"2","fixed_amount":"0","vat_rate":"15"}]
    assert run(list_fee_policies(db, "shop")) == []
    db["settings"].rows.append({"user_id":"shop"})
    with pytest.raises(HTTPException, match="owner_ambiguous"):
        run(list_fee_candidates(db,"shop"))


def test_conflicting_fee_labels_are_reported_and_never_offered_as_a_rate():
    from operational_fee_methods import fee_rail
    from operational_payment_evidence import provider_for

    class PreviewDB(DB):
        allowed = DB.allowed | {"settings"}
    db = PreviewDB()
    fee = dict(commission_percent="2", fixed_fee=0, vat_percent=15)
    rows = [dict(fee, name="البطاقة الإئتمانية"), dict(fee, name="بطاقة ائتمانية", commission_percent=3),
            dict(fee, name="EmkanInstallment", fixed_fee=None), dict(fee, name="إمكان"),
            dict(fee, name="تمارا", vat_percent=None), dict(fee, name="مدى"), dict(fee, name="Visa")]
    db["settings"].rows = [{"user_id": "shop", "payment_methods": rows}]

    for alias in ("البطاقة الإئتمانية", "البطاقه الائتمانيه", "بطاقة ائتمانية"):
        assert fee_rail(alias) == "credit_card"
    assert fee_rail("البطاقة الإئتمانية لمزود آخر") is None
    assert provider_for("البطاقة الإئتمانية") is None  # Fee labels do not change gross claim routing.

    for ordered in (rows, list(reversed(rows))):
        db["settings"].rows[0]["payment_methods"] = ordered
        preview = run(list_fee_candidates(db, "shop", include_conflicts=True))
        assert sorted(r["payment_method"] for r in preview["items"]) == ["mada", "visa"]
        conflicts = {(r["provider"], r["payment_method"]): (r["reason"], set(r["labels"])) for r in preview["conflicts"]}
        assert conflicts == {
            ("salla", "credit_card"): ("duplicate_settings", {"البطاقة الإئتمانية", "بطاقة ائتمانية"}),
            ("emkan", None): ("duplicate_settings", {"EmkanInstallment", "إمكان"}),
            ("tamara", None): ("incomplete_settings", {"تمارا"}),
        }
    db["settings"].rows[0]["payment_methods"] = [rows[0]]
    assert run(list_fee_candidates(db, "shop", include_conflicts=True)) == {"items": [
        {"label":"البطاقة الإئتمانية","provider":"salla","payment_method":"credit_card","percentage":"2",
         "fixed_amount":"0","vat_rate":"15"}], "conflicts":[]}
    assert run(list_fee_policies(db, "shop")) == []


@pytest.mark.asyncio
async def test_scoped_preview_http_and_explicit_confirmation_only_write_metadata(api):
    terms = dict(name="مدى", commission_percent=1.25, fixed_fee=1, vat_percent=15)
    await api.db.settings.insert_many([{"user_id": OWNER, "payment_methods": [
        terms, dict(terms, name="إمكان"), dict(terms, name="EmkanInstallment", fixed_fee=2)], "unrelated_private_field":"hidden"},
        {"user_id": "other", "payment_methods": [{**terms,"commission_percent":98}]}])
    before = await fingerprint(api, exclude=())
    await request(api, "GET", "/fee-policy-candidates", user="viewer", status=403)
    preview = await request(api, "GET", "/fee-policy-candidates")
    assert preview["conflicts"] == [{"provider":"emkan", "payment_method":None, "reason":"duplicate_settings",
                                     "labels":["إمكان", "EmkanInstallment"]}]
    assert preview["items"] == [
        {"label":"مدى", "provider":"salla", "payment_method":"mada", "percentage":"1.25", "fixed_amount":"1", "vat_rate":"15"}]
    assert await fingerprint(api, exclude=()) == before
    config = dict(provider="salla", payment_method="mada", percentage="1.25", fixed_amount="1", vat_rate="15",
                  vat_treatment="exclusive", currency="SAR", effective_from="2026-11-01", evidence="synthetic merchant contract")
    before_write = await fingerprint(api, exclude=("mz2_provider_fee_policies_v2",))
    await request(api,"POST","/fee-policies",config,user="viewer",status=403)
    approved = await request(api,"POST","/fee-policies",config)
    assert approved["user_id"] == OWNER and approved["confirmed_by"] == "full"
    await request(api,"POST","/fee-policies",config,status=409)
    assert await fingerprint(api, exclude=("mz2_provider_fee_policies_v2",)) == before_write


def test_refund_uses_rail_on_original_approved_payment_snapshot():
    from operational_provider_fees import with_recorded_refund
    original = payment("mada")
    with pytest.raises(ValueError, match="method_conflict"):
        with_recorded_refund(payment("visa"), original, "one", "10")
    legacy_snapshot = {k: v for k,v in original.items() if k != "payment_method"}
    historical = with_recorded_refund(original, legacy_snapshot, "one", "10")
    assert "payment_method" not in historical
