import { useCallback, useEffect, useMemo, useState } from "react";
import {
    ArrowCounterClockwise,
    ArrowsLeftRight,
    CheckCircle,
    Link,
    ShieldCheck,
    SpinnerGap,
    WarningCircle,
    Warehouse,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    createSallaInventorySyncPreview,
    loadSallaInventorySyncCatalog,
    newInventoryReceiptIdempotencyKey,
    publishSallaInventorySync,
    saveSallaInventoryBranchMappings,
    verifySallaInventorySyncRun,
} from "../../services/mezanInventoryReceiving";

const EMPTY = {
    salla_branches: [],
    mezan_warehouses: [],
    quantity_reasons: [],
    mapping: { revision: 0, mappings: [] },
    recent_runs: [],
    permissions: {},
    safety: {},
    salla_connection: {},
    feature: {},
};

const ERRORS = {
    inventory_branch_mapping_required: "اربط فرعًا واحدًا على الأقل بمخزن ميزان.",
    inventory_branch_mapping_revision_conflict: "تغيّر الربط من مستخدم آخر؛ حدّث الصفحة.",
    inventory_salla_branch_mapped_twice: "لا يمكن ربط فرع سلة بأكثر من مخزن.",
    inventory_mezan_warehouse_mapped_twice: "لا يمكن ربط مخزن ميزان بأكثر من فرع سلة.",
    inventory_sync_preview_stale: "تغيّر مخزون ميزان أو سلة بعد المعاينة. أنشئ معاينة جديدة.",
    inventory_sync_preview_expired: "انتهت صلاحية المعاينة. أنشئ معاينة جديدة.",
    inventory_sync_issues_acknowledgement_required: "راجع المشكلات ووافق على استبعاد العناصر غير الآمنة.",
    inventory_sync_publish_uncertain: "قبلت سلة جزءًا من العملية أو تعذر التأكد. لا تعِد النشر؛ استخدم التحقق.",
    salla_inventory_api_error: "تعذر الوصول إلى فروع أو كميات سلة. قد يحتاج الربط إلى صلاحيات الفروع والمنتجات.",
    salla_inventory_scopes_required: "فعّل branches.read و products.read_write في تطبيق سلة ثم أعد ربط المتجر.",
    salla_branch_inventory_sync_frozen: "مزامنة فروع سلة مجمّدة حتى توافق سلة على الصلاحية.",
    fulfillment_permission_required: "لا توجد صلاحية لهذه العملية.",
};

const STATUS_LABELS = {
    previewed: "معاينة جاهزة",
    publishing: "جارٍ النشر",
    accepted_pending_verification: "قبلتها سلة وتنتظر التحقق",
    verified: "تم النشر والتحقق",
    publish_uncertain: "تحتاج مطابقة",
    stale: "معاينة قديمة",
    expired: "انتهت المعاينة",
};

function errorMessage(error, fallback = "تعذر إتمام العملية.") {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string") return detail;
    return ERRORS[detail?.code] || detail?.message || fallback;
}

const controlClass = "h-12 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-100 disabled:bg-slate-100 disabled:text-slate-400";

function branchName(row) {
    return row?.name || row?.salla_branch?.name || row?.salla_branch_id || "فرع سلة";
}

export default function SallaInventorySync() {
    const [data, setData] = useState(EMPTY);
    const [mappings, setMappings] = useState([]);
    const [reasonId, setReasonId] = useState("");
    const [preview, setPreview] = useState(null);
    const [confirmToken, setConfirmToken] = useState("");
    const [acknowledgeIssues, setAcknowledgeIssues] = useState(false);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState("");

    const load = useCallback(async ({ quiet = false } = {}) => {
        if (!quiet) setLoading(true);
        try {
            const result = { ...EMPTY, ...(await loadSallaInventorySyncCatalog()) };
            setData(result);
            const saved = new Map(
                (result.mapping?.mappings || []).map(
                    (row) => [String(row.salla_branch_id), String(row.mezan_warehouse_id)],
                ),
            );
            setMappings(
                (result.salla_branches || []).map((branch) => ({
                    salla_branch_id: String(branch.id),
                    mezan_warehouse_id: saved.get(String(branch.id)) || "",
                })),
            );
            if (result.quantity_reasons?.length) {
                const preferred = result.quantity_reasons.find(
                    (row) => String(row.name || "").includes("تصحيح"),
                );
                setReasonId((current) => (
                    current
                    || String(preferred?.id || result.quantity_reasons[0].id)
                ));
            }
        } catch (error) {
            toast.error(errorMessage(error, "تعذر تحميل ربط المخازن مع سلة."));
        } finally {
            if (!quiet) setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const usedWarehouses = useMemo(
        () => new Set(mappings.map((row) => row.mezan_warehouse_id).filter(Boolean)),
        [mappings],
    );

    function updateMapping(sallaBranchId, warehouseId) {
        setMappings((current) => current.map((row) => (
            row.salla_branch_id === sallaBranchId
                ? { ...row, mezan_warehouse_id: warehouseId }
                : row
        )));
        setPreview(null);
        setConfirmToken("");
    }

    async function saveMappings() {
        setBusy("mappings");
        try {
            const result = await saveSallaInventoryBranchMappings({
                expected_revision: Number(data.mapping?.revision || 0),
                mappings: mappings.filter((row) => row.mezan_warehouse_id),
            });
            setData((current) => ({ ...current, mapping: result.mapping }));
            toast.success("تم حفظ ربط فروع سلة بمخازن ميزان.");
        } catch (error) {
            toast.error(errorMessage(error));
            await load({ quiet: true });
        } finally {
            setBusy("");
        }
    }

    async function makePreview() {
        if (!reasonId) {
            toast.error("اختر سبب تعديل الكمية.");
            return;
        }
        setBusy("preview");
        try {
            const result = await createSallaInventorySyncPreview({ reason_id: reasonId });
            setPreview(result.run);
            setConfirmToken(result.confirm_token || "");
            setAcknowledgeIssues(false);
            toast.success("تمت المعاينة فقط؛ لم تتغير أي كمية في سلة.");
        } catch (error) {
            toast.error(errorMessage(error));
        } finally {
            setBusy("");
        }
    }

    async function publish() {
        if (!preview || !confirmToken) return;
        setBusy("publish");
        try {
            const result = await publishSallaInventorySync({
                preview_id: preview.id,
                confirm_token: confirmToken,
                idempotency_key: newInventoryReceiptIdempotencyKey("salla-inventory-publish"),
                acknowledge_issues: acknowledgeIssues,
            });
            setPreview(result.run);
            setConfirmToken("");
            toast.success(
                result.run?.verified
                    ? "تم تحديث كميات سلة والتحقق منها."
                    : "قبلت سلة التحديث؛ قد تستغرق قائمة الانتظار عدة دقائق قبل التحقق.",
            );
            await load({ quiet: true });
        } catch (error) {
            toast.error(errorMessage(error));
            if (error?.response?.data?.detail?.code === "inventory_sync_preview_stale") {
                setConfirmToken("");
            }
        } finally {
            setBusy("");
        }
    }

    async function verify(run = preview) {
        if (!run?.id) return;
        setBusy(`verify:${run.id}`);
        try {
            const result = await verifySallaInventorySyncRun(run.id);
            setPreview(result.run);
            toast.success(
                result.run?.verified
                    ? "تطابقت كميات سلة مع ميزان."
                    : "لم يكتمل تطبيق قائمة انتظار سلة بعد؛ تحقق لاحقًا.",
            );
            await load({ quiet: true });
        } catch (error) {
            toast.error(errorMessage(error));
        } finally {
            setBusy("");
        }
    }

    if (loading) {
        return <div className="flex min-h-[300px] items-center justify-center"><SpinnerGap size={32} className="animate-spin text-violet-700" /></div>;
    }

    if (data.feature?.enabled === false) {
        return (
            <div className="space-y-5" dir="rtl" data-testid="salla-inventory-sync-frozen">
                <section className="rounded-3xl border border-amber-300 bg-amber-50 p-6 shadow-sm">
                    <div className="flex items-start gap-3">
                        <ShieldCheck size={30} className="mt-0.5 shrink-0 text-amber-700" weight="duotone" />
                        <div>
                            <h2 className="text-xl font-black text-amber-950">مزامنة فروع سلة مجمّدة مؤقتًا</h2>
                            <p className="mt-2 text-sm leading-7 text-amber-900">
                                لن يطلب ميزان بيانات الفروع ولن يكتب أي كمية في سلة حتى توافق سلة على صلاحية <span dir="ltr" className="font-mono">branches.read</span>.
                            </p>
                            <p className="mt-2 text-sm font-bold leading-7 text-emerald-800">
                                مخزون ميزان، استلام المشتريات، الحجوزات وأوامر تجهيز المخزون تعمل بشكل مستقل ولا تتوقف.
                            </p>
                        </div>
                    </div>
                </section>
                <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
                    <h3 className="font-black text-slate-950">طريقة التفعيل مستقبلًا</h3>
                    <p className="mt-2 text-sm leading-7 text-slate-600">
                        بعد الموافقة وإعادة ربط سلة، نفعّل مفتاح النظام فقط ثم نختبر الربط بالمعاينة قبل أي نشر فعلي.
                    </p>
                    <div className="mt-3 rounded-xl bg-slate-950 px-4 py-3 font-mono text-xs text-white" dir="ltr">
                        MEZAN_SALLA_BRANCH_INVENTORY_SYNC_ENABLED=true
                    </div>
                </section>
            </div>
        );
    }

    return (
        <div className="space-y-5" dir="rtl" data-testid="salla-inventory-sync">
            <section className="rounded-3xl border border-emerald-200 bg-emerald-50 p-5">
                <div className="flex items-start gap-3">
                    <ShieldCheck size={28} className="mt-0.5 shrink-0 text-emerald-700" weight="duotone" />
                    <div>
                        <h2 className="text-lg font-black text-emerald-950">ميزان هو المصدر الرئيسي للمخزون</h2>
                        <p className="mt-1 text-sm leading-6 text-emerald-900">المنتج عام للمتجر ولا يرتبط بفرع. الذي يرتبط بالفرع هو الكمية فقط، وسلة تستقبل نسخة من الكمية المتاحة بعد خصم الحجوزات.</p>
                    </div>
                </div>
            </section>

            {(data.salla_connection?.missing_scopes || []).length > 0 && (
                <section className="rounded-3xl border border-amber-300 bg-amber-50 p-5">
                    <div className="flex items-start gap-3">
                        <WarningCircle size={26} className="mt-0.5 shrink-0 text-amber-700" />
                        <div>
                            <h2 className="font-black text-amber-950">يلزم تحديث صلاحيات ربط سلة قبل المزامنة</h2>
                            <p className="mt-1 text-sm leading-6 text-amber-900">فعّل في تطبيق سلة: <span dir="ltr" className="font-mono">branches.read</span> و <span dir="ltr" className="font-mono">products.read_write</span>، ثم أعد ربط المتجر. لم ينفذ النظام أي كتابة.</p>
                            <div className="mt-2 text-xs font-bold text-amber-800">الناقص: {(data.salla_connection.missing_scopes || []).join("، ")}</div>
                        </div>
                    </div>
                </section>
            )}

            <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div>
                        <h2 className="flex items-center gap-2 text-lg font-black text-slate-950"><Link /> 1. ربط الفروع بالمخازن</h2>
                        <p className="mt-1 text-xs leading-5 text-slate-500">كل فرع سلة يرتبط بمخزن ميزان واحد، وكل مخزن يرتبط بفرع واحد فقط.</p>
                    </div>
                    {data.permissions?.can_manage_mappings && (
                        <button type="button" onClick={saveMappings} disabled={busy === "mappings"} className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-black text-white disabled:opacity-60">
                            {busy === "mappings" ? "جارٍ الحفظ…" : "حفظ الربط"}
                        </button>
                    )}
                </div>
                <div className="mt-4 grid gap-3 lg:grid-cols-2">
                    {(data.salla_branches || []).map((branch) => {
                        const current = mappings.find((row) => row.salla_branch_id === String(branch.id));
                        return (
                            <article key={branch.id} className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                                <div className="mb-3 flex items-center gap-2">
                                    <Warehouse className="text-violet-700" />
                                    <div className="font-black text-slate-950">{branchName(branch)}{branch.is_default ? " — الرئيسي" : ""}</div>
                                </div>
                                <select
                                    value={current?.mezan_warehouse_id || ""}
                                    onChange={(event) => updateMapping(String(branch.id), event.target.value)}
                                    disabled={!data.permissions?.can_manage_mappings}
                                    className={controlClass}
                                >
                                    <option value="">غير مربوط</option>
                                    {(data.mezan_warehouses || []).map((warehouse) => (
                                        <option
                                            key={warehouse.id}
                                            value={warehouse.id}
                                            disabled={usedWarehouses.has(String(warehouse.id)) && current?.mezan_warehouse_id !== String(warehouse.id)}
                                        >
                                            {warehouse.name} — {warehouse.code} {warehouse.city ? `— ${warehouse.city}` : ""}
                                        </option>
                                    ))}
                                </select>
                            </article>
                        );
                    })}
                    {(data.salla_branches || []).length === 0 && <div className="rounded-xl border border-dashed p-6 text-center text-slate-500">لم تُرجع سلة أي فروع.</div>}
                </div>
            </section>

            <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
                    <div>
                        <h2 className="flex items-center gap-2 text-lg font-black text-slate-950"><ArrowsLeftRight /> 2. معاينة الفروقات</h2>
                        <p className="mt-1 text-xs leading-5 text-slate-500">المعاينة تقرأ الكمية الحالية من سلة وتقارنها بالمتاح في ميزان، ولا تنفذ كتابة.</p>
                    </div>
                    <div className="grid gap-2 sm:grid-cols-[240px_auto]">
                        <select value={reasonId} onChange={(event) => setReasonId(event.target.value)} className={controlClass}>
                            <option value="">سبب التعديل</option>
                            {(data.quantity_reasons || []).map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}
                        </select>
                        <button type="button" onClick={makePreview} disabled={busy === "preview" || (data.salla_connection?.missing_scopes || []).length > 0} className="rounded-xl bg-violet-700 px-5 py-3 text-sm font-black text-white disabled:opacity-60">
                            {busy === "preview" ? "جارٍ الحساب…" : "إنشاء معاينة آمنة"}
                        </button>
                    </div>
                </div>

                {preview && (
                    <div className="mt-5 space-y-4">
                        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                            <div className="rounded-xl bg-violet-50 p-3 text-center"><div className="text-xs text-violet-700">ستتغير</div><div className="mt-1 text-2xl font-black text-violet-950">{preview.changes_count || 0}</div></div>
                            <div className="rounded-xl bg-emerald-50 p-3 text-center"><div className="text-xs text-emerald-700">متطابقة</div><div className="mt-1 text-2xl font-black text-emerald-950">{preview.unchanged_count || 0}</div></div>
                            <div className="rounded-xl bg-amber-50 p-3 text-center"><div className="text-xs text-amber-700">تحتاج مراجعة</div><div className="mt-1 text-2xl font-black text-amber-950">{preview.issues_count || 0}</div></div>
                            <div className="rounded-xl bg-slate-50 p-3 text-center"><div className="text-xs text-slate-500">الحالة</div><div className="mt-2 text-sm font-black text-slate-900">{STATUS_LABELS[preview.status] || preview.status}</div></div>
                        </div>

                        {(preview.issues || []).length > 0 && (
                            <div className="rounded-2xl border border-amber-300 bg-amber-50 p-4">
                                <div className="flex items-start gap-2">
                                    <WarningCircle size={22} className="shrink-0 text-amber-700" />
                                    <div>
                                        <div className="font-black text-amber-950">عناصر لن تُنشر لحمايتها من كمية خاطئة</div>
                                        <ul className="mt-2 list-inside list-disc space-y-1 text-xs leading-5 text-amber-900">
                                            {(preview.issues || []).slice(0, 8).map((row, index) => (
                                                <li key={`${row.salla_branch_id}:${row.salla_product_id}:${index}`}>
                                                    {row.name || row.target_key}: {row.code === "variant_stock_not_linked" ? "مخزون خيار غير مربوط باللون/المقاس في سلة" : row.code === "product_variants_not_loaded" ? "تفاصيل خيارات المنتج لم تُحمّل" : "لم يوجد هدف الكمية في سلة"}
                                                </li>
                                            ))}
                                        </ul>
                                    </div>
                                </div>
                                {confirmToken && (
                                    <label className="mt-3 flex items-start gap-2 text-sm font-bold text-amber-950">
                                        <input type="checkbox" checked={acknowledgeIssues} onChange={(event) => setAcknowledgeIssues(event.target.checked)} className="mt-1" />
                                        فهمت أن هذه العناصر ستُستبعد، وسيُنشر فقط المخزون المرتبط بشكل آمن.
                                    </label>
                                )}
                            </div>
                        )}

                        <div className="overflow-x-auto rounded-2xl border border-slate-200">
                            <table className="min-w-full text-right text-sm">
                                <thead className="bg-slate-50 text-xs text-slate-500"><tr><th className="p-3">المنتج</th><th className="p-3">فرع سلة</th><th className="p-3">في سلة</th><th className="p-3">ميزان المتاح</th><th className="p-3">الإجراء</th></tr></thead>
                                <tbody className="divide-y divide-slate-100">
                                    {(preview.rows || []).slice(0, 100).map((row) => (
                                        <tr key={`${row.salla_branch_id}:${row.target_key}`}>
                                            <td className="p-3 font-bold">{row.name}{row.variant_name ? <span className="block text-xs text-violet-700">{row.variant_name}</span> : null}</td>
                                            <td className="p-3 text-slate-600">{row.salla_branch?.name || row.salla_branch_id}</td>
                                            <td className="p-3">{row.remote_unlimited ? "غير محدود" : row.remote_quantity}</td>
                                            <td className="p-3 font-black">{row.desired_unlimited ? "حجز مسبق" : row.desired_quantity}</td>
                                            <td className="p-3">{row.operation === "noop" ? <span className="text-emerald-700">لا تغيير</span> : row.operation === "increment" ? `زيادة ${row.operation_quantity}` : row.operation === "decrement" ? `خفض ${row.operation_quantity}` : "تثبيت السياسة والكمية"}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>

                        {confirmToken && data.permissions?.can_publish && (
                            <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4">
                                <div className="font-black text-rose-950">3. النشر الفعلي إلى سلة</div>
                                <p className="mt-1 text-xs leading-5 text-rose-800">هذا الزر وحده يغيّر كميات سلة. قبل التنفيذ يعيد النظام القراءة، ويلغي المعاينة إذا تغيّر أي رصيد.</p>
                                <button type="button" onClick={publish} disabled={busy === "publish" || (preview.issues_count > 0 && !acknowledgeIssues)} className="mt-3 inline-flex items-center gap-2 rounded-xl bg-rose-700 px-5 py-3 text-sm font-black text-white disabled:opacity-50">
                                    {busy === "publish" ? <SpinnerGap className="animate-spin" /> : <ShieldCheck />} تأكيد ونشر {preview.changes_count || 0} تغيير
                                </button>
                            </div>
                        )}

                        {!confirmToken && ["accepted_pending_verification", "publish_uncertain"].includes(preview.status) && (
                            <button type="button" onClick={() => verify()} disabled={busy === `verify:${preview.id}`} className="inline-flex items-center gap-2 rounded-xl bg-amber-600 px-5 py-3 text-sm font-black text-white disabled:opacity-60">
                                <ArrowCounterClockwise className={busy === `verify:${preview.id}` ? "animate-spin" : ""} /> تحقق الآن من تطبيق سلة
                            </button>
                        )}
                        {preview.status === "verified" && <div className="flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 p-4 font-black text-emerald-900"><CheckCircle /> كميات الفروع في سلة مطابقة للمتاح في ميزان.</div>}
                    </div>
                )}
            </section>

            <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
                <div className="flex items-center justify-between gap-3">
                    <h2 className="font-black text-slate-950">سجل المزامنة</h2>
                    <button type="button" onClick={() => load()} className="rounded-xl border px-3 py-2 text-xs font-black text-slate-600"><ArrowCounterClockwise className="ml-1 inline" /> تحديث</button>
                </div>
                <div className="mt-3 space-y-2">
                    {(data.recent_runs || []).map((run) => (
                        <div key={run.id} className="flex flex-col gap-2 rounded-xl border border-slate-200 p-3 sm:flex-row sm:items-center sm:justify-between">
                            <div><div className="font-mono text-xs text-slate-500">{run.id}</div><div className="mt-1 text-sm font-black">{STATUS_LABELS[run.status] || run.status}</div></div>
                            <div className="text-xs text-slate-500">{run.changes_count || 0} تغيير · {run.issues_count || 0} مراجعة</div>
                        </div>
                    ))}
                    {(data.recent_runs || []).length === 0 && <div className="rounded-xl bg-slate-50 p-4 text-sm text-slate-500">لا توجد عمليات سابقة.</div>}
                </div>
            </section>
        </div>
    );
}
