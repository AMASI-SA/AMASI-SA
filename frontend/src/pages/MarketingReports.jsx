import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ArrowsClockwise, ArrowSquareOut, DownloadSimple, Package, X } from "@phosphor-icons/react";
import api from "../lib/api";
import { buildProfitabilityProductCostHref } from "../campaignProfitabilityProductNavigation";
import { snapchatSyncRangeAllowed } from "./SnapchatV2Page";
import "./MarketingReports.css";

const LABELS = { all: "جميع المنصات", snapchat: "سناب شات", tiktok: "تيك توك", meta: "ميتا", google: "إعلانات Google", direct: "طلبات مباشرة", unclassified: "غير مصنّفة لمنصة" };
const ORDER_SOURCES = new Set(["direct", "unclassified"]);
const COLUMNS = [["orders", "عدد الطلبات"], ["cpa_sar", "تكلفة النتيجة"], ["roas", "ROAS الشراء"],
    ["spend_sar", "المبلغ المصروف"], ["sales_sar", "إجمالي المبيعات"], ["product_cost_sar", "تكلفة المنتجات"],
    ["shipping_cost_sar", "تكلفة الشحن"], ["payment_fees_sar", "رسوم الدفع"], ["net_profit_sar", "صافي الأرباح"], ["net_margin_pct", "هامش الربح"]];
export const formatReportNumber = (value, digits = 2) => value == null || !Number.isFinite(Number(value)) ? "—" : Number(value).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
const moneyKeys = new Set(COLUMNS.filter(([key]) => !["orders", "roas", "net_margin_pct"].includes(key)).map(([key]) => key));

function Metric({ value, field }) {
    const color = field === "net_profit_sar" && value != null ? value < 0 ? "loss" : "gain" : "";
    return <div className={`report-metric ${color}`}><b dir="ltr">{formatReportNumber(value, field === "orders" ? 0 : 2)}{value != null && field === "roas" ? "×" : value != null && field === "net_margin_pct" ? "%" : ""}</b>{value != null && moneyKeys.has(field) && <small>ر.س</small>}</div>;
}

function SourceSwitch({ source, onChange, provider }) {
    return <div className="report-source"><span>مصدر نتائج الطلبات</span><div role="group" aria-label="مصدر نتائج الطلبات">
        <button type="button" aria-pressed={source === "salla"} onClick={() => onChange("salla")}>عرض نتائج سلة</button>
        {!ORDER_SOURCES.has(provider) && <button type="button" aria-pressed={source === "platform"} onClick={() => onChange("platform")}>عرض نتائج {provider === "all" ? "المنصات" : LABELS[provider]}</button>}
    </div></div>;
}

export function ProductDialog({ row, source, provider, onSource, onClose }) {
    const ref = useRef(null);
    const previousFocus = useRef(document.activeElement);
    useEffect(() => {
        const original = document.body.style.overflow;
        document.body.style.overflow = "hidden";
        ref.current?.querySelector("button")?.focus();
        const keydown = event => {
            if (event.key === "Escape") onClose();
            if (event.key !== "Tab") return;
            const nodes = [...ref.current.querySelectorAll('button, a[href], input, select')].filter(el => !el.disabled);
            const first = nodes[0], last = nodes[nodes.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        };
        document.addEventListener("keydown", keydown);
        return () => { document.body.style.overflow = original; document.removeEventListener("keydown", keydown); previousFocus.current?.focus?.(); };
    }, [onClose]);
    const profit = row.profitability || {};
    const metrics = row.metrics || {};
    const products = profit.products || [];
    const cards = ["sales_sar", "product_cost_sar", "spend_sar", "shipping_cost_sar", "payment_fees_sar", "net_profit_sar", "net_margin_pct"].map(key => COLUMNS.find(([k]) => k === key));
    const productSales = products.reduce((sum, p) => sum + (p.sales_sar || 0), 0);
    const incompleteProducts = source === "salla" && metrics.sales_sar != null && Math.abs(productSales - metrics.sales_sar) > 0.02;
    return <div className="report-overlay" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
        <section className="report-dialog" role="dialog" aria-modal="true" aria-labelledby="report-product-title" ref={ref} dir="rtl" data-testid="report-products-dialog">
            <header><div><p>تفاصيل المنتجات والأرباح</p><h2 id="report-product-title">{row.name}</h2><small>{products.length} منتجات · {source === "salla" ? "طلبات سلة المطابقة" : "نتائج الحساب الإعلاني"}</small></div>
                <SourceSwitch source={source} onChange={onSource} provider={provider} /><button className="report-close" type="button" aria-label="إغلاق تفاصيل المنتجات" onClick={onClose}><X size={25} /></button></header>
            <div className="report-dialog-body"><div className="report-cards">{cards.map(([key, label]) => <div key={key} className={`report-card ${["shipping_cost_sar", "payment_fees_sar"].includes(key) ? "added-cost" : ""}`}><span>{label}</span><Metric field={key} value={metrics[key]} /></div>)}</div>
                <p className="report-explanation">{source === "salla" ? "صافي الأرباح بعد خصم تكلفة المنتجات والإعلان والشحن ورسوم الدفع. التكاليف الناقصة تظهر بشرطة حتى تكتمل." : "المنصة تعرض تحويلات وقيمة إجمالية ولا تحدد المنتجات لكل طلب. اختر نتائج سلة لعرض المنتجات وتكاليفها وأرباحها الفعلية."}</p>
                {incompleteProducts && <p className="report-alert">بعض مبيعات الحملة لا تتوفر لها تفاصيل منتجات؛ إجمالي الحملة يشملها ولا يساوي مجموع المنتجات المتاحة فقط.</p>}
                {source === "salla" && <div className="report-product-scroll"><table className="report-products"><thead><tr>{["المنتج", "الكمية", "إجمالي المبيعات", "تكلفة المنتجات", "الصرف الموزع", "تكلفة الشحن", "رسوم الدفع", "صافي الأرباح", "الهامش"].map(label => <th key={label}>{label}</th>)}</tr></thead>
                    <tbody>{products.map((p, index) => <tr key={`${p.identity}-${index}`}><td className="report-product"><div>{p.image_url ? <img src={p.image_url} alt="" loading="lazy" /> : <span className="product-placeholder"><Package size={30} /></span>}<div><strong>{p.name}</strong><small dir="ltr">{p.sku || p.salla_product_id}</small><a href={buildProfitabilityProductCostHref({ productId: p.mezan_product_id || p.salla_product_id, sku: p.sku, name: p.name })} target="_blank" rel="noopener noreferrer">فتح المنتج <ArrowSquareOut size={16} /></a></div></div></td>
                        <td><Metric field="orders" value={p.units} /></td>{[["sales_sar", p.sales_sar], ["product_cost_sar", p.product_cost_sar], ["spend_sar", p.allocated_ad_spend_sar], ["shipping_cost_sar", p.shipping_cost_sar], ["payment_fees_sar", p.payment_fees_sar], ["net_profit_sar", p.net_profit_sar], ["net_margin_pct", p.net_margin_pct]].map(([field, value]) => <td key={field}><Metric field={field} value={value} /></td>)}</tr>)}
                        {!products.length && <tr><td colSpan={9} className="report-empty">لا توجد منتجات في طلبات سلة المطابقة لهذه الحملة خلال الفترة.</td></tr>}</tbody>
                    <tfoot><tr><th>إجمالي الحملة</th><td><Metric field="orders" value={products.reduce((s,p) => s + (p.units || 0), 0)} /></td>{["sales_sar", "product_cost_sar", "spend_sar", "shipping_cost_sar", "payment_fees_sar", "net_profit_sar", "net_margin_pct"].map(key => <td key={key}><Metric field={key} value={metrics[key]} /></td>)}</tr></tfoot></table></div>}
                <small className="report-footnote">الصرف الموزع حسب حصة المنتج من المبيعات؛ الشحن ورسوم الدفع من الطلب المطابق. لا تشمل الأرباح المصاريف التشغيلية العامة.{profit.payment_fee_sources?.includes("merchant_settings") ? " رسوم الدفع محسوبة من إعدادات التاجر." : ""}</small>
            </div>
        </section>
    </div>;
}

function downloadCsv(rows, filename) {
    const cell = value => `"${String(value ?? "").replace(/^[=+@-]/, "'$&").replaceAll('"', '""')}"`;
    const csv = [["الاسم", ...COLUMNS.map(c => c[1])], ...rows.map(r => [r.name, ...COLUMNS.map(([k]) => r.metrics[k])])].map(r => r.map(cell).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob(["\uFEFF", csv], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a"); a.href = url; a.download = filename; a.click(); URL.revokeObjectURL(url);
}

export default function MarketingReports() {
    const [params, setParams] = useSearchParams();
    const provider = Object.hasOwn(LABELS, params.get("provider")) ? params.get("provider") : "snapchat";
    const source = ORDER_SOURCES.has(provider) || params.get("source") === "salla" ? "salla" : "platform";
    const orderSource = ORDER_SOURCES.has(provider);
    const statusScope = params.get("status_scope") === "report" ? "report" : "all";
    const accountId = params.get("account_id") || "";
    const from = params.get("from") || "", to = params.get("to") || "";
    const [data, setData] = useState(null), [busy, setBusy] = useState(true), [error, setError] = useState("");
    const [syncing, setSyncing] = useState(false), [notice, setNotice] = useState("");
    const [sallaSyncing, setSallaSyncing] = useState(false);
    const [query, setQuery] = useState(""), [activeOnly, setActiveOnly] = useState(false), [activityOnly, setActivityOnly] = useState(true);
    const [sort, setSort] = useState({ key: "spend_sar", asc: false }), [dialogId, setDialogId] = useState(null);
    const [draftFrom, setDraftFrom] = useState(from), [draftTo, setDraftTo] = useState(to);
    const request = useRef(0), controller = useRef(null), inFlight = useRef(false);
    const change = useCallback(values => setParams(old => { const next = new URLSearchParams(old); Object.entries(values).forEach(([k,v]) => v ? next.set(k,v) : next.delete(k)); return next; }), [setParams]);
    const load = useCallback(async ({ background = false } = {}) => {
        if (background && inFlight.current) return;
        const epoch = ++request.current;
        controller.current?.abort(); controller.current = new AbortController();
        inFlight.current = true;
        if (!background) { setBusy(true); setData(null); }
        setError("");
        try {
            const result = await api.get("/marketing-reports", { signal: controller.current.signal, params: { provider, source, status_scope: statusScope, account_id: accountId || undefined, date_from: from || undefined, date_to: to || undefined } });
            if (epoch !== request.current) return;
            setData(result.data);
            setDraftFrom(result.data.range.date_from); setDraftTo(result.data.range.date_to);
        } catch (e) { if (epoch === request.current && e.code !== "ERR_CANCELED") setError("تعذر تحديث التقرير. إن ظهرت نتائج فهي من آخر قراءة ناجحة؛ أعد المحاولة."); }
        finally { if (epoch === request.current) { inFlight.current = false; setBusy(false); } }
    }, [provider, source, statusScope, accountId, from, to]);
    useEffect(() => { load(); return () => { request.current++; controller.current?.abort(); inFlight.current = false; }; }, [load]);
    useEffect(() => {
        const tick = () => { if (document.visibilityState === "visible" && !syncing) load({ background: true }); };
        const timer = setInterval(tick, 60000);
        document.addEventListener("visibilitychange", tick);
        return () => { clearInterval(timer); document.removeEventListener("visibilitychange", tick); };
    }, [load, syncing]);
    const currentReport = data?.reports?.[0];
    const rows = useMemo(() => {
        if (!data) return [];
        return provider === "all" ? data.reports.map(p => ({ id: p.provider, provider: p.provider, name: p.label, status: p.status, metrics: p.totals, source_totals: p.source_totals })) : currentReport?.rows || [];
    }, [data, provider, currentReport]);
    const displayed = useMemo(() => rows.filter(r => (!query || `${r.name} ${r.campaign_id || ""}`.toLowerCase().includes(query.toLowerCase())) && (provider === "all" || !activeOnly || r.active) &&
        (provider === "all" || !activityOnly || r.active || r.metrics.spend_sar > 0 || r.metrics.orders > 0 || r.metrics.sales_sar > 0)).sort((a,b) => {
            const x = a.metrics[sort.key], y = b.metrics[sort.key];
            if (x == null || y == null) return x == null ? y == null ? 0 : 1 : -1;
            return (x-y) * (sort.asc ? 1 : -1);
        }), [rows, query, activeOnly, activityOnly, provider, sort]);
    const modalRow = rows.find(r => r.id === dialogId);
    const close = useCallback(() => setDialogId(null), []);
    async function sync() {
        const id = currentReport?.selected_account_id;
        if (!id || id === "all" || !data) return;
        const range = { dateFrom: data.range.date_from, dateTo: data.range.date_to };
        if (!snapchatSyncRangeAllowed(range)) { setNotice("المزامنة المباشرة متاحة لآخر 30 يومًا؛ الفترات الأقدم تُقرأ من البيانات المحفوظة."); return; }
        setSyncing(true); setNotice("");
        try {
            const result = await api.post("/integrations-v2/snapchat-v2/sync", { ad_account_id: id, date_from: range.dateFrom, date_to: range.dateTo, action_report_time: "conversion", run_type: "manual" });
            setNotice(result.data?.status === "complete" ? "اكتملت مزامنة سناب وأُعيدت قراءة طلبات سلة." : "المزامنة قيد التشغيل أو بعض تفاصيلها لم تكتمل؛ راجع وقت آخر مزامنة.");
            await load({ background: true });
        } catch { setNotice("تعذرت المزامنة المباشرة. يمكن إعادة تحديث القراءة من آخر بيانات محفوظة."); }
        finally { setSyncing(false); }
    }
    async function syncSalla() {
        if (!data || sallaSyncing) return;
        setSallaSyncing(true); setNotice("");
        try {
            const first = new Date(`${data.range.date_from}T00:00:00Z`); first.setUTCDate(first.getUTCDate() - 1);
            const last = new Date(`${data.range.date_to}T00:00:00Z`); last.setUTCDate(last.getUTCDate() + 1);
            const response = await api.post("/salla/sync/orders", { from_date: first.toISOString().slice(0,10), to_date: last.toISOString().slice(0,10) });
            setNotice(response.data?.already_running ? "مزامنة طلبات سلة قيد التشغيل؛ ستُقرأ النتائج تلقائيًا كل دقيقة." : "بدأت مزامنة طلبات سلة في الخلفية؛ ستُقرأ النتائج تلقائيًا كل دقيقة.");
            await load({background:true});
        } catch { setNotice("تعذر بدء مزامنة سلة. أعد المحاولة أو راجع اتصال سلة."); }
        finally { setSallaSyncing(false); }
    }
    return <main className="marketing-reports" dir="rtl" data-testid="marketing-reports-page">
        <header className="reports-heading"><div><p>التسويق</p><h1>التقارير</h1><small>{provider === "all" ? "إجماليات مصادر الطلبات والمنصات" : orderSource ? LABELS[provider] : `الحملات الإعلانية · ${LABELS[provider]}`}</small></div><SourceSwitch provider={provider} source={source} onChange={v => change({source:v})} /></header>
        <section className="reports-filters" aria-label="فلاتر التقارير"><label>المنصة<select value={provider} onChange={e => { close(); change({ provider:e.target.value, account_id:"", ...(ORDER_SOURCES.has(e.target.value) ? {source:"salla"} : {}) }); }}>{Object.entries(LABELS).map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select></label>
            {provider === "snapchat" && <label>الحساب الإعلاني<select value={accountId || currentReport?.selected_account_id || ""} onChange={e => { close(); change({account_id:e.target.value}); }}><option value="" disabled>اختر الحساب</option>{(currentReport?.accounts || []).map(a => <option key={a.id} value={a.id}>{a.name} · {a.currency} · {a.timezone}</option>)}<option value="all">كل حسابات سناب</option></select></label>}
            {source === "salla" && <label>حالات الطلبات<select value={statusScope} onChange={e => change({status_scope:e.target.value})}><option value="all">جميع الحالات</option><option value="report">حسب إعدادات التقارير</option></select></label>}
            <label>من<input type="date" value={draftFrom} onChange={e => setDraftFrom(e.target.value)} /></label><label>إلى<input type="date" value={draftTo} min={draftFrom} onChange={e => setDraftTo(e.target.value)} /></label>
            <button type="button" className="report-primary" disabled={!draftFrom || !draftTo || draftTo < draftFrom} onClick={() => change({from:draftFrom,to:draftTo})}>تطبيق الفترة</button>
            <button type="button" disabled={busy || syncing} onClick={() => load({background:true})}><ArrowsClockwise size={17} />تحديث النتائج</button>
            <button type="button" disabled={busy || sallaSyncing} onClick={syncSalla}><ArrowsClockwise size={17} />{sallaSyncing ? "جارٍ طلب المزامنة" : "مزامنة سلة الآن"}</button>
            {provider === "snapchat" && <button type="button" disabled={busy || syncing || currentReport?.selected_account_id === "all"} onClick={sync}><ArrowsClockwise size={17} className={syncing ? "report-spin" : ""} />{syncing ? "جارٍ مزامنة سناب" : "مزامنة سناب الآن"}</button>}
        </section>
        <div className="report-context"><span>النتائج: <b>{source === "salla" ? "طلبات سلة المتزامنة دون تكرار" : "تحويلات المنصة"}</b> · الفترة بتوقيت {source === "salla" ? "Asia/Riyadh" : provider === "all" || accountId === "all" ? "كل حساب كما في مصدره" : currentReport?.rows?.[0]?.timezone || data?.range?.timezone || "الحساب"}</span><span>تحديث تلقائي كل دقيقة · آخر قراءة {data?.generated_at ? new Date(data.generated_at).toLocaleTimeString("ar-SA") : "—"}</span></div>
        {data?.reports?.some(r => r.last_sync_at) && <p className="report-footnote">آخر مزامنة للمصدر: {data.reports.filter(r => r.last_sync_at).map(r => `${r.label}: ${new Date(r.last_sync_at).toLocaleString("ar-SA")}`).join(" · ")}</p>}
        {source === "salla" && data?.reconciliation && <div className="report-explanation" role="status" data-testid="salla-reconciliation">
            <strong>إجمالي طلبات سلة المتزامنة: {formatReportNumber(data.reconciliation.orders, 0)} طلب · {formatReportNumber(data.reconciliation.sales_sar)} ر.س</strong>
            <div>{data.reconciliation.scope === "store" ? `فرق توزيع الطلبات: ${formatReportNumber(data.reconciliation.order_delta, 0)} · فرق المبيعات: ${formatReportNumber(data.reconciliation.sales_delta_sar)} ر.س` : "الجدول يعرض المصدر أو الحساب المحدد؛ اختر جميع المنصات لعرض إجمالي المتجر."}</div>
            <small>{statusScope === "all" ? "جميع حالات الطلبات، بما فيها الملغاة والمسترجعة." : `الحالات: ${(data.reconciliation.included_statuses || []).join("، ") || "جميع الحالات"}.`} قيمة الطلبات بالريال وفق التحويل المسجل، وليست صافي المبيعات بعد خصم المرتجعات. المطابقة هنا بين الطلبات المتزامنة وتوزيعها؛ استخدم مزامنة سلة لجلب آخر التحديثات.</small>
        </div>}
        {(error || notice) && <p className={error ? "report-alert" : "report-explanation"} role="status">{error || notice}</p>}
        {data?.reports?.some(r => r.status !== "available") && <p className="report-alert" role="status">بعض بيانات المنصات غير متاحة أو غير مرتبطة. لا تُحسب البيانات الناقصة كصفر.</p>}
        {data?.reports?.flatMap(r => (r.warnings || []).map((warning, index) => <p className="report-alert" role="status" key={`${r.provider}-${index}`}>{r.label}: {warning}</p>))}
        <section className="reports-table-panel"><div className="reports-toolbar"><span>جميع المبالغ بالريال السعودي</span><div><input aria-label="بحث في التقرير" placeholder={provider === "all" ? "بحث بالمنصة" : "بحث باسم الحملة أو المعرّف"} value={query} onChange={e => setQuery(e.target.value)} />{provider !== "all" && !orderSource && <><label><input type="checkbox" checked={activeOnly} onChange={e => setActiveOnly(e.target.checked)} />النشطة فقط</label><label><input type="checkbox" checked={activityOnly} onChange={e => setActivityOnly(e.target.checked)} />إخفاء الحملات بلا نشاط</label></>}<button type="button" disabled={!displayed.length} onClick={() => downloadCsv(displayed, `reports-${provider}-${data?.range.date_from}.csv`)}><DownloadSimple size={19} />تنزيل</button></div></div>
            <div className="reports-scroll" aria-busy={busy}><table className="reports-table"><thead><tr><th className="report-name">{provider === "all" ? "اسم المنصة / المصدر" : orderSource ? "مصدر الطلبات" : "اسم الحملة"}</th><th>الحالة</th>{COLUMNS.map(([key,label]) => <th key={key} className={["shipping_cost_sar", "payment_fees_sar"].includes(key) ? "added-cost" : ""}><button onClick={() => setSort({key,asc:sort.key === key ? !sort.asc : false})}>{source === "platform" && key === "orders" ? "تحويلات المنصة" : label}{sort.key === key ? sort.asc ? " ↑" : " ↓" : ""}</button>{key === "net_margin_pct" && <small>بعد التكاليف المباشرة</small>}</th>)}</tr></thead>
                <tbody>{!busy && displayed.map(row => <tr key={row.id}><td className="report-name"><button onClick={() => provider === "all" ? change({provider:row.provider,account_id:""}) : setDialogId(row.id)}>{row.name}</button><small dir="ltr">{row.row_kind === "order_source" || ORDER_SOURCES.has(row.provider) ? "عرض تفاصيل المنتجات" : row.campaign_id || "عرض الحملات الإعلانية"}</small></td><td><span className={`report-status ${row.active || row.status === "available" ? "active" : ""}`}>{provider === "all" ? row.status === "available" ? "متاحة" : "غير متاحة" : row.row_kind === "order_source" ? "طلبات سلة" : row.active ? "نشطة" : "متوقفة"}</span></td>{COLUMNS.map(([key]) => <td key={key}><Metric field={key} value={row.metrics[key]} />{key === "net_profit_sar" && provider !== "all" && <button className="report-products-link" onClick={() => setDialogId(row.id)}>تفاصيل المنتجات</button>}</td>)}</tr>)}{(busy || !displayed.length) && <tr><td colSpan={12} className="report-empty">{busy ? "جارٍ تحميل التقرير من مصادره…" : "لا توجد نتائج للفترة والفلاتر المحددة."}</td></tr>}</tbody>
                {!busy && data && <tfoot><tr><th colSpan={2}>إجمالي الفترة</th>{COLUMNS.map(([key]) => <td key={key}><Metric field={key} value={data.totals[key]} /></td>)}</tr></tfoot>}</table></div>
            <footer className="reports-table-footer"><span>عرض {displayed.length} من {rows.length} {provider === "all" ? "مصادر" : orderSource ? "مصدر" : "حملة / مجموعة طلبات"} · الإجمالي يشمل كامل الفترة قبل فلاتر العرض</span><span>مصدر موحّد للحساب والفترة المحددين</span></footer>
        </section>
        <p className="report-equation">صافي الأرباح = إجمالي المبيعات − تكلفة المنتجات − الصرف الإعلاني − تكلفة الشحن − رسوم الدفع</p>
        {source === "platform" && <p className="report-footnote">لعرض إجمالي طلبات المتجر، بما فيها المباشرة وغير المصنّفة، اختر «عرض نتائج سلة». تحويلات المنصة قد تختلف عن طلبات سلة بسبب الإسناد. تفاصيل المنتجات والتكاليف والأرباح متاحة عبر «عرض نتائج سلة»؛ لا يمكن استنتاجها من التحويلات الإجمالية.</p>}
        {modalRow && <ProductDialog row={modalRow} source={source} provider={provider} onSource={v => change({source:v})} onClose={close} />}
    </main>;
}
