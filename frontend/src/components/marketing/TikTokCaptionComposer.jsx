import { useEffect, useRef, useState } from "react";
import api from "../../lib/api";

const BUTTON = "rounded-xl border bg-white px-4 py-3 text-sm font-bold disabled:opacity-40";
export default function TikTokCaptionComposer({ disabled = false, onApply, onBusyChange }) {
    const [query,setQuery] = useState(""), [items,setItems] = useState([]), [product,setProduct] = useState(null), [brief,setBrief] = useState("");
    const [busy,setBusy] = useState(false), [error,setError] = useState(""), [result,setResult] = useState(null);
    const active = useRef(true), lock = useRef(false), requestKey = useRef(null), abort = useRef(null);
    useEffect(() => { active.current = true; return () => { active.current=false;abort.current?.abort(); }; }, []);
    useEffect(() => { onBusyChange?.(busy); }, [busy,onBusyChange]);
    function changed() { requestKey.current=null;setResult(null);setError(""); }
    async function work(action) {
        if (lock.current || disabled) return;
        lock.current=true;setBusy(true);setError("");abort.current=new AbortController();
        try { await action({ signal:abort.current.signal,timeout:60000 }); }
        catch (failure) { if(active.current)setError(failure?.response?.data?.detail?.message || "تعذر تأكيد اقتراح الوصف؛ راجع نفس الطلب قبل توليد نص آخر."); }
        finally { lock.current=false;if(active.current)setBusy(false); }
    }
    function generate(fresh=false) {
        if (!product) return;
        if (fresh) requestKey.current=null;
        requestKey.current ||= crypto.randomUUID().replaceAll("-","");
        work(async (options) => { const { data }=await api.post("/integrations-v2/tiktok/content/captions/proposals",{ product_id:String(product.salla_product_id),brief,idempotency_key:requestKey.current },options);if(active.current)setResult(data); });
    }
    return <section data-testid="tiktok-caption-composer" className="space-y-3 rounded-xl border border-violet-200 bg-violet-50 p-4">
        <h3 className="font-black">وصف يجذب المشاهدين بالذكاء</h3><p className="text-sm leading-6">اختر منتج الفيديو لنجهز افتتاحية ووصفًا وهاشتاقات مناسبة للجمهور السعودي، بنسخة عضوية وأخرى مناسبة للترويج. راجع تطابق النص مع الفيديو؛ التفاعل والمشاهدات غير مضمونين.</p>
        <fieldset disabled={disabled||busy} className="space-y-3">
            <label className="block text-sm font-bold">منتج الفيديو<input className="mt-1 w-full rounded-xl border p-3" aria-label="البحث عن منتج فيديو TikTok" value={query} maxLength={80} onChange={(e)=>setQuery(e.target.value)} /></label>
            <button type="button" className={BUTTON} disabled={!query.trim()} onClick={()=>work(async(options)=>{const escaped=query.trim().replace(/[.*+?^${}()|[\]\\]/g,"\\$&");const {data}=await api.get("/products-v2",{...options,params:{q:escaped,per_page:10}});if(active.current)setItems(data.items||[]);})}>بحث منتج الفيديو</button>
            {items.filter((p)=>p.salla_product_id).map((p)=><button type="button" className={`${BUTTON} block w-full text-right`} key={p.salla_product_id} onClick={()=>{changed();setProduct(p);setItems([]);}}>{p.name}</button>)}
            {product&&<p className="text-sm font-bold">المنتج المختار: {product.name}</p>}
            <label className="block text-sm font-bold">فكرة الفيديو أو المناسبة · اختياري<textarea aria-label="فكرة فيديو TikTok" className="mt-1 w-full rounded-xl border p-3" maxLength={600} value={brief} onChange={(e)=>{changed();setBrief(e.target.value);}} /></label>
            <button type="button" className={BUTTON} disabled={!product} onClick={()=>generate()}>تجهيز وصف تسويقي للسعودية</button>
        </fieldset>
        {busy&&<p role="status">جارٍ تجهيز الاقتراح…</p>}{error&&<p role="alert" className="text-sm text-rose-800">{error}</p>}
        {result&&<div className="space-y-3">{["organic","promoted"].map((kind)=><article key={kind} className="space-y-2 rounded-xl border bg-white p-3"><h4 className="font-bold">{kind==="organic"?"للنشر العضوي والتفاعل":"مناسب للترويج أيضًا"}</h4><p className="whitespace-pre-wrap text-sm leading-7">{result.copy[kind].caption}</p><button type="button" className={BUTTON} disabled={disabled||busy} onClick={()=>onApply(result.copy[kind].caption)}>استخدام هذا الوصف</button></article>)}<p className="text-sm leading-6">اقتراح لبداية الفيديو: {result.copy.opening_tip}</p><button type="button" className={BUTTON} disabled={disabled||busy} onClick={()=>generate(true)}>اقتراح جديد</button></div>}
    </section>;
}
