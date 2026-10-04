import { buildDisplayRequest, createLatestDisplayLoader, validateDisplayResponse } from "./supplierInvoiceDisplayTransport";

test("display request preserves persistence lines and original variant/service details", () => {
    const lines = [{ key: "financial", piece_ids: ["a", "b"], total_halalas: 10000, services: [{service_id:"s1",selected:true},{service_id:"s2",selected:false}] }];
    const pieces = [{piece_id:"a",variant_id:"v1",order_item_id:"i1"},{piece_id:"b",variant_id:"v2",order_item_id:"i2"}];
    const before = JSON.stringify({lines,pieces});
    const request = buildDisplayRequest(lines,pieces);
    expect(request.lines[0].source_key).toBe("financial");
    expect(request.lines[0].services).toEqual([lines[0].services[0]]);
    expect(request.pieces).toEqual(pieces);
    expect(request.expected_total_halalas).toBe(10000);
    expect(JSON.stringify({lines,pieces})).toBe(before);
});

const fixtures = JSON.parse(require("fs").readFileSync(require("path").resolve(__dirname,
    "../../../../backend/tests/fixtures/supplier_invoice_display_cases.json"), "utf8")).cases;
const requestFor = fixture => ({lines:fixture.lines,pieces:fixture.pieces,expected_total_halalas:fixture.display.total_halalas});

test("late display response cannot replace the current projection", async () => {
    const pending = [];
    const publish = jest.fn();
    const loader = createLatestDisplayLoader(() => new Promise(resolve => pending.push(resolve)), publish);
    const oldFixture = fixtures.find(row=>row.case==="A");
    const newFixture = fixtures.find(row=>row.case==="B");
    const first = loader.load(requestFor(oldFixture));
    const second = loader.load(requestFor(newFixture));
    pending[1]({ok:true,display:newFixture.display});
    await second;
    pending[0]({ok:true,display:oldFixture.display});
    await first;
    expect(publish.mock.calls.filter(([state])=>state.display).map(([state])=>state.display)).toEqual([newFixture.display]);
});

test.each(fixtures.map(fixture=>[fixture.case,fixture]))("canonical response %s passes integrity validation",(_,fixture)=>{
    expect(validateDisplayResponse(requestFor(fixture),{ok:true,display:fixture.display})).toBe(fixture.display);
});

test.each([
    ["not successful",result=>{result.ok=false;}],
    ["total changed",result=>{result.display.total_halalas++;}],
    ["duplicate piece",result=>{result.display.cards[0].pieces[1]=result.display.cards[0].pieces[0];}],
    ["piece missing",result=>{result.display.cards[0].pieces.pop();}],
    ["wrong source",result=>{result.display.cards[0].pieces[0].source_key="other";}],
    ["lost options",result=>{delete result.display.cards[0].pieces[0].source.product_options;}],
    ["service switched",result=>{result.display.cards[0].pieces[0].services[0].service_id="wrap";}],
    ["card cost changed",result=>{result.display.cards[0].effective_cost.numerator++;}],
    ["wrong variant",result=>{result.display.cards[0].variant_id="other";}],
    ["card total changed",result=>{result.display.cards[0].total_halalas++;}],
])("rejects %s without showing unverified display",async(_,mutate)=>{
    const fixture=fixtures.find(row=>row.case==="B");
    const result=JSON.parse(JSON.stringify({ok:true,display:fixture.display}));
    mutate(result);
    const publish=jest.fn();
    const loader=createLatestDisplayLoader(async()=>result,publish);
    await loader.load(requestFor(fixture));
    expect(publish).toHaveBeenLastCalledWith({loading:false,display:null,error:"supplier_display_response_integrity_failed"});
});

test("display errors remain visible, with no financial-line fallback", async () => {
    const publish = jest.fn();
    const loader = createLatestDisplayLoader(async()=>{throw new Error("projection failed");},publish);
    await loader.load({});
    expect(publish).toHaveBeenLastCalledWith({loading:false,display:null,error:"projection failed"});
});
