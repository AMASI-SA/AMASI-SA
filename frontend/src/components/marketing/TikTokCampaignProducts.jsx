import { useEffect, useRef, useState } from "react";
import api from "../../lib/api";

export default function TikTokCampaignProducts({ accountId, campaignId }) {
    const [query, setQuery] = useState("");
    const [items, setItems] = useState([]);
    const [preview, setPreview] = useState(null);
    const [message, setMessage] = useState("");
    const [busy, setBusy] = useState(false);
    const active = useRef(true);
    const lock = useRef(false);
    useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
    const run = async (operation) => {
        if (lock.current) return;
        lock.current = true; setBusy(true); setMessage("");
        try { await operation(); }
        catch (error) { if (active.current) setMessage(error?.response?.data?.detail?.message || "تعذر إكمال الطلب. أعد المحاولة؛ لا يلزم تكرار الحفظ إن نجح."); }
        finally { lock.current = false; if (active.current) setBusy(false); }
    };
    const search = () => run(async () => {
        setPreview(null);
        const escaped = query.trim().replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        const { data } = await api.get("/products-v2", { params: { q: escaped, per_page: 10 } });
        if (active.current) { setItems(data.items || []); if (!data.items?.length) setMessage("لم يُعثر على منتج مطابق في كتالوجك."); }
    });
    const choose = (product) => run(async () => {
        const { data } = await api.post("/integrations-v2/tiktok_ads/campaign-product", {
            account_id: accountId, campaign_id: campaignId, product_id: String(product.salla_product_id),
        });
        if (active.current) setPreview({ ...data, requestKey: crypto.randomUUID() });
    });
    const save = () => run(async () => {
        const { data } = await api.post("/integrations-v2/tiktok_ads/campaign-product", {
            account_id: accountId, campaign_id: campaignId, product_id: preview.product.id,
            confirm: true, idempotency_key: preview.requestKey,
        });
        if (active.current && data.status === "linked") {
            setPreview(null);
            setMessage(`تم ربط ${data.product.name}. يمكنك إضافة منتج آخر. التحليل المعروض سابق للربط؛ أعد التحليل عند رغبتك.`);
        }
    });
    return <section className="rounded-xl border bg-white p-3 space-y-2" aria-label="منتجات الحملة">
        <h4 className="font-bold">منتجات الحملة</h4>
        <p className="text-sm">اختر المنتج الذي تعلن عنه هذه الحملة. الربط لا يغيّر الإعلان أو يثبت إسناد المبيعات.</p>
        <div className="flex gap-2">
            <input aria-label="اسم المنتج" value={query} maxLength={80} disabled={busy}
                onChange={(event) => setQuery(event.target.value)} className="border rounded p-2" placeholder="ابحث باسم المنتج" />
            <button type="button" disabled={busy || !query.trim()} onClick={search}>بحث المنتجات</button>
        </div>
        <ul>{items.filter((p) => p.salla_product_id).map((product) => <li key={product.salla_product_id}>
            <button type="button" disabled={busy} onClick={() => choose(product)}>{product.name}</button>
        </li>)}</ul>
        {preview && <div className="rounded border p-2">
            <p>{preview.product.name}</p>
            <a href={preview.product.url} target="_blank" rel="noopener noreferrer">عرض المنتج</a>
            <button type="button" disabled={busy} onClick={save} className="mr-3 font-bold">تأكيد ربط المنتج</button>
        </div>}
        {message && <p role="status">{message}</p>}
    </section>;
}
