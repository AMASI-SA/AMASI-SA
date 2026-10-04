import { buildDisplayRequest, createLatestDisplayLoader } from "./supplierInvoiceDisplay";

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

test("late display response cannot replace the current projection", async () => {
    const pending = [];
    const publish = jest.fn();
    const loader = createLatestDisplayLoader(() => new Promise(resolve => pending.push(resolve)), publish);
    const first = loader.load({lines:[1]});
    const second = loader.load({lines:[2]});
    pending[1]({display:{cards:["new"]}});
    await second;
    pending[0]({display:{cards:["old"]}});
    await first;
    expect(publish.mock.calls.filter(([state])=>state.display).map(([state])=>state.display.cards)).toEqual([["new"]]);
});

test("display errors remain visible, with no financial-line fallback", async () => {
    const publish = jest.fn();
    const loader = createLatestDisplayLoader(async()=>{throw new Error("projection failed");},publish);
    await loader.load({});
    expect(publish).toHaveBeenLastCalledWith({loading:false,display:null,error:"projection failed"});
});
