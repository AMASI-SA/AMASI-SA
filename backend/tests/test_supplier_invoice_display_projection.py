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


def test_card_identity_survives_real_financial_line_consolidation():
    from datetime import datetime, timezone
    from supplier_receiving_routes import build_supplier_receiving_invoice, SupplierReceivingInvoiceLineRequest
    from test_supplier_receiving import _build37_group_scan
    import json
    from pathlib import Path
    cases=json.loads((Path(__file__).parent/"fixtures/supplier_invoice_display_cases.json").read_text(encoding="utf-8"))["cases"]
    for case in cases:
        scans=[]
        for piece,line in zip(case["pieces"],case["lines"]):
            service=line["services"][0]
            scans.append({**_build37_group_scan(piece["piece_id"],
                product_price=line["product_unit_price_halalas"],service_id=service["service_id"],
                service_price=service["unit_price_halalas"]), **piece})
        request=[SupplierReceivingInvoiceLineRequest(piece_ids=l["piece_ids"],
            product_unit_price_halalas=l["product_unit_price_halalas"],services=[{
            "service_id":x["service_id"],"unit_price_halalas":x["unit_price_halalas"]} for x in l["services"]]) for l in case["lines"]]
        originals=deepcopy(request)
        financial=build_supplier_receiving_invoice(session={"reference":"synthetic","supplier_snapshot":{}},
            scans=scans,requested_lines=request,saved_at=datetime(2026,10,4,tzinfo=timezone.utc))
        before=deepcopy(financial)
        final=project_supplier_invoice_display(financial["lines"],case["pieces"],expected_total_halalas=financial["total_halalas"])
        def signature(view):
            return [(c["key"],c["quantity"],c["piece_ids"],c["total_halalas"],
              [(p["piece_id"],p["effective_cost"],[(v["service_id"],v["unit_price_halalas"],v["display_total_halalas"]) for v in p["services"]]) for p in c["pieces"]]) for c in view["cards"]]
        assert signature(final)==signature(case["display"]),case["case"]
        assert request==originals and financial==before


def test_expanded_detail_limit_is_bounded():
    lines,pieces=fixture(251)
    services=[{"service_id":str(i),"unit_price_halalas":1,"quantity_per_piece":1} for i in range(200)]
    line={"piece_ids":[p["piece_id"] for p in pieces],"product_unit_price_halalas":0,"services":services}
    with pytest.raises(ValueError,match="supplier_display_detail_limit"):
        project_supplier_invoice_display([line],pieces)
