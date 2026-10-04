import { renderToStaticMarkup } from "react-dom/server";
import { SupplierDisplayCards } from "./SupplierInvoiceDisplay";

const piece = (id, service) => ({piece_id:id,source_key:"financial-source",source:{piece_id:id,order_item_id:`item-${id}`,variant_id:"v1",options:{name:id}},services:[{service_id:service,service_name:service,quantity_per_piece:1,unit_price_halalas:1000}],effective_cost:{numerator:5000,denominator:1}});
const display = {cards:[{key:"same-product-cost",product_id:"p",sku:"sku",variant_id:"v1",product_name:"Product",quantity:2,piece_ids:["a","b"],total_halalas:10000,effective_cost:{numerator:5000,denominator:1},pieces:[piece("a","engraving"),piece("b","wrapping")]}],piece_ids:["a","b"],total_halalas:10000};

test("all presentation stages render the canonical cards without merging source services",()=>{
    const before = JSON.stringify(display);
    const stages = ["Draft","Services","Review","Final/PDF projection"];
    const rendered = stages.map(()=>renderToStaticMarkup(<SupplierDisplayCards projection={{display}} />));
    expect(new Set(rendered).size).toBe(1);
    expect((rendered[0].match(/data-testid="supplier-display-card"/g)||[]).length).toBe(1);
    expect(rendered[0]).toContain('data-piece-id="a"');
    expect(rendered[0]).toContain('data-piece-id="b"');
    expect(rendered[0]).toContain("item-a");
    expect(rendered[0]).toContain("item-b");
    expect(rendered[0]).toContain("engraving");
    expect(rendered[0]).toContain("wrapping");
    expect(JSON.stringify(display)).toBe(before);
});

test("missing projection never silently renders persisted grouping",()=>{
    const markup = renderToStaticMarkup(<SupplierDisplayCards projection={{loading:true,display:null}} />);
    expect(markup).toContain('role="status"');
    expect(markup).not.toContain('data-testid="supplier-display-card"');
});
