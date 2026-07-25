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

const CODE39 = {
    "0": "nnnwwnwnn", "1": "wnnwnnnnw", "2": "nnwwnnnnw", "3": "wnwwnnnnn",
    "4": "nnnwwnnnw", "5": "wnnwwnnnn", "6": "nnwwwnnnn", "7": "nnnwnnwnw",
    "8": "wnnwnnwnn", "9": "nnwwnnwnn", A: "wnnnnwnnw", B: "nnwnnwnnw",
    C: "wnwnnwnnn", D: "nnnnwwnnw", E: "wnnnwwnnn", F: "nnwnwwnnn",
    G: "nnnnnwwnw", H: "wnnnnwwnn", I: "nnwnnwwnn", J: "nnnnwwwnn",
    K: "wnnnnnnww", L: "nnwnnnnww", M: "wnwnnnnwn", N: "nnnnwnnww",
    O: "wnnnwnnwn", P: "nnwnwnnwn", Q: "nnnnnnwww", R: "wnnnnnwwn",
    S: "nnwnnnwwn", T: "nnnnwnwwn", U: "wwnnnnnnw", V: "nwwnnnnnw",
    W: "wwwnnnnnn", X: "nwnnwnnnw", Y: "wwnnwnnnn", Z: "nwwnwnnnn",
    "-": "nwnnnnwnw", ".": "wwnnnnwnn", " ": "nwwnnnwnn", "*": "nwnnwnwnn",
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

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function code39Svg(rawValue) {
    const value = `*${String(rawValue || "").toUpperCase()}*`;
    const narrow = 2;
    const wide = 6;
    const gap = 2;
    const height = 84;
    let x = 10;
    const bars = [];
    for (const char of value) {
        const pattern = CODE39[char];
        if (!pattern) continue;
        pattern.split("").forEach((unit, index) => {
            const width = unit === "w" ? wide : narrow;
            if (index % 2 === 0) bars.push(`<rect x="${x}" y="4" width="${width}" height="${height}" />`);
            x += width;
        });
        x += gap;
    }
    return `<svg class="barcode" viewBox="0 0 ${x + 10} 96" role="img" aria-label="باركود ${escapeHtml(rawValue)}" xmlns="http://www.w3.org/2000/svg"><g fill="#000">${bars.join("")}</g></svg>`;
}

function printablePage({ eyebrow, value, subtitle, code, barcode = false }) {
    return `
        <section class="print-page">
            <div class="brand">MEZAN OS</div>
            <div class="eyebrow">${escapeHtml(eyebrow)}</div>
            <div class="big-value">${escapeHtml(value)}</div>
            ${subtitle ? `<div class="subtitle">${escapeHtml(subtitle)}</div>` : ""}
            ${barcode && code ? code39Svg(code) : ""}
            ${code ? `<div class="code">${escapeHtml(code)}</div>` : ""}
        </section>
    `;
}

function printCabinetPack(warehouse, cabinet) {
    if (!warehouse || !cabinet) return;
    const cabinetNumber = cabinet.cabinet_number || cabinet.code;
    const total = Number(cabinet.total_locations || 0);
    const width = Number(cabinet.width || 0);
    const warehouseLabel = `${warehouse.name} — ${warehouse.city} — مستودع رقم ${warehouse.warehouse_number}`;

    const cover = printablePage({
        eyebrow: "رقم الدولاب",
        value: cabinetNumber,
        subtitle: warehouseLabel,
        code: `${warehouse.code}-${cabinetNumber}`,
    });
    const columnPages = Array.from({ length: width }, (_, index) => printablePage({
        eyebrow: `الدولاب رقم ${cabinetNumber}`,
        value: `عمود ${index + 1}`,
        subtitle: warehouseLabel,
        code: `${warehouse.code}-${cabinetNumber}-C${String(index + 1).padStart(2, "0")}`,
    })).join("");
    const locationPages = Array.from({ length: total }, (_, index) => {
        const locationNumber = String(index + 1).padStart(3, "0");
        const locationCode = `${warehouse.code}-${cabinetNumber}-${locationNumber}`;
        return printablePage({
            eyebrow: `الدولاب رقم ${cabinetNumber} — رقم الخانة`,
            value: locationNumber,
            subtitle: "امسح هذا الباركود قبل وضع أي منتج في الخانة",
            code: locationCode,
            barcode: true,
        });
    }).join("");

    const printWindow = window.open("", "_blank");
    if (!printWindow) {
        toast.error("اسمح بفتح النوافذ المنبثقة لطباعة ملف الدولاب");
        return;
    }
    printWindow.document.write(`<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8" />
<title>دولاب ${escapeHtml(cabinetNumber)} — ${escapeHtml(warehouse.name)}</title>
<style>
@page { size: A4 portrait; margin: 0; }
* { box-sizing: border-box; }
body { margin: 0; font-family: Arial, Tahoma, sans-serif; color: #0f172a; background: white; }
.print-page { position: relative; width: 210mm; height: 297mm; page-break-after: always; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 18mm; text-align: center; border: 8mm solid #0f766e; }
.print-page:last-child { page-break-after: auto; }
.brand { position: absolute; top: 14mm; font-size: 20pt; font-weight: 900; letter-spacing: 2px; color: #0f766e; }
.eyebrow { font-size: 26pt; font-weight: 800; margin-bottom: 8mm; }
.big-value { font-size: 96pt; line-height: 1; font-weight: 900; direction: ltr; }
.subtitle { margin-top: 10mm; font-size: 18pt; font-weight: 700; max-width: 170mm; }
.code { margin-top: 6mm; font: 900 22pt monospace; direction: ltr; letter-spacing: 1px; }
.barcode { width: 165mm; height: 34mm; margin-top: 12mm; }
@media screen { body { background: #e2e8f0; } .print-page { margin: 10px auto; background: white; box-shadow: 0 4px 18px rgba(15,23,42,.18); } }
</style></head><body>${cover}${columnPages}${locationPages}<script>window.onload=()=>window.print();</script></body></html>`);
    printWindow.document.close();
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
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                    <h3 className="font-extrabold">{cabinet.name} — رقم {cabinet.cabinet_number || cabinet.code}</h3>
                                    <span className="text-xs font-bold text-slate-500 num">{cabinet.total_locations} خانة</span>
                                </div>
                                <p className="mt-1 text-xs text-slate-500 num">{cabinet.length} × {cabinet.width}</p>
                                <button type="button" onClick={() => printCabinetPack(selected, cabinet)} className="mt-3 rounded-lg border border-teal-700 px-3 py-2 text-xs font-extrabold text-teal-800 hover:bg-teal-50">
                                    طباعة ملف الدولاب والباركودات
                                </button>
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
