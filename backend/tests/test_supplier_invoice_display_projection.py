"""Display acceptance; all data synthetic. Persistence is deliberately not grouped."""
from copy import deepcopy
import pytest
from supplier_invoice_display import project_supplier_invoice_display


def fixture(n=2, *, services=None, costs=None, variants=None, options=None):
    pieces=[]; lines=[]
    for i in range(n):
        service=(services or ["engrave"]*n)[i]
        cost=(costs or [5000]*n)[i]
        pieces.append({"piece_id":f"piece-{i}","product_id":"p","sku":"SKU",
          "variant_id":(variants or ["v"]*n)[i],"order_item_id":f"item-{i}",
          "product_options":(options or [{"text":"A"}]*n)[i]})
        lines.append({"source_key":f"line-{i}","piece_ids":[f"piece-{i}"],
          "product_unit_price_halalas":cost-100,
          "services":[{"service_id":service,"service_name":service,
          "quantity_per_piece":1,"unit_price_halalas":100}]})
    return lines,pieces


@pytest.mark.parametrize("case,kwargs,count",[
    ("A",{},1),
    ("B",{"services":["engrave","wrap"]},1),
    ("C",{"costs":[5000,5100]},2),
    ("D",{"costs":[5000,7000],"options":[{}, {"age":12}]},2),
    ("E",{"options":[{"name":"A"},{"name":"B"}]},1),
    ("F",{"variants":["v1","v2"]},2),
    ("G",{"n":200},1),
    ("H",{"n":200,"variants":[f"v{i%4}" for i in range(200)],
       "costs":[5000+100*(i%5) for i in range(200)],
       "services":[f"svc{i%7}" for i in range(200)]},20),
])
def test_acceptance(case,kwargs,count):
    lines,pieces=fixture(**kwargs);before=deepcopy((lines,pieces))
    view=project_supplier_invoice_display(lines,pieces)
    assert len(view["cards"])==count
    assert sum(c["quantity"] for c in view["cards"])==len(pieces)
    details=[p for c in view["cards"] for p in c["pieces"]]
    assert sorted(p["piece_id"] for p in details)==sorted(p["piece_id"] for p in pieces)
    assert view["total_halalas"]==sum((l["product_unit_price_halalas"]+100) for l in lines)
    assert sum(c["total_halalas"] for c in view["cards"])==view["total_halalas"]
    for p in details:
        index=int(p["piece_id"].split("-")[1])
        assert p["source"]==pieces[index]
        assert p["services"][0]["service_id"]==lines[index]["services"][0]["service_id"]
    assert (lines,pieces)==before
    # Display is independently mutable; it must not alias a persistence line.
    details[0]["source"]["product_options"]["new"]="display only"
    assert (lines,pieces)==before


def test_mixed_variant_financial_line_uses_each_original_piece():
    lines,pieces=fixture(variants=["v1","v2"])
    lines=[{**lines[0],"variant_id":"v1","piece_ids":["piece-0","piece-1"]}]
    view=project_supplier_invoice_display(lines,pieces)
    assert {c["variant_id"] for c in view["cards"]}=={"v1","v2"}
    assert len(lines)==1 and lines[0]["variant_id"]=="v1"


def test_equal_effective_cost_different_base_and_service_cost():
    lines,pieces=fixture()
    lines[1]["product_unit_price_halalas"]-=200
    lines[1]["services"][0]["unit_price_halalas"]+=200
    assert len(project_supplier_invoice_display(lines,pieces)["cards"])==1


def test_retail_price_does_not_affect_key():
    lines,pieces=fixture()
    pieces[0]["selling_price"]=100;pieces[1]["selling_price"]=1000
    assert len(project_supplier_invoice_display(lines,pieces)["cards"])==1


def test_fractional_service_rounds_at_original_line_boundary_only():
    lines,pieces=fixture(variants=["v1","v2"])
    lines=[{**lines[0],"piece_ids":["piece-0","piece-1"],
      "product_unit_price_halalas":0,"services":[{"service_id":"half",
      "quantity_per_piece":0.5,"unit_price_halalas":1}]}]
    view=project_supplier_invoice_display(lines,pieces)
    assert view["total_halalas"]==1
    assert sum(c["total_halalas"] for c in view["cards"])==1
    assert all(c["effective_cost"]=={"numerator":1,"denominator":2} for c in view["cards"])
    assert all("rounding_adjustment" in p for c in view["cards"] for p in c["pieces"])


@pytest.mark.parametrize("defect",["missing","duplicate","extra","total"])
def test_unproven_identity_or_total_is_rejected_without_mutation(defect):
    lines,pieces=fixture()
    if defect=="missing":pieces.pop()
    if defect=="duplicate":lines[1]["piece_ids"]=["piece-0"]
    if defect=="extra":pieces.append({**pieces[0],"piece_id":"extra"})
    before=deepcopy((lines,pieces))
    with pytest.raises(ValueError):
        project_supplier_invoice_display(lines,pieces,expected_total_halalas=1 if defect=="total" else None)
    assert (lines,pieces)==before
