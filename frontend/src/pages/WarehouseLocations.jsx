import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const PURPOSES = [
    ["temporary_staging", "تجميع مؤقت"],
    ["permanent_storage", "تخزين دائم"],
    ["returns", "مرتجعات"],
    ["damaged", "تالف"],
    ["reserved", "محجوز"],
];

const emptyWarehouse = {
    name: "",
    code: "",
    country: "السعودية",
    city: "",
    district: "",
    street: "",
    warehouse_number: "",
    is_primary: true,
};

const emptyCabinet = {
    cabinet_code: "",
    cabinet_name: "",
    length: 4,
    width: 6,
    purpose: "temporary_staging",
    max_items_per_bin: "",
};

function Field({ label, children }) {
    return (
        <label className="block">
            <span className="mb-1 block text-xs font-bold text-slate-600">{label}</span>
            {children}
        </label>
    );
}

function Input(props) {
    return <input {...props} className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-brand" />;
}

export default function WarehouseLocations() {
    const [warehouses, setWarehouses] = useState([]);
    const [selectedId, setSelectedId] = useState("");
    const [detail, setDetail] = useState(null);
    const [warehouseForm, setWarehouseForm] = useState(emptyWarehouse);
    const [cabinetForm, setCabinetForm] = useState(emptyCabinet);
    const [preview, setPreview] = useState(null);
    const [loading, setLoading] = useState(false);

    const selected = useMemo(
        () => warehouses.find((row) => row.id === selectedId) || null,
        [warehouses, selectedId],
    );

    const loadWarehouses = async () => {
        const res = await api.get("/warehouse-locations/warehouses");
        const items = res.data?.items || [];
        setWarehouses(items);
        if (!selectedId && items.length) setSelectedId(items[0].id);
    };

    const loadDetail = async (id) => {
        if (!id) return setDetail(null);
        const res = await api.get(`/warehouse-locations/warehouses/${id}`);
        setDetail(res.data);
    };

    useEffect(() => {
        loadWarehouses().catch(() => toast.error("تعذر تحميل المستودعات"));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        loadDetail(selectedId).catch(() => toast.error("تعذر تحميل تفاصيل المستودع"));
    }, [selectedId]);

    const createWarehouse = async (e) => {
        e.preventDefault();
        setLoading(true);
        try {
            const res = await api.post("/warehouse-locations/warehouses", warehouseForm);
            toast.success("تم إنشاء المستودع");
            setWarehouseForm(emptyWarehouse);
            await loadWarehouses();
            setSelectedId(res.data.id);
        } catch (err) {
            toast.error(err?.response?.data?.detail?.message || "تعذر إنشاء المستودع");
        } finally {
            setLoading(false);
        }
    };

    const previewCabinet = async () => {
        if (!selectedId) return toast.warning("اختر مستودعًا أولًا");
        try {
            const payload = {
                ...cabinetForm,
                warehouse_id: selectedId,
                length: Number(cabinetForm.length),
                width: Number(cabinetForm.width),
                max_items_per_bin: cabinetForm.max_items_per_bin ? Number(cabinetForm.max_items_per_bin) : null,
            };
            const res = await api.post("/warehouse-locations/cabinets/preview", payload);
            setPreview(res.data);
        } catch (err) {
            toast.error(err?.response?.data?.detail?.message || "تعذر معاينة الدولاب");
        }
    };

    const createCabinet = async (e) => {
        e.preventDefault();
        if (!selectedId) return toast.warning("اختر مستودعًا أولًا");
        setLoading(true);
        try {
            const payload = {
                ...cabinetForm,
                warehouse_id: selectedId,
                length: Number(cabinetForm.length),
                width: Number(cabinetForm.width),
                max_items_per_bin: cabinetForm.max_items_per_bin ? Number(cabinetForm.max_items_per_bin) : null,
            };
            await api.post("/warehouse-locations/cabinets/generate", payload);
            toast.success("تم إنشاء الدولاب والخانات");
            setCabinetForm(emptyCabinet);
            setPreview(null);
            await loadDetail(selectedId);
        } catch (err) {
            toast.error(err?.response?.data?.detail?.message || "تعذر إنشاء الدولاب");
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="space-y-5" dir="rtl">
            <div>
                <h1 className="text-2xl font-extrabold text-slate-900">مواقع المستودع</h1>
                <p className="mt-1 text-sm text-slate-500">أنشئ المستودع مرة واحدة، ثم أضف تحته أي عدد من الدواليب وحدد الطول × العرض.</p>
            </div>

            <div className="grid gap-5 xl:grid-cols-2">
                <form onSubmit={createWarehouse} className="rounded-xl border bg-white p-5">
                    <h2 className="mb-4 text-lg font-extrabold">إضافة مستودع</h2>
                    <div className="grid gap-3 md:grid-cols-2">
                        <Field label="اسم المستودع"><Input required value={warehouseForm.name} onChange={(e) => setWarehouseForm({ ...warehouseForm, name: e.target.value })} /></Field>
                        <Field label="رمز المستودع"><Input required placeholder="WH01" value={warehouseForm.code} onChange={(e) => setWarehouseForm({ ...warehouseForm, code: e.target.value })} /></Field>
                        <Field label="الدولة"><Input required value={warehouseForm.country} onChange={(e) => setWarehouseForm({ ...warehouseForm, country: e.target.value })} /></Field>
                        <Field label="المدينة"><Input required value={warehouseForm.city} onChange={(e) => setWarehouseForm({ ...warehouseForm, city: e.target.value })} /></Field>
                        <Field label="الحي"><Input required value={warehouseForm.district} onChange={(e) => setWarehouseForm({ ...warehouseForm, district: e.target.value })} /></Field>
                        <Field label="الشارع"><Input required value={warehouseForm.street} onChange={(e) => setWarehouseForm({ ...warehouseForm, street: e.target.value })} /></Field>
                        <Field label="رقم المستودع"><Input required value={warehouseForm.warehouse_number} onChange={(e) => setWarehouseForm({ ...warehouseForm, warehouse_number: e.target.value })} /></Field>
                    </div>
                    <button disabled={loading} className="mt-4 rounded-lg bg-brand px-4 py-2 text-sm font-bold text-white disabled:opacity-50">إنشاء المستودع</button>
                </form>

                <form onSubmit={createCabinet} className="rounded-xl border bg-white p-5">
                    <h2 className="mb-4 text-lg font-extrabold">إضافة دولاب</h2>
                    <Field label="المستودع">
                        <select value={selectedId} onChange={(e) => setSelectedId(e.target.value)} className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm">
                            <option value="">اختر المستودع</option>
                            {warehouses.map((row) => <option key={row.id} value={row.id}>{row.name} — {row.code}</option>)}
                        </select>
                    </Field>
                    <div className="mt-3 grid gap-3 md:grid-cols-2">
                        <Field label="رمز الدولاب"><Input required placeholder="A01" value={cabinetForm.cabinet_code} onChange={(e) => setCabinetForm({ ...cabinetForm, cabinet_code: e.target.value })} /></Field>
                        <Field label="اسم الدولاب"><Input value={cabinetForm.cabinet_name} onChange={(e) => setCabinetForm({ ...cabinetForm, cabinet_name: e.target.value })} /></Field>
                        <Field label="عدد الخانات بالطول"><Input required type="number" min="1" max="100" value={cabinetForm.length} onChange={(e) => setCabinetForm({ ...cabinetForm, length: e.target.value })} /></Field>
                        <Field label="عدد الخانات بالعرض"><Input required type="number" min="1" max="100" value={cabinetForm.width} onChange={(e) => setCabinetForm({ ...cabinetForm, width: e.target.value })} /></Field>
                        <Field label="نوع الاستخدام">
                            <select value={cabinetForm.purpose} onChange={(e) => setCabinetForm({ ...cabinetForm, purpose: e.target.value })} className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm">
                                {PURPOSES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                            </select>
                        </Field>
                        <Field label="سعة الخانة (اختياري)"><Input type="number" min="1" value={cabinetForm.max_items_per_bin} onChange={(e) => setCabinetForm({ ...cabinetForm, max_items_per_bin: e.target.value })} /></Field>
                    </div>
                    <div className="mt-4 flex gap-2">
                        <button type="button" onClick={previewCabinet} className="rounded-lg border px-4 py-2 text-sm font-bold">معاينة</button>
                        <button disabled={loading} className="rounded-lg bg-brand px-4 py-2 text-sm font-bold text-white disabled:opacity-50">إنشاء الدولاب والخانات</button>
                    </div>
                    {preview && (
                        <div className="mt-4 rounded-lg border bg-slate-50 p-3 text-sm">
                            <p className="font-bold">إجمالي الخانات: <span className="num">{preview.total_locations}</span></p>
                            <p className="mt-1 text-slate-600">من {preview.first_code} إلى {preview.last_code}</p>
                        </div>
                    )}
                </form>
            </div>

            <div className="rounded-xl border bg-white p-5">
                <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <h2 className="text-lg font-extrabold">{selected?.name || "المستودع"}</h2>
                        {selected && <p className="text-sm text-slate-500">{[selected.country, selected.city, selected.district, selected.street, selected.warehouse_number].filter(Boolean).join(" — ")}</p>}
                    </div>
                    <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-bold">عدد الدواليب: {detail?.cabinet_count || 0}</span>
                </div>
                {!detail?.cabinets?.length ? (
                    <div className="rounded-lg border border-dashed p-8 text-center text-sm text-slate-500">لا توجد دواليب بعد.</div>
                ) : (
                    <div className="grid gap-4 lg:grid-cols-2">
                        {detail.cabinets.map((cabinet) => (
                            <div key={cabinet.id} className="rounded-xl border p-4">
                                <div className="flex items-center justify-between">
                                    <h3 className="font-extrabold">{cabinet.name} — {cabinet.code}</h3>
                                    <span className="text-xs font-bold text-slate-500 num">{cabinet.total_locations} خانة</span>
                                </div>
                                <p className="mt-1 text-xs text-slate-500 num">{cabinet.length} × {cabinet.width}</p>
                                <div className="mt-3 grid gap-2" style={{ gridTemplateColumns: `repeat(${Math.min(cabinet.width || 1, 12)}, minmax(0, 1fr))` }}>
                                    {Array.from({ length: Math.min(cabinet.total_locations || 0, 120) }, (_, i) => (
                                        <div key={i} className="flex aspect-square items-center justify-center rounded border bg-emerald-50 text-[10px] font-bold text-emerald-700 num">{String(i + 1).padStart(3, "0")}</div>
                                    ))}
                                </div>
                                {(cabinet.total_locations || 0) > 120 && <p className="mt-2 text-xs text-slate-500">تم اختصار المعاينة إلى أول 120 خانة.</p>}
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}
