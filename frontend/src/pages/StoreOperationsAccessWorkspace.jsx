import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCircle, ClockCounterClockwise, Robot, ShieldCheck, UsersThree, WarningCircle } from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    getStoreOperationsAccess,
    getStoreOperationsAudit,
    saveStoreOperationsAccess,
} from "../services/aiStoreOperations";

const RESPONSIBILITY_LABELS = {
    instant_ready: "الطلبات الجاهزة والشحن الفوري",
    packing: "التغليف",
    shipping_labeling: "الشحن والعنونة",
    carrier_handoff: "التسليم لشركة الشحن",
    stock_preparation: "تجهيز مخزون جاهز",
};

function RoleCard({ user, roleLabels, roleCatalog, warehouses, responsibilityTypes, onSave }) {
    const assignment = user.assignment || {};
    const [roleKey, setRoleKey] = useState(assignment.role_key || (user.is_owner ? "owner" : "product_operator"));
    const [enabled, setEnabled] = useState(assignment.enabled !== false);
    const [warehouseIds, setWarehouseIds] = useState(assignment.warehouse_ids || []);
    const [workplaceWarehouseId, setWorkplaceWarehouseId] = useState(assignment.workplace_warehouse_id || "");
    const [responsibilities, setResponsibilities] = useState(assignment.fulfillment_responsibilities || []);
    const [extraPermissions, setExtraPermissions] = useState(assignment.extra_permissions || []);
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        setRoleKey(assignment.role_key || (user.is_owner ? "owner" : "product_operator"));
        setEnabled(assignment.enabled !== false);
        setWarehouseIds(assignment.warehouse_ids || []);
        setWorkplaceWarehouseId(assignment.workplace_warehouse_id || "");
        setResponsibilities(assignment.fulfillment_responsibilities || []);
        setExtraPermissions(assignment.extra_permissions || []);
    }, [assignment.role_key, assignment.enabled, assignment.warehouse_ids, assignment.workplace_warehouse_id, assignment.fulfillment_responsibilities, assignment.extra_permissions, user.is_owner]);

    async function save() {
        setBusy(true);
        try {
            await onSave(user.id, {
                role_key: roleKey,
                enabled,
                extra_permissions: extraPermissions,
                denied_permissions: assignment.denied_permissions || [],
                warehouse_ids: warehouseIds,
                workplace_warehouse_id: workplaceWarehouseId || null,
                fulfillment_responsibilities: responsibilities,
            });
            toast.success(`تم حفظ صلاحيات ${user.name || user.email}`);
        } finally {
            setBusy(false);
        }
    }

    const effective = user.effective_permissions || [];
    const toggleWarehouse = (value) => {
        const next = warehouseIds.includes(value)
            ? warehouseIds.filter((item) => item !== value)
            : [...warehouseIds, value];
        setWarehouseIds(next);
        if (!next.includes(workplaceWarehouseId)) {
            setWorkplaceWarehouseId(next.length === 1 ? next[0] : "");
        } else if (!workplaceWarehouseId && next.length === 1) {
            setWorkplaceWarehouseId(next[0]);
        }
    };
    const toggle = (values, value, setter) => setter(values.includes(value) ? values.filter((item) => item !== value) : [...values, value]);
    return (
        <article className="rounded-2xl border bg-white p-4 shadow-sm">
            <div className="flex flex-col gap-4 xl:flex-row xl:items-center">
                <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                        <h2 className="truncate text-lg font-black">{user.name || "مستخدم"}</h2>
                        {user.is_owner && <span className="rounded-full bg-amber-100 px-2 py-1 text-[11px] font-black text-amber-800">Owner</span>}
                        <span className="rounded-full bg-slate-100 px-2 py-1 text-[11px] font-bold text-slate-600">{user.account_role || "viewer"}</span>
                    </div>
                    <p className="mt-1 truncate text-xs text-slate-500" dir="ltr">{user.email}</p>
                </div>
                <label className="min-w-[220px] text-xs font-bold text-slate-600">الدور التشغيلي
                    <select value={roleKey} onChange={(event) => setRoleKey(event.target.value)} className="mt-1 w-full rounded-xl border p-3 text-sm text-slate-900">
                        {Object.keys(roleCatalog || {}).map((key) => <option key={key} value={key}>{roleLabels?.[key] || key}</option>)}
                    </select>
                </label>
                <label className="flex items-center gap-2 rounded-xl border px-3 py-3 text-sm font-bold">
                    <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /> مفعل
                </label>
                <button type="button" onClick={save} disabled={busy} className="rounded-xl bg-violet-700 px-5 py-3 font-black text-white disabled:opacity-50">{busy ? "جارٍ الحفظ…" : "حفظ الدور"}</button>
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
                {effective.length ? effective.map((permission) => <span key={permission} className="rounded-lg border border-emerald-200 bg-emerald-50 px-2 py-1 text-[11px] font-bold text-emerald-800" dir="ltr">{permission}</span>) : <span className="text-xs text-slate-400">لا توجد صلاحيات تشغيلية فعالة.</span>}
            </div>
            <div className="mt-4 grid gap-3 lg:grid-cols-2">
                <section className="rounded-xl border bg-slate-50 p-3">
                    <div className="text-xs font-black text-slate-700">الفروع والمخازن المسؤولة</div>
                    <div className="mt-2 flex flex-wrap gap-2">
                        {(warehouses || []).map((warehouse) => <label key={warehouse.id} className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-xs font-bold ${warehouseIds.includes(warehouse.id) ? "border-violet-400 bg-violet-50 text-violet-900" : "bg-white"}`}><input type="checkbox" checked={warehouseIds.includes(warehouse.id)} onChange={() => toggleWarehouse(warehouse.id)} />{warehouse.name}{warehouse.city ? ` — ${warehouse.city}` : ""}</label>)}
                        {!(warehouses || []).length && <span className="text-xs text-slate-400">أنشئ فرعًا أو مخزنًا أولًا.</span>}
                    </div>
                    {!user.is_owner && warehouseIds.length > 0 && (
                        <label className="mt-3 block text-xs font-black text-slate-700">
                            مقر العمل الافتراضي
                            <select
                                value={workplaceWarehouseId}
                                onChange={(event) => setWorkplaceWarehouseId(event.target.value)}
                                className="mt-1 w-full rounded-lg border bg-white px-3 py-2 text-sm"
                            >
                                <option value="">اختر مقر العمل</option>
                                {(warehouses || []).filter((warehouse) => warehouseIds.includes(warehouse.id)).map((warehouse) => (
                                    <option key={warehouse.id} value={warehouse.id}>{warehouse.name}{warehouse.city ? ` — ${warehouse.city}` : ""}</option>
                                ))}
                            </select>
                            <span className="mt-1 block font-normal text-slate-500">يظهر هذا الفرع تلقائيًا عند استلام المخزون وتنفيذ المهام.</span>
                        </label>
                    )}
                </section>
                <section className="rounded-xl border bg-slate-50 p-3">
                    <div className="text-xs font-black text-slate-700">مسؤوليات التنفيذ</div>
                    <div className="mt-2 flex flex-wrap gap-2">
                        {(responsibilityTypes || []).map((value) => <label key={value} className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-xs font-bold ${responsibilities.includes(value) ? "border-emerald-400 bg-emerald-50 text-emerald-900" : "bg-white"}`}><input type="checkbox" checked={responsibilities.includes(value)} onChange={() => toggle(responsibilities, value, setResponsibilities)} />{RESPONSIBILITY_LABELS[value] || value}</label>)}
                    </div>
                    <label className={`mt-3 flex items-start gap-2 rounded-lg border px-3 py-2 text-xs font-bold ${extraPermissions.includes("fulfillment.labels.reprint") ? "border-amber-400 bg-amber-50 text-amber-950" : "bg-white"}`}>
                        <input type="checkbox" checked={extraPermissions.includes("fulfillment.labels.reprint")} onChange={() => toggle(extraPermissions, "fulfillment.labels.reprint", setExtraPermissions)} />
                        <span>السماح بإعادة الطباعة<span className="mt-1 block font-normal">تظل بحاجة إلى سبب، وتسجل باسم الموظف.</span></span>
                    </label>
                </section>
            </div>
        </article>
    );
}

export default function StoreOperationsAccessWorkspace() {
    const [data, setData] = useState(null);
    const [audit, setAudit] = useState([]);
    const [loading, setLoading] = useState(true);
    const [query, setQuery] = useState("");

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const [access, log] = await Promise.all([
                getStoreOperationsAccess(),
                getStoreOperationsAudit({ limit: 100 }),
            ]);
            setData(access);
            setAudit(log.items || []);
        } catch (error) {
            toast.error(error?.response?.data?.detail?.code || "تعذر تحميل صلاحيات فريق المتجر");
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const users = useMemo(() => {
        const needle = query.trim().toLowerCase();
        const rows = data?.users || [];
        if (!needle) return rows;
        return rows.filter((user) => `${user.name || ""} ${user.email || ""}`.toLowerCase().includes(needle));
    }, [data?.users, query]);

    async function save(userId, payload) {
        await saveStoreOperationsAccess(userId, payload);
        await load();
    }

    return (
        <div className="space-y-5" dir="rtl" data-testid="store-operations-access-workspace">
            <section className="overflow-hidden rounded-3xl border border-violet-200 bg-white shadow-sm">
                <div className="bg-gradient-to-l from-slate-950 to-violet-800 p-6 text-white">
                    <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                        <div>
                            <div className="flex items-center gap-2 text-sm font-black text-violet-100"><UsersThree size={22} weight="duotone" /> Mezan Store Operations Access</div>
                            <h1 className="mt-2 text-2xl font-black sm:text-3xl">فريق المتجر والصلاحيات</h1>
                            <p className="mt-2 max-w-3xl text-sm leading-7 text-violet-100">طبقة تشغيل مستقلة فوق حسابات ميزان الحالية تحدد من يراجع المنتجات والتكاليف والصور، وما الذي يستطيع الذكاء الاصطناعي تنفيذه.</p>
                        </div>
                        <div className="rounded-2xl border border-white/20 bg-white/10 p-4 text-sm">
                            <Robot className="ml-1 inline" /> الوكيل: <b>{data?.ai_agent?.name || "Mezan AI"}</b><br />
                            <span className="text-xs text-violet-100">النشر عالي المخاطر: غير مسموح</span>
                        </div>
                    </div>
                </div>
                <div className="border-t border-violet-100 bg-violet-50 px-5 py-3 text-xs font-bold text-violet-900"><ShieldCheck className="ml-1 inline" /> الحسابات وكلمات المرور تبقى في نظام الفريق الحالي؛ هذه الصفحة تدير صلاحيات تشغيل المتجر فقط.</div>
            </section>

            <section className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-2xl border bg-white p-4"><div className="text-xs text-slate-500">المستخدمون</div><div className="mt-1 text-3xl font-black">{data?.users?.length || 0}</div></div>
                <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4"><div className="text-xs text-emerald-700">أدوار مفعلة</div><div className="mt-1 text-3xl font-black text-emerald-900">{(data?.users || []).filter((user) => user.assignment?.enabled !== false && user.assignment).length}</div></div>
                <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4"><div className="text-xs text-amber-700">بدون دور تشغيلي</div><div className="mt-1 text-3xl font-black text-amber-900">{(data?.users || []).filter((user) => !user.assignment).length}</div></div>
            </section>

            <section className="rounded-2xl border bg-white p-4"><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="ابحث باسم الموظف أو البريد…" className="h-12 w-full rounded-xl border px-4 outline-none focus:border-violet-400" /></section>

            <section className="space-y-3">
                {loading && <div className="rounded-2xl border bg-white p-10 text-center text-slate-500">جارٍ تحميل المستخدمين والصلاحيات…</div>}
                {!loading && users.map((user) => <RoleCard key={user.id} user={user} roleLabels={data?.role_labels} roleCatalog={data?.role_catalog} warehouses={data?.warehouses} responsibilityTypes={data?.fulfillment_responsibility_types} onSave={save} />)}
            </section>

            <section className="rounded-2xl border bg-white p-4 shadow-sm">
                <h2 className="font-black"><ClockCounterClockwise className="ml-1 inline" /> سجل تغييرات الصلاحيات</h2>
                <div className="mt-3 space-y-2">
                    {!audit.length && <p className="text-sm text-slate-400">لا توجد تغييرات مسجلة حتى الآن.</p>}
                    {audit.map((row) => <div key={row.id} className="flex flex-col gap-1 rounded-xl border bg-slate-50 p-3 text-xs sm:flex-row sm:items-center sm:justify-between"><div><CheckCircle className="ml-1 inline text-emerald-600" /><b>{row.action}</b> · {row.target_id}</div><div className="text-slate-500">{row.actor_name || row.actor_id} · {row.occurred_at}</div></div>)}
                </div>
            </section>

            <section className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-xs leading-6 text-amber-900"><WarningCircle className="ml-1 inline" /> تعيين دور هنا لا يمنح صلاحية تسجيل دخول جديدة، ولا يتجاوز صلاحيات الحساب العامة في ميزان. يجب أن ينجح الشرطان معًا قبل تنفيذ أي عملية.</section>
        </div>
    );
}
