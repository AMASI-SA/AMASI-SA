import { useEffect, useMemo, useState } from "react";
import { projectSupplierInvoiceDisplay } from "../../services/supplierReceiving";
import { buildDisplayRequest, createLatestDisplayLoader } from "./supplierInvoiceDisplayTransport";

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
const optionFields = [
    ["product_options", "خيارات المنتج"],
    ["product_options_snapshot", "خيارات المنتج المسجلة"],
    ["options", "الخيارات"],
    ["options_raw", "الخيارات الأصلية"],
    ["options_normalized", "الخيارات المنظمة"],
    ["custom_fields", "حقول التخصيص"],
    ["specifications", "المواصفات"],
];

function CardChargeSummary({ card }) {
    const unitTotal = card.pieces[0]?.display_total_halalas;
    const sameUnitTotal = unitTotal != null && card.pieces.every(piece => piece.display_total_halalas === unitTotal);
    const servicesOnly = card.pieces.length > 0 && card.total_halalas > 0 && card.pieces.every(piece => piece.product_cost_halalas === 0);
    return <strong>
        الكمية {card.quantity}
        {sameUnitTotal && ` · إجمالي الوحدة شامل الخدمات ${money(unitTotal)} ر.س`}
        {servicesOnly && " · خدمة فقط"}
    </strong>;
}

export function SupplierDisplayCards({ projection }) {
    if (projection?.error) return <p role="alert" className="p-3 text-rose-800">{projection.error}</p>;
    if (!projection?.display) return <p role="status" className="p-3 text-slate-500">جارٍ تجهيز عرض الفاتورة…</p>;
    return <div data-testid="supplier-display-cards" className="space-y-3">
        {projection.display.cards.map(card=><article key={card.key} data-testid="supplier-display-card" className="rounded-xl border border-slate-200 bg-white p-3">
            <header className="flex justify-between gap-3">
                {card.selected_image_url && <img src={card.selected_image_url} alt="" className="h-12 w-12 rounded-xl object-cover" />}
                <div><strong>{card.product_name}</strong><p className="text-xs text-slate-500">{card.sku} {card.variant_id}</p></div>
                <CardChargeSummary card={card} />
            </header>
            <details className="mt-2 text-xs"><summary className="cursor-pointer">تفاصيل القطع والخدمات ({card.quantity})</summary>
                {card.pieces.map(piece=><section key={piece.piece_id} data-piece-id={piece.piece_id} className="mt-2 border-t p-2">
                    <p>القطعة: {piece.piece_id} · الطلب: {piece.source?.order_number || "—"} · بند الطلب: {piece.source?.order_item_id || "—"}</p>
                    <p>Variant: {piece.source?.variant_id || piece.source?.salla_variant_id || "—"}</p>
                    {piece.source?.options_snapshot_available === false && <p role="note">تفاصيل الخيارات الأصلية غير متاحة في السجل.</p>}
                    {optionFields.filter(([field]) => Object.prototype.hasOwnProperty.call(piece.source || {}, field)).map(([field,label]) => (
                        <div key={field} className="mt-1" data-option-field={field}>
                            <strong>{label}</strong>
                            <pre className="whitespace-pre-wrap">{JSON.stringify(piece.source[field],null,2)}</pre>
                        </div>
                    ))}
                    <p>سعر المنتج {money(piece.product_cost_halalas)} ر.س{piece.product_cost_halalas === 0 && piece.display_total_halalas > 0 && " · خدمة فقط"}</p>
                    {piece.services.map((service,index)=><p key={`${service.service_id}:${index}`}>{service.service_name || service.service_id}: {service.quantity_per_piece ?? 1} × {money(service.unit_price_halalas)} ر.س · المحتسب {money(service.display_total_halalas)} ر.س</p>)}
                    <p>إجمالي القطعة شامل الخدمات {money(piece.display_total_halalas)} ر.س</p>
                </section>)}
            </details>
            <footer className="mt-2 font-bold text-emerald-800">الإجمالي: {money(card.total_halalas)} ر.س</footer>
        </article>)}
    </div>;
}
