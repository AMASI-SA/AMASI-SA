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


test.each([
    "product_options", "product_options_snapshot", "options", "options_raw",
    "options_normalized", "custom_fields", "specifications",
])("piece details render original option field %s", field => {
    const fixture = JSON.parse(JSON.stringify(display));
    fixture.cards[0].pieces[0].source = {piece_id:"a",[field]:{text:`value-${field}`}};
    const markup = renderToStaticMarkup(<SupplierDisplayCards projection={{display:fixture}} />);
    expect(markup).toContain(`value-${field}`);
});

test("coexisting option forms remain separately available under their own piece",()=>{
    const fixture = JSON.parse(JSON.stringify(display));
    const source = fixture.cards[0].pieces[0].source;
    Object.assign(source, {product_options:{text:"selected-red"},product_options_snapshot:{text:"snapshot-blue"},
        options_raw:[{text:"raw-green"}],options_normalized:{text:"normalized-white"},custom_fields:{text:"custom-black"}});
    const before = JSON.stringify(fixture);
    const container = document.createElement("div");
    container.innerHTML = renderToStaticMarkup(<SupplierDisplayCards projection={{display:fixture}} />);
    const first = container.querySelector('[data-piece-id="a"]');
    const second = container.querySelector('[data-piece-id="b"]');
    for (const value of ["selected-red","snapshot-blue","raw-green","normalized-white","custom-black"]) {
        expect(first.textContent).toContain(value);
        expect(second.textContent).not.toContain(value);
    }
    expect(JSON.stringify(fixture)).toBe(before);
});
