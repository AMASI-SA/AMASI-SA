"""Synthetic, pure projection tests. No database or financial posting is used."""
from copy import deepcopy
from decimal import Decimal
import pytest
from operational_bundle_cost_projection import project_bundle_costs, _leaf_key

YES = [{"option_id": "gift", "value_id": "yes"}]
NO = [{"option_id": "gift", "value_id": "no"}]


def leaf(key, cost, qty="1", **updates):
    return dict(leaf_id=key, kind="product", product_id=key, quantity=qty,
                unit_cost=cost, sourcing_mode="supplier", supplier_id="assigned-only", **updates)


def snapshot(*, rose=True, embroidery=False, **updates):
    leaves = [leaf("shawl", "15", current_product_reference=True)]
    if rose:
        leaves.append(leaf("rose", "40"))
    if embroidery:
        leaves.append(dict(leaf_id="name", kind="resource", resource_id="embroidery",
                           quantity="1", unit_cost="10", cost_only=True))
    total = sum(Decimal(l["quantity"]) * Decimal(l["unit_cost"]) for l in leaves)
    return dict(product_id="bundle", revision=3, leaves=leaves, estimated_cost=str(total),
                cost_complete=True, selected_options=YES if rose else NO, **updates)


def project(snap=None, **updates):
    snap = snapshot() if snap is None else snap
    args = dict(owner="owner", order_number="order", order_item_id="line", product_id="bundle",
                sale_quantity=4, status="under_review", selected_options=snap["selected_options"], snapshot=snap)
    args.update(updates)
    return project_bundle_costs(**args)


def units(snap=None):
    snap = snap or snapshot()
    return [dict(user_id="owner", order_id="order", order_line_id="line", unit_index=i,
                 state="reserved", bundle_leaves=[dict(l, state="awaiting_supplier") for l in snap["leaves"]])
            for i in range(1, 5)]


def test_conditional_bouquet_is_220_not_parent_only_or_duplicated():
    r = project()
    assert r["recipe_cost_sar"] == r["unreceived_expected_sar"] == "220.00"
    assert len(r["rows"]) == 8
    assert sum(Decimal(x["cost_sar"]) for x in r["rows"]) == Decimal("220")
    assert r["complete"] and not r["posting_allowed"]
    assert all(x["supplier_id"] is None and x["confirmed_payable_sar"] is None for x in r["rows"])


def test_no_bouquet_and_optional_embroidery():
    assert project(snapshot(rose=False))["unreceived_expected_sar"] == "60.00"
    assert project(snapshot(embroidery=True))["unreceived_expected_sar"] == "260.00"


def test_unconditional_recipe_does_not_require_a_customer_choice():
    s = snapshot(); s["selected_options"] = []
    assert project(s)["unreceived_expected_sar"] == "220.00"


def test_flattened_nested_children_multiply_only_once():
    s = snapshot(); s["leaves"][1]["quantity"] = "2"; s["estimated_cost"] = "95"
    assert project(s)["unreceived_expected_sar"] == "380.00"
    assert project(s, sale_quantity=1)["unreceived_expected_sar"] == "95.00"


@pytest.mark.parametrize("status", ["under_review", "in_progress", "انتظار المراجعة", "قيد التنفيذ",
                                    {"slug": "under_review", "name": "بانتظار المراجعة"}])
def test_allowed_unreceived_statuses(status):
    assert project(status=status)["unreceived_expected_sar"] == "220.00"


@pytest.mark.parametrize("status", ["reviewed", "completed", "delivering", "delivered", "shipped",
                                    "cancelled", "pending", "unknown", "تم المراجعة", "تم التنفيذ", "تم التوصيل"])
def test_other_statuses_do_not_create_unreceived_estimates(status):
    r = project(status=status)
    assert r["unreceived_expected_sar"] == "0.00" and r["recipe_cost_sar"] == "220.00"


@pytest.mark.parametrize("q", [0, -1, True, "NaN", "Infinity", "1.5", 10001])
def test_invalid_quantities_are_unknown_not_zero(q):
    r = project(sale_quantity=q)
    assert not r["complete"] and r["unreceived_expected_sar"] is None


@pytest.mark.parametrize("cost", [None, True, "NaN", "Infinity", "-1", "oops"])
def test_missing_bad_cost_does_not_erase_the_problem_with_zero(cost):
    s = snapshot(); s["leaves"][1]["unit_cost"] = cost
    r = project(s)
    assert not r["complete"] and r["recipe_cost_sar"] is None and r["unreceived_expected_sar"] is None


def test_zero_cost_can_be_explicit_and_verified():
    s = snapshot(); s["leaves"][1]["unit_cost"] = "0"; s["estimated_cost"] = "15"
    assert project(s)["unreceived_expected_sar"] == "60.00"


def test_wrong_selection_is_rejected():
    r = project(selected_options=NO)
    assert r["unreceived_expected_sar"] is None
    assert r["issues"][0]["code"] == "bundle_selected_options_unresolved"


def test_missing_selection_is_not_assumed_to_mean_no():
    s = snapshot(); del s["selected_options"]
    r = project_bundle_costs(owner="owner", order_number="order", order_item_id="line", product_id="bundle",
                            sale_quantity=4, status="under_review", selected_options=YES, snapshot=s)
    assert r["unreceived_expected_sar"] is None


@pytest.mark.parametrize("field,value", [("cost_complete",False), ("needs_selection",True),
    ("historical_cost_verified",False), ("unresolved_components",["missing"]), ("product_id","other"),
    ("sale_quantity",3), ("estimated_cost","80"), ("cost_currency","KWD")])
def test_invalid_or_changed_snapshot_fails_closed(field,value):
    s = snapshot(); s[field] = value
    assert project(s)["unreceived_expected_sar"] is None


def test_duplicate_leaf_rejected_not_silently_deduped():
    s = snapshot(); s["leaves"].append(deepcopy(s["leaves"][0]));s["estimated_cost"]="70"
    r=project(s)
    assert r["issues"][0]["code"] == "bundle_leaf_identity_duplicate"


def test_two_paths_to_same_product_are_independent():
    s = snapshot()
    a=deepcopy(s["leaves"][1]);a["leaf_id"]="rose-other-path";s["leaves"].append(a);s["estimated_cost"]="95"
    assert project(s)["recipe_cost_sar"] == "380.00"


def test_no_input_mutation_or_id_churn_on_retry():
    s, u = snapshot(), units();before=deepcopy((s,u))
    a=project(s,native_units=u);b=project(s,native_units=u)
    assert a==b and before==(s,u)
    assert len({x["id"] for x in a["rows"]})==8
    c=project(s,owner="other")
    assert not {x["id"] for x in a["rows"]}&{x["id"] for x in c["rows"]}


def test_allocated_or_received_child_is_not_counted_as_unreceived():
    u=units();u[0]["bundle_leaves"][1]["received_quantity"]="1"
    r=project(native_units=u)
    assert not r["complete"] and r["unreceived_expected_sar"] is None
    assert r["known_unreceived_subtotal_sar"]=="180.00"
    assert r["recipe_cost_sar"]=="220.00"
    assert len(r["issues"])==1 and all(x["confirmed_payable_sar"] is None for x in r["rows"])


@pytest.mark.parametrize("field,value", [("reserved_quantity","1"),("consumed_quantity","1"),
    ("receipt_ids",["receipt"]),("state","consumed"),("received_quantity","2"),
    ("sourcing_segments",[{"quantity":"1","actual_cost":"40"}])])
def test_physical_and_segment_evidence_requires_matching_not_second_cost(field,value):
    u=units();u[0]["bundle_leaves"][1][field]=value
    r=project(native_units=u)
    assert not r["complete"] and r["recipe_cost_sar"]=="220.00"
    assert r["known_unreceived_subtotal_sar"]=="180.00"


def test_consumed_unit_without_quantities_not_assumed_unreceived():
    u=units();u[0]["state"]="consumed"
    r=project(native_units=u)
    assert not r["complete"] and r["known_unreceived_subtotal_sar"]=="165.00"


@pytest.mark.parametrize("field,value", [("user_id","other"),("order_id","other"),("order_line_id","other"),
    ("unit_index",True),("unit_index",0),("unit_index",2),("state","released")])
def test_native_scope_and_coverage_mismatch(field,value):
    u=units();u[0][field]=value
    assert project(native_units=u)["unreceived_expected_sar"] is None


def test_partial_units_are_not_counted_as_a_complete_line():
    assert project(native_units=units()[:2])["unreceived_expected_sar"] is None


def test_unit_leaf_identity_not_matched_by_price_or_sku():
    u=units();u[0]["bundle_leaves"][1]["product_id"]="another-rose"
    assert project(native_units=u)["unreceived_expected_sar"] is None


def test_rounding_conserves_line_not_each_fractional_leaf():
    s=snapshot();s["leaves"][0]["unit_cost"]="0.003";s["leaves"][1]["unit_cost"]="0.004";s["estimated_cost"]="0.007"
    r=project(s)
    assert r["recipe_cost_sar"]==r["unreceived_expected_sar"]=="0.03"
    assert sum(Decimal(x["cost_sar"]) for x in r["rows"])==Decimal("0.03")


def test_leaf_currency_not_customer_currency():
    s=snapshot();s["leaves"][1]["cost_currency"]="SAR";s["customer_currency"]="KWD"
    assert project(s)["recipe_cost_sar"]=="220.00"
    s["leaves"][1]["cost_currency"]="KWD"
    assert project(s)["recipe_cost_sar"] is None


def test_native_key_contract_preserved():
    import hashlib,json
    a=dict(path=["bundle","nested","rose"],kind="product",product_id="rose",quantity="1",unit_cost="40",sourcing_mode="auto")
    payload={k:a.get(k) for k in ("path","kind","product_id","resource_id","variant_id","sourcing_mode","supplier_id")}
    expected=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    assert _leaf_key(a)==expected
