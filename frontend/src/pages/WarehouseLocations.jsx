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

const FALLBACK_CATALOG = {
    default_country: "السعودية",
    default_city: "الرياض",
    countries: [
        { name: "السعودية", default_city: "الرياض", cities: ["الرياض", "جدة", "مكة المكرمة", "المدينة المنورة", "الدمام", "الخبر", "الطائف"] },
        { name: "الإمارات", default_city: "دبي", cities: ["دبي", "أبوظبي", "الشارقة", "عجمان", "رأس الخيمة", "الفجيرة", "أم القيوين"] },
        { name: "قطر", default_city: "الدوحة", cities: ["الدوحة", "الريان", "الوكرة", "الخــور", "لوسيل", "أم صلال"] },
    ],
};

const emptyWarehouse = {
    name: "",
    country: "السعودية",
    city: "الرياض",
    district: "",
    street: "",
    is_primary: true,
};

const emptyCabinet = {
    cabinet_name: "",
    length: 4,
    width: 6,
    purpose: "temporary_staging",
    max_items_per_location: "",
};

function Field({ label, hint, children }) {
    return (
        <label className="block">
            <span className="mb-1 block text-xs font-bold text-slate-600">{label}</span>
            {children}
            {hint && <span className="mt-1 block text-[11px] text-slate-400">{hint}</span>}
        </label>
    );
}

function Input(props) {
    return <input {...props} className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-brand" />;
}

function Select(props) {
    return <select {...props} className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-brand" />;
}

export default function WarehouseLocations() {
    const [catalog, setCatalog] = useState(FALLBACK_CATALOG);
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
    const countryRow = useMemo(
        () => catalog.countries.find((row) => row.name === warehouseForm.country) || catalog.countries[0],
        [catalog, warehouseForm.country],
    );
    const nextCabinetNumber = useMemo(() => {
        const values = (detail?.cabinets || []).map((row) => Number(row.cabinet_number || row.code)).filter(Number.isFinite);
        return values.length ? Math.max(...values) + 1 : 1;
    }, [detail]);

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
        Promise.all([
            api.get("/warehouse-locations-v2/catalog").then((res) => setCatalog(res.data || FALLBACK_CATALOG)).catch(() => null),
            loadWarehouses(),
        ]).catch(() => toast.error("تعذر تحميل بيانات المستودعات"));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        loadDetail(selectedId).catch(() => toast.error("تعذر تحميل تفاصيل المستودع"));
    }, [selectedId]);

    const changeCountry = (country) => {
        const row = catalog.countries.find((item) => item.name === country);
        setWarehouseForm((current) => ({
            ...current,
            country,
            city: row?.default_city || row?.cities?.[0] || "",
        }));
    };

    const createWarehouse = async (event) => {
        event.preventDefault();
        setLoading(true);
        try {
            const res = await api.post("/warehouse-locations-v2/warehouses", warehouseForm);
            toast.success(`تم إنشاء المستودع رقم ${res.data.warehouse_number} في ${res.data.city}`);
            setWarehouseForm({ ...emptyWarehouse, country: catalog.default_country, city: catalog.default_city });
            await loadWarehouses();
            setSelectedId(res.data.id);
        } catch (err) {
            const code = err?.response?.data?.detail?.code;
            toast.error(code === "city_not_in_country" ? "المدينة لا تتبع الدولة المحددة" : "تعذر إنشاء المستودع");
        } finally {
            setLoading(false);
        }
    };

    const buildCabinetPayload = () => ({
        ...cabinetForm,
        warehouse_id: selectedId,
        length: Number(cabinetForm.length),
        width: Number(cabinetForm.width),
        max_items_per_location: cabinetForm.max_items_per_location
            ? Number(cabinetForm.max_items_per_location)
            : null,
    });

    const previewCabinet = () => {
        if (!selectedId || !selected) return toast.warning("اختر مستودعًا أولًا");
        const total = Number(cabinetForm.length) * Number(cabinetForm.width);
        const prefix = `${selected.code}-${nextCabinetNumber}`;
        setPreview({
            total_locations: total,
            cabinet_number: nextCabinetNumber,
            first_code: `${prefix}-001`,
            last_code: `${prefix}-${String(total).padStart(3, "0")}`,
        });
    };

    const createCabinet = async (event) => {
        event.preventDefault();
        if (!selectedId) return toast.warning("اختر مستودعًا أولًا");
        setLoading(true);
        try {
            const res = await api.post("/warehouse-locations-v2/cabinets", buildCabinetPayload());
            toast.success(`تم إنشاء الدولاب رقم ${res.data.cabinet.cabinet_number}`);
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
                <h1 className="text-2xl font-extrabold text-slate-900">إدارة المستودعات</h1>
                <p className="mt-1 text-sm text-slate-500">الأرقام والرموز تُنشأ تلقائيًا؛ كل مدينة تبدأ مستودعاتها من 1، وكل مستودع تبدأ دواليبه من 1.</p>
            </div>

            <div className="grid gap-5 xl:grid-cols-2">
                <form onSubmit={createWarehouse} className="rounded-xl border bg-white p-5">
                    <h2 className="mb-4 text-lg font-extrabold">إضافة مستودع</h2>
                    <div className="grid gap-3 md:grid-cols-2">
                        <Field label="اسم المستودع"><Input required value={warehouseForm.name} onChange={(e) => setWarehouseForm({ ...warehouseForm, name: e.target.value })} /></Field>
                        <Field label="الدولة">
                            <Select value={warehouseForm.country} onChange={(e) => changeCountry(e.target.value)}>
                                {catalog.countries.map((row) => <option key={row.name} value={row.name}>{row.name}</option>)}
                            </Select>
                        </Field>
                        <Field label="المدينة">
                            <Select value={warehouseForm.city} onChange={(e) => setWarehouseForm({ ...warehouseForm, city: e.target.value })}>
                                {(countryRow?.cities || []).map((city) => <option key={city} value={city}>{city}</option>)}
                            </Select>
                        </Field>
                        <Field label="رقم المستودع" hint="يُحدد تلقائيًا داخل المدينة"><Input disabled value="تلقائي — يبدأ من 1" /></Field>
                        <Field label="الحي"><Input required value={warehouseForm.district} onChange={(e) => setWarehouseForm({ ...warehouseForm, district: e.target.value })} /></Field>
                        <Field label="الشارع"><Input required value={warehouseForm.street} onChange={(e) => setWarehouseForm({ ...warehouseForm, street: e.target.value })} /></Field>
                    </div>
                    <button disabled={loading} className="mt-4 rounded-lg bg-brand px-4 py-2 text-sm font-bold text-white disabled:opacity-50">إنشاء المستودع</button>
                </form>

                <form onSubmit={createCabinet} className="rounded-xl border bg-white p-5">
                    <h2 className="mb-4 text-lg font-extrabold">إضافة دولاب</h2>
                    <Field label="المستودع">
                        <Select value={selectedId} onChange={(e) => setSelectedId(e.target.value)}>
                            <option value="">اختر المستودع</option>
                            {warehouses.map((row) => <option key={row.id} value={row.id}>{row.name} — {row.city} — رقم {row.warehouse_number}</option>)}
                        </Select>
                    </Field>
                    <div className="mt-3 grid gap-3 md:grid-cols-2">
                        <Field label="رقم الدولاب" hint="يُحدد تلقائيًا داخل المستودع"><Input disabled value={selectedId ? `تلقائي — الرقم التالي ${nextCabinetNumber}` : "اختر المستودع"} /></Field>
                        <Field label="اسم الدولاب (اختياري)"><Input value={cabinetForm.cabinet_name} onChange={(e) => setCabinetForm({ ...cabinetForm, cabinet_name: e.target.value })} /></Field>
                        <Field label="عدد الخانات بالطول"><Input required type="number" min="1" max="100" value={cabinetForm.length} onChange={(e) => setCabinetForm({ ...cabinetForm, length: e.target.value })} /></Field>
                        <Field label="عدد الخانات بالعرض"><Input required type="number" min="1" max="100" value={cabinetForm.width} onChange={(e) => setCabinetForm({ ...cabinetForm, width: e.target.value })} /></Field>
                        <Field label="نوع الاستخدام">
                            <Select value={cabinetForm.purpose} onChange={(e) => setCabinetForm({ ...cabinetForm, purpose: e.target.value })}>
                                {PURPOSES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                            </Select>
                        </Field>
                        <Field label="سعة الخانة (اختياري)"><Input type="number" min="1" value={cabinetForm.max_items_per_location} onChange={(e) => setCabinetForm({ ...cabinetForm, max_items_per_location: e.target.value })} /></Field>
                    </div>
                    <div className="mt-4 flex gap-2">
                        <button type="button" onClick={previewCabinet} className="rounded-lg border px-4 py-2 text-sm font-bold">معاينة</button>
                        <button disabled={loading} className="rounded-lg bg-brand px-4 py-2 text-sm font-bold text-white disabled:opacity-50">إنشاء الدولاب والخانات</button>
                    </div>
                    {preview && <div className="mt-4 rounded-lg border bg-slate-50 p-3 text-sm"><p className="font-bold">الدولاب رقم {preview.cabinet_number} — إجمالي الخانات: <span className="num">{preview.total_locations}</span></p><p className="mt-1 text-slate-600">من {preview.first_code} إلى {preview.last_code}</p></div>}
                </form>
            </div>

            <div className="rounded-xl border bg-white p-5">
                <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <h2 className="text-lg font-extrabold">{selected?.name || "المستودع"}</h2>
                        {selected && <p className="text-sm text-slate-500">{[selected.country, selected.city, selected.district, selected.street, `مستودع رقم ${selected.warehouse_number}`].filter(Boolean).join(" — ")}</p>}
                    </div>
                    <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-bold">عدد الدواليب: {detail?.cabinet_count || 0}</span>
                </div>
                {!detail?.cabinets?.length ? (
                    <div className="rounded-lg border border-dashed p-8 text-center text-sm text-slate-500">لا توجد دواليب بعد.</div>
                ) : (
                    <div className="grid gap-4 lg:grid-cols-2">
                        {detail.cabinets.map((cabinet) => (
                            <div key={cabinet.id} className="rounded-xl border p-4">
                                <div className="flex items-center justify-between"><h3 className="font-extrabold">{cabinet.name} — رقم {cabinet.cabinet_number || cabinet.code}</h3><span className="text-xs font-bold text-slate-500 num">{cabinet.total_locations} خانة</span></div>
                                <p className="mt-1 text-xs text-slate-500 num">{cabinet.length} × {cabinet.width}</p>
                                <div className="mt-3 grid gap-2" style={{ gridTemplateColumns: `repeat(${Math.min(cabinet.width || 1, 12)}, minmax(0, 1fr))` }}>
                                    {Array.from({ length: Math.min(cabinet.total_locations || 0, 120) }, (_, i) => <div key={i} className="flex aspect-square items-center justify-center rounded border bg-emerald-50 text-[10px] font-bold text-emerald-700 num">{String(i + 1).padStart(3, "0")}</div>)}
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
