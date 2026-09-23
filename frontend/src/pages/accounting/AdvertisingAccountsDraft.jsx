import React, {useState} from "react";
import {CARDS, TABS, PREFIX, PROVIDERS, PAYMENT_MODES, canSee, canOpenTab, moneyText, countText, statusText,
    viewState, detailFor, scopedRows} from "./advertisingAccountsDraftContract";

const ACCOUNT_COLUMNS = ["المنصة", "اسم الحساب", "المعرف الخارجي", "العملة", "المنطقة الزمنية", "طريقة الدفع",
    "المحفظة أو الذمة", "البنك أو الصندوق الافتراضي", "آخر مزامنة", "آخر يوم مكتمل", "آخر إغلاق محاسبي", "حالة البيانات", "الحالة المحاسبية"];
const DAILY = [["date", "اليوم"], ["original_amount", "المبلغ الأصلي", "money"], ["currency", "العملة"], ["source_already_sar", "SAR من المصدر", "bool"],
    ["fx_rate", "سعر الصرف"], ["value_sar", "القيمة SAR", "money"], ["fx_source", "مصدر السعر"], ["settings_revision", "نسخة الإعدادات"], ["source_revision", "نسخة المصدر"], ["status", "الحالة", "status"]];
const INVOICES = [["external_number", "رقم الفاتورة"], ["period_start", "من"], ["period_end", "إلى"], ["original_amount", "المبلغ الأصلي", "money"], ["currency", "العملة"],
    ["fx_rate", "سعر الصرف المجمد"], ["value_sar", "القيمة SAR", "money"], ["issued_on", "الإصدار"], ["due_on", "الاستحقاق"],
    ["paid_sar", "المدفوع SAR", "money"], ["remaining_sar", "المتبقي SAR", "money"], ["status", "الحالة المحاسبية", "status"],
    ["source", "المصدر", "status"], ["evidence_ref", "مرجع PDF أو الدليل"], ["match_status", "مطابقة الصرف", "status"], ["review_ref", "سجل المراجعة"]];
const PAYMENTS = [["reference", "الدفعة"], ["date", "التاريخ"], ["source_ref", "البنك أو الصندوق الفعلي"], ["movement_ref", "مرجع الحركة"],
    ["evidence_ref", "إثبات السداد"], ["principal_sar", "الأصل SAR", "money"], ["fee_sar", "العمولة SAR", "money"], ["fx_difference_sar", "فرق الصرف SAR", "money"]];
const MATCHES = [["reference", "مرجع المطابقة"], ["daily_sar", "صرف المنصة SAR", "money"], ["wallet_sar", "استهلاك المحفظة SAR", "money"],
    ["invoice_sar", "الفاتورة SAR", "money"], ["bank_sar", "البنك أو الصندوق SAR", "money"], ["fee_sar", "العمولة SAR", "money"],
    ["fx_rate", "سعر الصرف"], ["journal_sar", "القيد SAR", "money"], ["difference_sar", "الفرق SAR", "money"], ["status", "الحالة", "status"]];
const AUDIT = [["at", "الوقت"], ["actor_ref", "المراجع"], ["action", "الإجراء"], ["before", "قبل"], ["after", "بعد"], ["source_revision", "نسخة المصدر"], ["reason", "السبب"]];
const ROLES = {advertising_expense: "مصروف الإعلانات", platform_payable: "ذمة المنصة", wallet_asset: "المحفظة الإعلانية",
    actual_bank_cash: "البنك أو الصندوق الفعلي", bank_fee_expense: "مصروف عمولات بنكية", fx_loss: "خسارة فرق صرف", fx_gain: "ربح فرق صرف"};
const cellText = (value) => typeof value === "string" || typeof value === "number" ? String(value) : "غير متاح";
const unavailable = <span className="text-slate-500">غير متاح</span>;
function Money({value}) { return <bdi dir="ltr" className="tabular-nums">{moneyText(value)}</bdi>; }
function DataTable({title, rows, columns}) {
    return <div className="overflow-x-auto rounded-lg border border-slate-200 my-3">
        <table className="w-full text-sm text-right"><caption className="text-right font-semibold p-3">{title}</caption>
            <thead className="bg-slate-50"><tr>{columns.map(([key, label]) => <th scope="col" key={key} className="p-3 whitespace-nowrap">{label}</th>)}</tr></thead>
            <tbody>{!rows ? <tr><td colSpan={columns.length} className="p-4">البيانات غير متاحة أو لم يكتمل التحقق من نطاقها؛ لا تُعد صفرًا.</td></tr>
                : rows.length === 0 ? <tr><td colSpan={columns.length} className="p-4">لا توجد حركات في الإسقاط المزوّد.</td></tr>
                : rows.map((row, index) => <tr key={row.id || index} className="border-t border-slate-100">{columns.map(([key, , kind]) => <td key={key} className="p-3 whitespace-nowrap">
                    {kind === "money" ? <Money value={row[key]}/> : kind === "status" ? statusText(row[key]) : kind === "bool" ? (row[key] === true ? "نعم" : row[key] === false ? "لا" : unavailable)
                        : <bdi>{cellText(row[key])}</bdi>}
                </td>)}</tr>)}</tbody>
        </table>
    </div>;
}
function Summary({summary, permissions}) {
    return <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3" data-testid="ad-summary">
        {CARDS.map((card) => {
            const entry = summary?.[card.id];
            const allowed = canSee(permissions, card.permission);
            const ready = allowed && entry?.status === "complete";
            return <section key={card.id} className="rounded-xl border border-slate-200 bg-white p-4" data-card={card.id}>
                <h2 className="text-sm text-slate-600">{card.label}</h2>
                <div className="mt-2 text-xl font-semibold">{!allowed ? "صلاحية غير متاحة" : !ready ? unavailable
                    : card.kind === "pair" ? <><span className="text-sm">المدفوع </span><Money value={entry.paid_sar}/><span className="text-sm"> / المتبقي </span><Money value={entry.remaining_sar}/></>
                        : card.kind === "count" ? <bdi dir="ltr">{countText(entry.value)}</bdi> : <Money value={entry.value}/>}</div>
                <p className="mt-1 text-xs text-slate-500">{card.kind === "count" ? "عدد" : "SAR"}{ready && entry.as_of ? ` · ${entry.as_of}` : ""}</p>
            </section>;
        })}
    </div>;
}
function DetailPanel({account, detail, tab, permissions}) {
    const tabContract = TABS.find((item) => item.id === tab);
    if (!tabContract || !canOpenTab(permissions, tabContract)) return <p role="alert">لا تملك صلاحية هذا التبويب.</p>;
    const rows = (key) => scopedRows(detail, key);
    if (tab === "overview") return <div className="space-y-3">
        <p>طريقة الدفع: <strong>{PAYMENT_MODES[account.payment_mode] || "الربط غير مكتمل"}</strong> · مصدر الاستحقاق: <strong>{statusText(account.accrual_source)}</strong></p>
        <p>الإغلاق المقترح: 1:00 صباحًا حسب <bdi dir="ltr">{account.timezone_name || "المنطقة الزمنية مفقودة"}</bdi>، لليوم المحلي السابق. المجدول غير مفعّل هنا.</p>
        <p>الصرف اليومي والفاتورة لا ينشئان المصروف نفسه مرتين. تغيير الإعدادات لا يغيّر Snapshot الأيام القديمة.</p>
        <button type="button" disabled className="rounded border px-3 py-2 opacity-50">إدارة الربط والإعدادات — معلّقة على الحسابات المالية</button>
    </div>;
    if (tab === "daily") return <><p>البيانات الناقصة لا تتحول إلى صفر. عمولة البنك ليست جزءًا من استهلاك المحفظة اليومي.</p><DataTable title="الصرف اليومي ومصدر سعر الصرف" rows={rows("daily")} columns={DAILY}/></>;
    if (tab === "wallet") return <>
        <p>مرجع المحفظة أو الذمة التابعة للحسابات المالية: <bdi>{account.funding_ref || "غير مربوط"}</bdi>. لا ينشأ نظام محافظ آخر هنا.</p>
        <p>الأرصدة السابقة للقطع تُدخل من صفحة الأرصدة الافتتاحية؛ رصيد المحفظة والمديونية مستقلان ولا تتم مقاصتهما.</p>
        <button type="button" disabled className="border rounded px-3 py-2">فتح الحسابات المالية والأرصدة الافتتاحية — الربط معلّق</button>
        <DataTable title="المحفظة والشحن — عرض فقط" rows={rows("topups")} columns={[["date", "التاريخ"], ["reference", "المرجع"], ["source_ref", "البنك أو الصندوق"], ["value_sar", "القيمة SAR", "money"], ["fee_sar", "العمولة SAR", "money"]]}/>
    </>;
    if (tab === "invoices") return <>
        <p>رفع الفاتورة ينشئ مسودة فقط. حالة API «مدفوعة» ليست إثبات سداد. فرق الفاتورة يظهر مستقلاً عن المصروف المسجل.</p>
        <button type="button" disabled className="border rounded px-3 py-2">استيراد فاتورة — الربط معلّق</button>
        <DataTable title="الفواتير والمديونيات" rows={rows("invoices")} columns={INVOICES}/>
    </>;
    if (tab === "payments") return <>
        <p>معاينة سداد كامل أو جزئي؛ عدة دفعات للفاتورة أو دفعة موزعة على فواتير. إثبات السداد والحركة الفعلية مطلوبان.</p>
        <button type="button" disabled className="border rounded px-3 py-2">تسجيل دفعة — غير مفعّل</button>
        <DataTable title="الدفعات ومصدر السداد الفعلي" rows={rows("payments")} columns={PAYMENTS}/>
        <DataTable title="توزيع الدفعات والمتبقي" rows={rows("allocations")} columns={[["payment_ref", "الدفعة"], ["invoice_ref", "الفاتورة"], ["original_amount", "المبلغ بعملة الفاتورة", "money"], ["currency", "العملة"], ["carrying_sar", "القيمة الدفترية SAR", "money"], ["remaining_sar", "المتبقي SAR", "money"]]}/>
    </>;
    if (tab === "reconciliation") return <>
        <p>أي فرق يبقى ظاهرًا. مثال: 10,000 SAR صرف مقابل 10,050 SAR فاتورة يعني فرق 50 SAR فقط بعد المراجعة، وليس مصروفًا جديدًا بقيمة الفاتورة.</p>
        <DataTable title="مقارنة المصادر والحركة والقيد" rows={rows("reconciliation")} columns={MATCHES}/>
    </>;
    if (tab === "journals") return <>
        <p className="font-semibold">معاينة فقط — لا يوجد زر ترحيل ولا تُمنح صلاحية الترحيل.</p>
        <DataTable title="أرجل معاينة القيد بالريال" rows={rows("journal_legs")?.map((row) => ({...row, role: ROLES[row.role] || row.role})) ?? null}
            columns={[["preview_ref", "المعاينة"], ["role", "الحساب المحاسبي"], ["reference", "مرجع الحساب"], ["debit", "مدين SAR", "money"], ["credit", "دائن SAR", "money"]]}/>
        <DataTable title="القيود الأصلية والمرفقات — مراجع فقط" rows={rows("attachments")} columns={[["reference", "مرجع الدليل"], ["journal_ref", "القيد الأصلي إن وجد"], ["status", "الحالة", "status"]]}/>
    </>;
    return <DataTable title="سجل التدقيق والمراجعة" rows={rows("audit")} columns={AUDIT}/>;
}

/** Isolated, unmounted component. All data comes from a future owner-scoped read adapter. */
export default function AdvertisingAccountsDraft({ownerId, permissions = [], model = null}) {
    const [query, setQuery] = useState("");
    const [selected, setSelected] = useState(null);
    const [tab, setTab] = useState("overview");
    if (!ownerId || !canSee(permissions, PREFIX + "view")) return <p dir="rtl" role="alert">لا تملك صلاحية عرض الحسابات الإعلانية المحاسبية.</p>;
    const activeTab = canOpenTab(permissions, TABS.find((item) => item.id === tab)) ? tab : "overview";
    const view = viewState(ownerId, model);
    const visible = view.accounts.filter((row) => [row.name, row.external_account_id, PROVIDERS[row.ad_provider]].some((value) => String(value || "").toLowerCase().includes(query.toLowerCase())));
    const account = view.accounts.find((row) => row.linked_account_ref === selected);
    const detail = account ? detailFor(model, ownerId, selected) : null;
    const chooseTab = (event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        const allowed = TABS.filter((item) => canOpenTab(permissions, item));
        const current = allowed.findIndex((item) => item.id === activeTab);
        const index = event.key === "Home" ? 0 : event.key === "End" ? allowed.length - 1
            : (current + (event.key === "ArrowLeft" ? 1 : -1) + allowed.length) % allowed.length;
        event.preventDefault(); setTab(allowed[index].id);
        event.currentTarget.parentElement.querySelector(`[data-tab="${allowed[index].id}"]`)?.focus();
    };
    return <section dir="rtl" lang="ar" className="space-y-5 p-4 text-slate-900" data-testid="advertising-accounts-draft">
        <header><p className="text-sm text-slate-500">المحاسبة / الحسابات الإعلانية</p><h1 className="text-2xl font-bold">الحسابات الإعلانية</h1></header>
        <div role="status" className="rounded-lg border border-amber-300 bg-amber-50 p-4">Draft فقط — الربط المشترك معلّق. هذه الواجهة لا تنشئ حسابًا ماليًا أو قيدًا أو رصيدًا، ولا تُسجل دفعة فعلية.</div>
        <Summary summary={view.summary} permissions={permissions}/>
        {view.status !== "ready" && <p role="alert">{view.status === "loading" ? "جاري تزويد الإسقاط؛ الأرقام غير متاحة حتى اكتماله." : "قائمة الحسابات المرتبطة غير متاحة قبل إكمال الربط أو التحقق؛ لا تُعد هذه نتيجة صفر حساب."} {view.code && <bdi dir="ltr">{view.code}</bdi>}</p>}
        <label className="block">بحث في الحسابات المرتبطة <input aria-label="بحث في الحسابات المرتبطة" type="search" disabled={view.status !== "ready"} value={query}
            onChange={(event) => {setQuery(event.target.value); setSelected(null); setTab("overview");}} className="border rounded px-3 py-2 mr-2"/></label>
        <div className="overflow-x-auto border rounded-lg"><table className="w-full text-right text-sm" data-testid="ad-accounts-table">
            <caption className="text-right p-3 font-semibold">الحسابات المرتبطة من Snapchat وMeta وTikTok وGoogle</caption>
            <thead className="bg-slate-50"><tr>{ACCOUNT_COLUMNS.map((label) => <th key={label} scope="col" className="p-3 whitespace-nowrap">{label}</th>)}</tr></thead>
            <tbody>{visible.map((row) => <tr key={row.linked_account_ref} className="border-t border-slate-100">
                <td className="p-3">{PROVIDERS[row.ad_provider]}</td><td className="p-3"><button type="button" className="underline font-semibold" onClick={() => {setSelected(row.linked_account_ref); setTab("overview");}}>{row.name || "عرض الحساب المرتبط"}</button></td>
                {[row.external_account_id, row.currency, row.timezone_name || statusText("MISSING_TIMEZONE"), PAYMENT_MODES[row.payment_mode] || statusText("MISSING_FUNDING_SOURCE"), row.funding_ref,
                    row.default_source_ref, row.last_sync, row.last_complete_day, row.last_accounting_close, statusText(row.data_status), statusText(row.accounting_status)].map((value, index) =>
                    <td key={index} className="p-3 whitespace-nowrap"><bdi>{cellText(value)}</bdi></td>)}
            </tr>)}{visible.length === 0 && <tr><td colSpan={13} className="p-4">{view.status === "ready" ? "لا توجد حسابات مطابقة في القائمة المزوّدة." : "الحسابات غير متاحة؛ لم تُنشأ حسابات تجريبية."}</td></tr>}</tbody>
        </table></div>
        {account && <section className="rounded-xl border p-4 space-y-4" aria-label="تفاصيل الحساب المحدد">
            <h2 className="text-lg font-bold">{account.name} <bdi dir="ltr" className="text-sm font-normal">{account.external_account_id}</bdi></h2>
            <div role="tablist" aria-label="تفاصيل الحساب الإعلاني" className="flex flex-wrap gap-2">{TABS.map((item) => <button type="button" role="tab" key={item.id}
                id={`ad-tab-${item.id}`} data-tab={item.id} aria-controls="ad-detail-panel" aria-selected={activeTab === item.id} disabled={!canOpenTab(permissions, item)}
                tabIndex={activeTab === item.id ? 0 : -1} onKeyDown={chooseTab} onClick={() => setTab(item.id)} className={`border rounded px-3 py-2 disabled:opacity-40 ${activeTab === item.id ? "bg-slate-900 text-white" : "bg-white"}`}>{item.label}</button>)}</div>
            {!detail && <p role="status">تفاصيل الحساب غير متاحة من المصدر؛ لا تعرض أرصدة افتراضية.</p>}
            <div id="ad-detail-panel" role="tabpanel" aria-labelledby={`ad-tab-${activeTab}`}><DetailPanel account={account} detail={detail} tab={activeTab} permissions={permissions}/></div>
        </section>}
        <p className="text-xs text-slate-500">الواجهة معزولة وغير مسجلة في الراوتر. لا قراءة من ميزان القديم؛ الأرصدة الافتتاحية والمحافظ تتبع المهمة المشتركة.</p>
    </section>;
}
