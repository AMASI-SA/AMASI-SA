// Presentation requests never replace the detailed lines used by save/close.
export function buildDisplayRequest(lines, pieces) {
    return {
        lines: lines.map(line => ({...line, source_key: line.key, services: line.services.filter(service => service.selected)})),
        pieces,
        expected_total_halalas: lines.reduce((sum,line)=>sum + Number(line.total_halalas || 0),0),
    };
}


const canonical = value => JSON.stringify(value, (_, item) =>
    item && typeof item === "object" && !Array.isArray(item)
        ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);

export function validateDisplayResponse(payload, result) {
    const fail = () => { throw new Error("supplier_display_response_integrity_failed"); };
    const display = result?.display;
    if (result?.ok !== true || !Array.isArray(display?.cards) || !Array.isArray(display?.piece_ids)) fail();
    if (display.total_halalas !== payload.expected_total_halalas) fail();
    const expected = payload.lines.flatMap(line => line.piece_ids);
    const unique = ids => Array.isArray(ids) && ids.every(id => typeof id === "string" && id) && new Set(ids).size === ids.length;
    const sameIds = (a,b) => unique(a) && unique(b) && canonical([...a].sort()) === canonical([...b].sort());
    if (!sameIds(expected, display.piece_ids)) fail();
    const sources = new Map(payload.pieces.map(piece => [piece.piece_id, piece]));
    if (sources.size !== payload.pieces.length || !sameIds(expected,[...sources.keys()])) fail();
    const validCost = cost => Number.isSafeInteger(cost?.numerator) && cost.numerator >= 0
        && Number.isSafeInteger(cost?.denominator) && cost.denominator > 0;
    const seen = [];
    let total = 0;
    const cardKeys = new Set();
    for (const card of display.cards) {
        if (!card.key || cardKeys.has(card.key) || !Array.isArray(card.pieces)
            || card.quantity !== card.pieces.length || !Number.isSafeInteger(card.total_halalas)) fail();
        cardKeys.add(card.key);
        if (!sameIds(card.piece_ids, card.pieces.map(piece => piece.piece_id))) fail();
        let pieceTotal = 0;
        for (const piece of card.pieces) {
            const source = sources.get(piece.piece_id);
            const line = payload.lines[piece.source_line_index];
            if (!validCost(card.effective_cost) || !validCost(piece.effective_cost)
                || BigInt(card.effective_cost.numerator) * BigInt(piece.effective_cost.denominator)
                    !== BigInt(piece.effective_cost.numerator) * BigInt(card.effective_cost.denominator)) fail();
            if (source && (card.product_id !== String(source.product_id)
                || card.sku !== String(source.sku || "")
                || (card.variant_id || "") !== String(source.variant_id || source.salla_variant_id || ""))) fail();
            if (!source || !line || line.piece_ids[piece.source_piece_index] !== piece.piece_id
                || piece.source_key !== line.source_key || canonical(piece.source) !== canonical(source)
                || !Array.isArray(piece.services) || !Number.isSafeInteger(piece.display_total_halalas)) fail();
            if (piece.services.length !== line.services.length) fail();
            line.services.forEach((service,index) => {
                for (const key of Object.keys(service)) {
                    if (canonical(piece.services[index][key]) !== canonical(service[key])) fail();
                }
            });
            seen.push(piece.piece_id);
            pieceTotal += piece.display_total_halalas;
        }
        if (pieceTotal !== card.total_halalas) fail();
        total += card.total_halalas;
    }
    if (!sameIds(expected,seen) || total !== display.total_halalas) fail();
    return display;
}

export function createLatestDisplayLoader(request, publish) {
    let generation = 0;
    return {
        async load(payload) {
            const current = ++generation;
            publish({loading:true,display:null,error:""});
            try {
                const result = await request(payload);
                if (current === generation) {
                    const display = validateDisplayResponse(payload, result);
                    publish({loading:false,display,error:""});
                }
            } catch(error) {
                if (current === generation) publish({loading:false,display:null,error:error.message || "تعذر تجميع عرض الفاتورة"});
            }
        },
        cancel() { generation += 1; },
    };
}
