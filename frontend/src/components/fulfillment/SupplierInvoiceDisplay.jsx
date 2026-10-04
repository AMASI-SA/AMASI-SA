import { useEffect, useMemo, useState } from "react";
import { projectSupplierInvoiceDisplay } from "../../services/supplierReceiving";
import { buildDisplayRequest, createLatestDisplayLoader } from "./supplierInvoiceDisplay";

export function useSupplierInvoiceDisplay(lines, pieces) {
    const payload = useMemo(()=>buildDisplayRequest(lines,pieces),[lines,pieces]);
    const key = JSON.stringify(payload);
    const [state,setState] = useState({key:"",loading:false,display:null,error:""});
    useEffect(()=>{
        if (!payload.lines.length) {
            setState({key,loading:false,display:{cards:[],piece_ids:[],total_halalas:0},error:""});
            return undefined;
        }
        const loader = createLatestDisplayLoader(projectSupplierInvoiceDisplay, next=>setState({...next,key}));
        void loader.load(payload);
        return ()=>loader.cancel();
    },[payload,key]);
    return state.key === key ? state : {loading:true,display:null,error:""};
}

const money = value => (Number(value || 0)/100).toFixed(2);
export function SupplierDisplayCards({ projection }) {
    if (projection?.error) return <p role="alert" className="p-3 text-rose-800">{projection.error}</p>;
    if (!projection?.display) return <p role="status" className="p-3 text-slate-500">جارٍ تجهيز عرض الفاتورة…</p>;
    return <div data-testid="supplier-display-cards" className="space-y-3">
        {projection.display.cards.map(card=><article key={card.key} data-testid="supplier-display-card" className="rounded-xl border border-slate-200 bg-white p-3">
            <header className="flex justify-between gap-3">{card.selected_image_url && <img src={card.selected_image_url} alt="" className="h-12 w-12 rounded-xl object-cover" />}<div><strong>{card.product_name}</strong><p className="text-xs text-slate-500">{card.sku} {card.variant_id}</p></div><strong>تكلفة الوحدة: {card.quantity} × {money(Number(card.effective_cost.numerator)/Number(card.effective_cost.denominator))} ر.س</strong></header>
            <details className="mt-2 text-xs"><summary className="cursor-pointer">تفاصيل القطع والخدمات ({card.quantity})</summary>
                {card.pieces.map(piece=><section key={piece.piece_id} data-piece-id={piece.piece_id} className="mt-2 border-t p-2">
                    <p>القطعة: {piece.piece_id} · الطلب: {piece.source?.order_number || "—"} · بند الطلب: {piece.source?.order_item_id || "—"}</p>
                    <p>Variant: {piece.source?.variant_id || piece.source?.salla_variant_id || "—"}</p>
                    {(piece.source?.product_options || piece.source?.options || piece.source?.specifications) && <pre className="whitespace-pre-wrap">{JSON.stringify(piece.source.product_options || piece.source.options || piece.source.specifications,null,2)}</pre>}
                    {piece.services.map((service,index)=><p key={`${service.service_id}:${index}`}>{service.service_name || service.service_id}: {service.quantity_per_piece} × {money(service.unit_price_halalas)} ر.س</p>)}
                    <p>تكلفة القطعة: {money(Number(piece.effective_cost.numerator)/Number(piece.effective_cost.denominator))} ر.س</p>
                </section>)}
            </details>
            <footer className="mt-2 font-bold text-emerald-800">الإجمالي: {money(card.total_halalas)} ر.س</footer>
        </article>)}
    </div>;
}
