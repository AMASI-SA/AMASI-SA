// Presentation requests never replace the detailed lines used by save/close.
export function buildDisplayRequest(lines, pieces) {
    return {
        lines: lines.map(line => ({...line, source_key: line.key, services: line.services.filter(service => service.selected)})),
        pieces,
        expected_total_halalas: lines.reduce((sum,line)=>sum + Number(line.total_halalas || 0),0),
    };
}

export function createLatestDisplayLoader(request, publish) {
    let generation = 0;
    return {
        async load(payload) {
            const current = ++generation;
            publish({loading:true,display:null,error:""});
            try {
                const result = await request(payload);
                if (current === generation) publish({loading:false,display:result.display,error:""});
            } catch(error) {
                if (current === generation) publish({loading:false,display:null,error:error.message || "تعذر تجميع عرض الفاتورة"});
            }
        },
        cancel() { generation += 1; },
    };
}
