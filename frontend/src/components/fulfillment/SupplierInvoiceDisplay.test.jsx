import { renderToStaticMarkup } from "react-dom/server";
import { SupplierDisplayCards } from "./SupplierInvoiceDisplay";

const piece = (id, service) => ({piece_id:id,source_key:"financial-source",source:{piece_id:id,order_item_id:`item-${id}`,variant_id:"v1",options:{name:id}},product_cost_halalas:4000,display_total_halalas:5000,services:[{display_total_halalas:1000,service_id:service,service_name:service,quantity_per_piece:1,unit_price_halalas:1000}],effective_cost:{numerator:5000,denominator:1}});
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

test("a 15-riyal product and a later 15-riyal embroidery invoice identify the charge on each receipt", () => {
    for (const serviceOnly of [false, true]) {
        const productCost = serviceOnly ? 0 : 1500;
        const services = serviceOnly ? [{service_id: "embroidery", service_name: "تطريز الاسم", unit_price_halalas: 1500, display_total_halalas: 1500}] : [];
        const detail = {...piece("a", "embroidery"), product_cost_halalas: productCost, display_total_halalas: 1500, services};
        const card = {...display.cards[0], quantity: 1, pieces: [detail], total_halalas: 1500, effective_cost: {numerator: 1500, denominator: 1}};
        const container = document.createElement("div");
        container.innerHTML = renderToStaticMarkup(<SupplierDisplayCards projection={{display: {cards: [card]}}} />);
        const header = container.querySelector("header").textContent;
        const details = container.querySelector('[data-piece-id="a"]').textContent;
        expect(header.includes("خدمة فقط")).toBe(serviceOnly);
        expect(header).toContain("إجمالي الوحدة شامل الخدمات 15.00 ر.س");
        expect(details).toContain(`سعر المنتج ${serviceOnly ? "0.00" : "15.00"} ر.س`);
        expect(details).toContain("إجمالي القطعة شامل الخدمات 15.00 ر.س");
        expect(details.includes("تطريز الاسم")).toBe(serviceOnly);
    }
});

test("a mixed card preserves each allocated halala without claiming a common rounded price", () => {
    const services = [{service_id: "first", unit_price_halalas: 1, quantity_per_piece: 0.5}];
    const pieces = [
        {...piece("a"), product_cost_halalas: 1, services: [{...services[0], display_total_halalas: 1}], display_total_halalas: 2},
        {...piece("b"), product_cost_halalas: 1, services: [{...services[0], display_total_halalas: 0}], display_total_halalas: 1},
        {...piece("c"), product_cost_halalas: 0, services: [{service_id: "other", unit_price_halalas: 3, quantity_per_piece: 0.5, display_total_halalas: 2}], display_total_halalas: 2},
    ];
    const card = {...display.cards[0], pieces, quantity: 3, total_halalas: 5, effective_cost: {numerator: 3, denominator: 2}};
    const container = document.createElement("div");
    container.innerHTML = renderToStaticMarkup(<SupplierDisplayCards projection={{display: {cards: [card]}}} />);
    expect(container.querySelector("header").textContent).not.toMatch(/خدمة فقط|إجمالي الوحدة/);
    expect(container.querySelector('[data-piece-id="a"]').textContent).toContain("إجمالي القطعة شامل الخدمات 0.02 ر.س");
    expect(container.querySelector('[data-piece-id="b"]').textContent).toContain("إجمالي القطعة شامل الخدمات 0.01 ر.س");
    expect(container.querySelector('[data-piece-id="c"]').textContent).toContain("سعر المنتج 0.00 ر.س · خدمة فقط");
    expect(container.querySelector("footer").textContent).toContain("0.05 ر.س");
});
