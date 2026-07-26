import { useEffect, useMemo, useState } from "react";
import {
    ArrowRight,
    CheckCircle,
    Cube,
    GearSix,
    MapPin,
    Package,
    Plus,
    Printer,
    Trash,
    Warehouse,
    Wrench,
    X,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import api from "../lib/api";

const PURPOSES = [
    ["permanent_storage", "تخزين دائم"],
    ["temporary_staging", "تجميع مؤقت"],
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

const FALLBACK_CAPABILITIES = [
    { value: "cabinets", label: "دواليب وخانات" },
    { value: "workstations", label: "محطات عمل" },
    { value: "assembly", label: "تركيب" },
    { value: "engraving", label: "نحت ونقش" },
    { value: "packing", label: "تغليف" },
    { value: "shipping_labeling", label: "شحن وعنونة" },
    { value: "quality_control", label: "فحص جودة" },
    { value: "waiting_areas", label: "مناطق انتظار" },
    { value: "equipment", label: "أجهزة ومعدات" },
    { value: "production_line", label: "خط إنتاج" },
    { value: "office", label: "إدارة ومكاتب" },
    { value: "worker_housing", label: "سكن عمال" },
    { value: "returns", label: "مرتجعات" },
];

const FALLBACK_TEMPLATES = {
    storage: ["cabinets"],
    assembly: ["cabinets", "workstations", "assembly", "quality_control"],
    engraving: ["cabinets", "workstations", "engraving", "quality_control"],
    shipping: ["cabinets", "workstations", "packing", "shipping_labeling", "waiting_areas"],
    production: ["cabinets", "workstations", "equipment", "production_line", "quality_control"],
    administration: ["cabinets", "office"],
};

const TEMPLATE_LABELS = {
    storage: "تخزين",
    assembly: "تركيب",
    engraving: "نحت ونقش",
    shipping: "شحن وعنونة",
    production: "خط إنتاج",
    administration: "إدارة",
    custom: "مخصص",
};

const EMPTY_BRANCH = {
    name: "",
    country: "السعودية",
    city: "الرياض",
    district: "",
    street: "",
    is_primary: true,
};

const EMPTY_SECTION = {
    name: "",
    template: "storage",
    capabilities: ["cabinets"],
    notes: "",
};

const EMPTY_CABINET = {
    cabinet_name: "",
    columns: 6,
    slots_per_column: 4,
    purpose: "permanent_storage",
    max_items_per_location: "",
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

function Field({ label, hint, children }) {
    return (
        <label className="block">
            <span className="mb-1 block text-xs font-extrabold text-slate-700">{label}</span>
            {children}
            {hint && <span className="mt-1 block text-[11px] leading-5 text-slate-400">{hint}</span>}
        </label>
    );
}

function Input(props) {
    return <input {...props} className={`w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-100 ${props.className || ""}`} />;
}

function Select(props) {
    return <select {...props} className={`w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-100 ${props.className || ""}`} />;
}

function Modal({ title, subtitle, onClose, children, maxWidth = "max-w-2xl" }) {
    return (
        <div className="fixed inset-0 z-[90] flex items-center justify-center bg-slate-950/50 p-4" onMouseDown={onClose}>
            <div className={`max-h-[92vh] w-full ${maxWidth} overflow-y-auto rounded-3xl border border-slate-200 bg-white shadow-2xl`} onMouseDown={(event) => event.stopPropagation()} dir="rtl">
                <div className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-slate-100 bg-white/95 px-5 py-4 backdrop-blur">
                    <div>
                        <h2 className="text-xl font-black text-slate-950">{title}</h2>
                        {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
                    </div>
                    <button type="button" onClick={onClose} className="rounded-xl border border-slate-200 p-2 text-slate-500 hover:bg-slate-50" aria-label="إغلاق">
                        <X size={19} weight="bold" />
                    </button>
                </div>
                <div className="p-5">{children}</div>
            </div>
        </div>
    );
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
    return `<svg class="barcode" viewBox="0 0 ${x + 10} 96" xmlns="http://www.w3.org/2000/svg"><g fill="#000">${bars.join("")}</g></svg>`;
}

function printablePage({ eyebrow, value, subtitle, code, barcode = false }) {
    return `<section class="print-page"><div class="brand">MEZAN OS</div><div class="eyebrow">${escapeHtml(eyebrow)}</div><div class="big-value">${escapeHtml(value)}</div>${subtitle ? `<div class="subtitle">${escapeHtml(subtitle)}</div>` : ""}${barcode && code ? code39Svg(code) : ""}${code ? `<div class="code">${escapeHtml(code)}</div>` : ""}</section>`;
}

function printCabinetPack(branch, section, cabinet) {
    if (!branch || !section || !cabinet) return;
    const cabinetNumber = cabinet.cabinet_number || cabinet.code;
    const total = Number(cabinet.total_locations || 0);
    const columns = Number(cabinet.width || 0);
    const baseCode = `${branch.code}-${cabinet.code}`;
    const branchLabel = `${branch.name} — ${branch.city} — قسم ${section.section_number || section.room_number} — ${section.name}`;
    const cover = printablePage({ eyebrow: "رقم الدولاب", value: cabinetNumber, subtitle: branchLabel, code: baseCode });
    const columnPages = Array.from({ length: columns }, (_, index) => printablePage({
        eyebrow: `الدولاب ${cabinetNumber}`,
        value: `عمود ${index + 1}`,
        subtitle: branchLabel,
        code: `${baseCode}-C${String(index + 1).padStart(2, "0")}`,
    })).join("");
    const locationPages = Array.from({ length: total }, (_, index) => {
        const number = String(index + 1).padStart(3, "0");
        const code = `${baseCode}-${number}`;
        return printablePage({ eyebrow: `الدولاب ${cabinetNumber} — الخانة`, value: number, subtitle: "امسح الباركود قبل وضع المنتج", code, barcode: true });
    }).join("");
    const printWindow = window.open("", "_blank");
    if (!printWindow) return toast.error("اسمح بفتح النوافذ المنبثقة لطباعة الملصقات");
    printWindow.document.write(`<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"/><title>${escapeHtml(branch.name)} — ${escapeHtml(String(cabinetNumber))}</title><style>@page{size:A4 portrait;margin:0}*{box-sizing:border-box}body{margin:0;font-family:Arial,Tahoma,sans-serif;color:#0f172a;background:#fff}.print-page{position:relative;width:210mm;height:297mm;page-break-after:always;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:18mm;text-align:center;border:8mm solid #5b21b6}.print-page:last-child{page-break-after:auto}.brand{position:absolute;top:14mm;font-size:20pt;font-weight:900;letter-spacing:2px;color:#5b21b6}.eyebrow{font-size:26pt;font-weight:800;margin-bottom:8mm}.big-value{font-size:96pt;line-height:1;font-weight:900;direction:ltr}.subtitle{margin-top:10mm;font-size:18pt;font-weight:700;max-width:170mm}.code{margin-top:6mm;font:900 22pt monospace;direction:ltr;letter-spacing:1px}.barcode{width:165mm;height:34mm;margin-top:12mm}@media screen{body{background:#e2e8f0}.print-page{margin:10px auto;background:#fff;box-shadow:0 4px 18px rgba(15,23,42,.18)}}</style></head><body>${cover}${columnPages}${locationPages}<script>window.onload=()=>window.print();</script></body></html>`);
    printWindow.document.close();
}

function Step({ number, title, subtitle, done, active }) {
    return (
        <div className={`rounded-2xl border p-3 transition ${active ? "border-violet-300 bg-violet-50" : done ? "border-emerald-200 bg-emerald-50" : "border-slate-200 bg-white"}`}>
            <div className="flex items-center gap-3">
                <div className={`flex h-8 w-8 items-center justify-center rounded-full text-sm font-black ${done ? "bg-emerald-600 text-white" : active ? "bg-violet-700 text-white" : "bg-slate-100 text-slate-500"}`}>
                    {done ? <CheckCircle size={18} weight="fill" /> : number}
                </div>
                <div>
                    <p className="text-sm font-black text-slate-900">{title}</p>
                    <p className="text-[11px] text-slate-500">{subtitle}</p>
                </div>
            </div>
        </div>
    );
}

function CapabilityPills({ values, options }) {
    return (
        <div className="flex flex-wrap gap-1.5">
            {(values || []).map((value) => (
                <span key={value} className="rounded-full bg-violet-50 px-2.5 py-1 text-[10px] font-bold text-violet-800">
                    {options.find((item) => item.value === value)?.label || value}
                </span>
            ))}
        </div>
    );
}

export default function WarehouseHierarchyWorkspace() {
    const [catalog, setCatalog] = useState(FALLBACK_CATALOG);
    const [capabilityOptions, setCapabilityOptions] = useState(FALLBACK_CAPABILITIES);
    const [templates, setTemplates] = useState(FALLBACK_TEMPLATES);
    const [branches, setBranches] = useState([]);
    const [branchId, setBranchId] = useState("");
    const [sections, setSections] = useState([]);
    const [sectionId, setSectionId] = useState("");
    const [cabinetId, setCabinetId] = useState("");
    const [dialog, setDialog] = useState(null);
    const [loading, setLoading] = useState(false);
    const [branchForm, setBranchForm] = useState(EMPTY_BRANCH);
    const [sectionForm, setSectionForm] = useState(EMPTY_SECTION);
    const [cabinetForm, setCabinetForm] = useState(EMPTY_CABINET);
    const [resetConfirmation, setResetConfirmation] = useState("");

    const selectedBranch = useMemo(() => branches.find((row) => row.id === branchId) || null, [branches, branchId]);
    const selectedSection = useMemo(() => sections.find((row) => row.id === sectionId) || null, [sections, sectionId]);
    const selectedCabinet = useMemo(() => selectedSection?.cabinets?.find((row) => row.id === cabinetId) || null, [selectedSection, cabinetId]);
    const allCabinets = useMemo(() => sections.flatMap((row) => row.cabinets || []), [sections]);
    const totalLocations = useMemo(() => allCabinets.reduce((sum, row) => sum + Number(row.total_locations || 0), 0), [allCabinets]);
    const countryRow = useMemo(() => catalog.countries.find((row) => row.name === branchForm.country) || catalog.countries[0], [branchForm.country, catalog.countries]);
    const cabinetTotal = Number(cabinetForm.columns || 0) * Number(cabinetForm.slots_per_column || 0);

    async function loadBranches(preferredId = "") {
        const response = await api.get("/warehouse-locations/warehouses");
        const items = response.data?.items || [];
        setBranches(items);
        const nextId = preferredId && items.some((row) => row.id === preferredId) ? preferredId : "";
        setBranchId(nextId);
        if (!nextId) {
            setSections([]);
            setSectionId("");
            setCabinetId("");
        }
        return nextId;
    }

    async function loadSections(id, preferredSectionId = "", preferredCabinetId = "") {
        if (!id) {
            setSections([]);
            return;
        }
        const response = await api.get(`/warehouse-locations-v2/warehouses/${id}/sections`);
        const items = response.data?.items || [];
        setSections(items);
        const nextSectionId = preferredSectionId && items.some((row) => row.id === preferredSectionId) ? preferredSectionId : "";
        setSectionId(nextSectionId);
        const section = items.find((row) => row.id === nextSectionId);
        const nextCabinetId = preferredCabinetId && section?.cabinets?.some((row) => row.id === preferredCabinetId) ? preferredCabinetId : "";
        setCabinetId(nextCabinetId);
    }

    useEffect(() => {
        Promise.all([
            api.get("/warehouse-locations-v2/catalog").then((response) => setCatalog(response.data || FALLBACK_CATALOG)).catch(() => null),
            api.get("/warehouse-locations-v2/section-capabilities").then((response) => {
                setCapabilityOptions(response.data?.items || FALLBACK_CAPABILITIES);
                setTemplates(response.data?.recommended || FALLBACK_TEMPLATES);
            }).catch(() => null),
            loadBranches(),
        ]).catch(() => toast.error("تعذر تحميل الفروع والمخازن"));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    function chooseBranch(id) {
        setBranchId(id);
        setSectionId("");
        setCabinetId("");
        loadSections(id).catch(() => toast.error("تعذر تحميل أقسام الفرع"));
    }

    function chooseSection(id) {
        setSectionId(id);
        setCabinetId("");
    }

    function changeCountry(country) {
        const row = catalog.countries.find((item) => item.name === country);
        setBranchForm((current) => ({ ...current, country, city: row?.default_city || row?.cities?.[0] || "" }));
    }

    function applyTemplate(value) {
        const next = value === "custom" ? sectionForm.capabilities : templates[value] || ["cabinets"];
        setSectionForm((current) => ({ ...current, template: value, capabilities: Array.from(new Set(["cabinets", ...next])) }));
    }

    function toggleCapability(value) {
        if (value === "cabinets") return;
        setSectionForm((current) => ({
            ...current,
            template: "custom",
            capabilities: current.capabilities.includes(value)
                ? current.capabilities.filter((item) => item !== value)
                : [...current.capabilities, value],
        }));
    }

    async function createBranch(event) {
        event.preventDefault();
        setLoading(true);
        try {
            const response = await api.post("/warehouse-locations-v2/warehouses", branchForm);
            setDialog(null);
            setBranchForm({ ...EMPTY_BRANCH, country: catalog.default_country, city: catalog.default_city });
            await loadBranches(response.data.id);
            await loadSections(response.data.id);
            toast.success("تم إنشاء الفرع. الخطوة التالية: أضف قسمًا.");
        } catch (error) {
            const code = error?.response?.data?.detail?.code;
            toast.error(code === "city_not_in_country" ? "المدينة لا تتبع الدولة المحددة" : "تعذر إنشاء الفرع");
        } finally {
            setLoading(false);
        }
    }

    async function createSection(event) {
        event.preventDefault();
        if (!branchId) return toast.warning("اختر الفرع أولًا");
        setLoading(true);
        try {
            const response = await api.post("/warehouse-locations-v2/sections", {
                warehouse_id: branchId,
                name: sectionForm.name,
                capabilities: Array.from(new Set(["cabinets", ...sectionForm.capabilities])),
                notes: sectionForm.notes || null,
            });
            setDialog(null);
            setSectionForm({ ...EMPTY_SECTION, capabilities: templates.storage || ["cabinets"] });
            await loadSections(branchId, response.data.id);
            toast.success("تم إنشاء القسم. الخطوة التالية: أضف دولابًا داخله.");
        } catch (error) {
            toast.error(error?.response?.data?.detail?.message || "تعذر إنشاء القسم");
        } finally {
            setLoading(false);
        }
    }

    async function createCabinet(event) {
        event.preventDefault();
        if (!sectionId) return toast.warning("اختر القسم أولًا");
        setLoading(true);
        try {
            const response = await api.post(`/warehouse-locations-v2/sections/${sectionId}/cabinets`, {
                cabinet_name: cabinetForm.cabinet_name || null,
                length: Number(cabinetForm.slots_per_column),
                width: Number(cabinetForm.columns),
                purpose: cabinetForm.purpose,
                max_items_per_location: cabinetForm.max_items_per_location ? Number(cabinetForm.max_items_per_location) : null,
            });
            setDialog(null);
            setCabinetForm(EMPTY_CABINET);
            await loadSections(branchId, sectionId, response.data.cabinet.id);
            toast.success(`تم إنشاء الدولاب و${response.data.locations_created} خانة تلقائيًا.`);
        } catch (error) {
            toast.error(error?.response?.data?.detail?.message || "تعذر إنشاء الدولاب");
        } finally {
            setLoading(false);
        }
    }

    async function resetWorkspace(event) {
        event.preventDefault();
        if (resetConfirmation !== "حذف") return;
        setLoading(true);
        try {
            await api.post("/warehouse-locations-v2/reset", { confirmation: "حذف" });
            setDialog(null);
            setResetConfirmation("");
            setBranches([]);
            setBranchId("");
            setSections([]);
            setSectionId("");
            setCabinetId("");
            toast.success("تم حذف بيانات التجربة وإعادة ترقيم الفروع والأقسام والدواليب من 1.");
        } catch (error) {
            toast.error(error?.response?.data?.detail?.message || "تعذر بدء النظام من الصفر");
        } finally {
            setLoading(false);
        }
    }

    const address = selectedBranch ? [selectedBranch.country, selectedBranch.city, selectedBranch.district, selectedBranch.street].filter(Boolean).join(" — ") : "";
    const currentStep = !branchId ? 1 : !sectionId ? 2 : !cabinetId ? 3 : 4;

    function renderWorkspace() {
        if (!selectedBranch) {
            return (
                <div className="flex min-h-[520px] flex-col items-center justify-center rounded-3xl border border-dashed border-slate-300 bg-white p-8 text-center">
                    <Warehouse size={64} weight="duotone" className="text-slate-300" />
                    <h2 className="mt-4 text-2xl font-black text-slate-950">ابدأ بالفرع</h2>
                    <p className="mt-2 max-w-lg text-sm leading-7 text-slate-500">البنية المعتمدة ثابتة وواضحة: فرع ثم أقسام، وكل دولاب يجب أن يكون داخل قسم. لا يوجد دولاب مباشر خارج الأقسام.</p>
                    <button type="button" onClick={() => setDialog("branch")} className="mt-5 flex items-center gap-2 rounded-xl bg-violet-700 px-5 py-3 text-sm font-extrabold text-white">
                        <Plus size={18} weight="bold" /> إضافة أول فرع
                    </button>
                </div>
            );
        }

        if (selectedCabinet && selectedSection) {
            return (
                <div className="space-y-4">
                    <div className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
                        <button type="button" onClick={() => setCabinetId("")} className="mb-4 flex items-center gap-1 text-xs font-bold text-violet-700"><ArrowRight size={15} /> الرجوع إلى {selectedSection.name}</button>
                        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                            <div>
                                <p className="text-xs font-extrabold text-violet-600">الخطوة 4 — الخانات</p>
                                <h2 className="mt-1 text-2xl font-black text-slate-950">{selectedCabinet.name || `دولاب ${selectedCabinet.cabinet_number}`}</h2>
                                <p className="num mt-1 text-xs text-slate-400">{selectedBranch.code}-{selectedCabinet.code}</p>
                            </div>
                            <button type="button" onClick={() => printCabinetPack(selectedBranch, selectedSection, selectedCabinet)} className="flex items-center justify-center gap-2 rounded-xl border border-violet-200 bg-violet-50 px-4 py-2.5 text-sm font-extrabold text-violet-800">
                                <Printer size={18} /> طباعة الملصقات والباركودات
                            </button>
                        </div>
                        <div className="mt-5 grid gap-3 sm:grid-cols-4">
                            <div className="rounded-2xl bg-slate-50 p-3"><span className="block text-[11px] text-slate-400">القسم</span><strong>{selectedSection.name}</strong></div>
                            <div className="rounded-2xl bg-slate-50 p-3"><span className="block text-[11px] text-slate-400">الأعمدة</span><strong className="num">{selectedCabinet.width}</strong></div>
                            <div className="rounded-2xl bg-slate-50 p-3"><span className="block text-[11px] text-slate-400">الخانات في العمود</span><strong className="num">{selectedCabinet.length}</strong></div>
                            <div className="rounded-2xl bg-slate-50 p-3"><span className="block text-[11px] text-slate-400">إجمالي الخانات</span><strong className="num">{selectedCabinet.total_locations}</strong></div>
                        </div>
                    </div>
                    <div className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
                        <div className="mb-4 flex items-center justify-between">
                            <div><h3 className="font-black text-slate-950">خريطة الخانات</h3><p className="text-xs text-slate-500">كل خانة تحمل باركودًا مستقلًا ويجب مسحه قبل وضع المنتج.</p></div>
                            <span className="num rounded-full bg-emerald-50 px-3 py-1 text-xs font-bold text-emerald-700">{selectedCabinet.total_locations} خانة</span>
                        </div>
                        <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${Math.min(Number(selectedCabinet.width || 1), 12)}, minmax(0, 1fr))` }}>
                            {Array.from({ length: Math.min(Number(selectedCabinet.total_locations || 0), 240) }, (_, index) => (
                                <div key={index} className="flex aspect-square items-center justify-center rounded-xl border border-emerald-100 bg-emerald-50 text-[10px] font-black text-emerald-700 num">{String(index + 1).padStart(3, "0")}</div>
                            ))}
                        </div>
                    </div>
                </div>
            );
        }

        if (selectedSection) {
            return (
                <div className="space-y-4">
                    <div className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
                        <button type="button" onClick={() => { setSectionId(""); setCabinetId(""); }} className="mb-4 flex items-center gap-1 text-xs font-bold text-violet-700"><ArrowRight size={15} /> الرجوع إلى {selectedBranch.name}</button>
                        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                            <div>
                                <p className="text-xs font-extrabold text-violet-600">الخطوة 3 — دواليب القسم</p>
                                <h2 className="mt-1 text-2xl font-black text-slate-950">{selectedSection.name}</h2>
                                <p className="num mt-1 text-xs text-slate-400">{selectedBranch.code}-{selectedSection.code}</p>
                            </div>
                            <button type="button" onClick={() => { setCabinetForm(EMPTY_CABINET); setDialog("cabinet"); }} className="flex items-center justify-center gap-2 rounded-xl bg-violet-700 px-4 py-2.5 text-sm font-extrabold text-white">
                                <Plus size={18} weight="bold" /> إضافة دولاب داخل القسم
                            </button>
                        </div>
                        <div className="mt-4"><CapabilityPills values={selectedSection.capabilities} options={capabilityOptions} /></div>
                    </div>
                    {!selectedSection.cabinets?.length ? (
                        <button type="button" onClick={() => setDialog("cabinet")} className="flex min-h-[360px] w-full flex-col items-center justify-center rounded-3xl border-2 border-dashed border-violet-200 bg-violet-50/50 p-8 text-center">
                            <Cube size={52} weight="duotone" className="text-violet-400" />
                            <h3 className="mt-3 text-xl font-black text-slate-950">لا توجد دواليب في هذا القسم</h3>
                            <p className="mt-2 text-sm text-slate-500">حدد عدد الأعمدة وعدد الخانات في كل عمود، وسيولّد ميزان جميع الخانات وأكوادها تلقائيًا.</p>
                            <span className="mt-4 rounded-xl bg-violet-700 px-4 py-2 text-sm font-bold text-white">إضافة أول دولاب</span>
                        </button>
                    ) : (
                        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                            {selectedSection.cabinets.map((cabinet) => (
                                <button key={cabinet.id} type="button" onClick={() => setCabinetId(cabinet.id)} className="rounded-3xl border border-slate-200 bg-white p-5 text-right shadow-sm transition hover:border-violet-300 hover:shadow-md">
                                    <div className="flex items-start justify-between gap-3">
                                        <div><p className="text-[11px] font-extrabold text-violet-600">دولاب {cabinet.cabinet_number}</p><h3 className="mt-1 text-lg font-black text-slate-950">{cabinet.name}</h3></div>
                                        <Cube size={25} weight="duotone" className="text-violet-700" />
                                    </div>
                                    <div className="mt-4 grid grid-cols-3 gap-2 text-center">
                                        <div className="rounded-xl bg-slate-50 p-2"><span className="block text-[10px] text-slate-400">أعمدة</span><strong className="num">{cabinet.width}</strong></div>
                                        <div className="rounded-xl bg-slate-50 p-2"><span className="block text-[10px] text-slate-400">في العمود</span><strong className="num">{cabinet.length}</strong></div>
                                        <div className="rounded-xl bg-emerald-50 p-2"><span className="block text-[10px] text-emerald-600">الإجمالي</span><strong className="num text-emerald-800">{cabinet.total_locations}</strong></div>
                                    </div>
                                </button>
                            ))}
                        </div>
                    )}
                </div>
            );
        }

        return (
            <div className="space-y-4">
                <div className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
                    <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                        <div>
                            <p className="text-xs font-extrabold text-violet-600">الخطوة 2 — أقسام الفرع</p>
                            <h2 className="mt-1 text-2xl font-black text-slate-950">{selectedBranch.name}</h2>
                            <p className="mt-2 flex items-center gap-1 text-sm text-slate-500"><MapPin size={16} /> {address}</p>
                        </div>
                        <button type="button" onClick={() => { setSectionForm({ ...EMPTY_SECTION, capabilities: templates.storage || ["cabinets"] }); setDialog("section"); }} className="flex items-center justify-center gap-2 rounded-xl bg-violet-700 px-4 py-2.5 text-sm font-extrabold text-white">
                            <Plus size={18} weight="bold" /> إضافة قسم
                        </button>
                    </div>
                </div>
                {!sections.length ? (
                    <button type="button" onClick={() => setDialog("section")} className="flex min-h-[360px] w-full flex-col items-center justify-center rounded-3xl border-2 border-dashed border-violet-200 bg-violet-50/50 p-8 text-center">
                        <Wrench size={52} weight="duotone" className="text-violet-400" />
                        <h3 className="mt-3 text-xl font-black text-slate-950">الفرع لا يحتوي أقسامًا بعد</h3>
                        <p className="mt-2 max-w-lg text-sm leading-6 text-slate-500">أضف قسم تخزين أو تركيب أو نحت أو تجهيز أو شحن. كل قسم يمكن أن يجمع أكثر من وظيفة، وكل دولاب سيكون داخله.</p>
                        <span className="mt-4 rounded-xl bg-violet-700 px-4 py-2 text-sm font-bold text-white">إضافة أول قسم</span>
                    </button>
                ) : (
                    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                        {sections.map((section) => (
                            <button key={section.id} type="button" onClick={() => chooseSection(section.id)} className="rounded-3xl border border-slate-200 bg-white p-5 text-right shadow-sm transition hover:border-violet-300 hover:shadow-md">
                                <div className="flex items-start justify-between gap-3">
                                    <div><p className="num text-[11px] font-extrabold text-violet-600">{section.code}</p><h3 className="mt-1 text-lg font-black text-slate-950">{section.name}</h3></div>
                                    <Wrench size={25} weight="duotone" className="text-violet-700" />
                                </div>
                                <div className="mt-3"><CapabilityPills values={section.capabilities} options={capabilityOptions} /></div>
                                <div className="mt-4 flex items-center justify-between rounded-xl bg-slate-50 p-3 text-sm"><span className="text-slate-500">الدواليب</span><strong className="num text-lg text-slate-950">{section.cabinet_count || 0}</strong></div>
                            </button>
                        ))}
                    </div>
                )}
            </div>
        );
    }

    return (
        <div className="space-y-5" dir="rtl" data-testid="warehouse-hierarchy-workspace">
            <section className="rounded-3xl border border-slate-200 bg-gradient-to-l from-white via-white to-violet-50 p-5 shadow-sm">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                    <div>
                        <div className="flex items-center gap-2 text-xs font-extrabold text-violet-700"><Warehouse size={18} weight="duotone" /> Mezan WMS</div>
                        <h1 className="mt-2 text-2xl font-black text-slate-950 sm:text-3xl">الفروع والمخازن</h1>
                        <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-500">المعمارية المعتمدة: <strong>فرع ← قسم ← دولاب ← خانات</strong>. لا يمكن إنشاء دولاب خارج قسم، والترقيم والأكواد والباركودات كلها تلقائية.</p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                        <button type="button" onClick={() => setDialog("branch")} className="flex items-center gap-2 rounded-xl bg-violet-700 px-4 py-2.5 text-sm font-extrabold text-white"><Plus size={18} weight="bold" /> إضافة فرع</button>
                        {!!branches.length && <button type="button" onClick={() => setDialog("reset")} className="flex items-center gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-2.5 text-sm font-extrabold text-rose-700"><Trash size={18} /> بدء من الصفر</button>}
                    </div>
                </div>
            </section>

            <section className="grid gap-4 xl:grid-cols-[280px_minmax(0,1fr)_270px]">
                <aside className="rounded-3xl border border-slate-200 bg-white p-3 shadow-sm">
                    <div className="mb-3 px-2"><h2 className="font-black text-slate-950">الهيكل</h2><p className="text-[11px] text-slate-400">فرع ثم قسم ثم دولاب</p></div>
                    {!branches.length ? (
                        <button type="button" onClick={() => setDialog("branch")} className="w-full rounded-2xl border border-dashed border-violet-200 bg-violet-50 p-4 text-sm font-bold text-violet-800">+ إضافة أول فرع</button>
                    ) : (
                        <div className="space-y-2">
                            {branches.map((branch) => {
                                const activeBranch = branch.id === branchId;
                                return (
                                    <div key={branch.id} className="space-y-1">
                                        <button type="button" onClick={() => chooseBranch(branch.id)} className={`flex w-full items-center gap-2 rounded-2xl border px-3 py-3 text-right transition ${activeBranch ? "border-violet-300 bg-violet-50" : "border-transparent hover:border-slate-200 hover:bg-slate-50"}`}>
                                            <Warehouse size={19} weight="duotone" className={activeBranch ? "text-violet-700" : "text-slate-400"} />
                                            <span className="min-w-0 flex-1"><span className="block truncate text-sm font-black text-slate-900">{branch.name}</span><span className="num block text-[10px] text-slate-400">{branch.code}</span></span>
                                        </button>
                                        {activeBranch && sections.map((section) => {
                                            const activeSection = section.id === sectionId;
                                            return (
                                                <div key={section.id} className="me-4 space-y-1">
                                                    <button type="button" onClick={() => chooseSection(section.id)} className={`flex w-full items-center gap-2 rounded-xl px-3 py-2 text-right ${activeSection ? "bg-violet-100 text-violet-950" : "hover:bg-slate-50"}`}>
                                                        <Wrench size={16} className="text-violet-500" /><span className="min-w-0 flex-1 truncate text-xs font-extrabold">{section.name}</span><span className="num text-[10px] text-slate-400">{section.cabinet_count || 0}</span>
                                                    </button>
                                                    {activeSection && (section.cabinets || []).map((cabinet) => (
                                                        <button key={cabinet.id} type="button" onClick={() => setCabinetId(cabinet.id)} className={`me-4 flex w-[calc(100%-1rem)] items-center gap-2 rounded-xl px-3 py-2 text-right ${cabinet.id === cabinetId ? "bg-emerald-50 text-emerald-900" : "hover:bg-slate-50"}`}>
                                                            <Cube size={15} className="text-emerald-600" /><span className="min-w-0 flex-1 truncate text-xs font-bold">{cabinet.name}</span><span className="num text-[10px] text-slate-400">{cabinet.total_locations}</span>
                                                        </button>
                                                    ))}
                                                </div>
                                            );
                                        })}
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </aside>

                <main className="min-w-0">{renderWorkspace()}</main>

                <aside className="space-y-3">
                    <div className="rounded-3xl border border-slate-200 bg-white p-4 shadow-sm">
                        <h2 className="mb-3 font-black text-slate-950">مسار الإعداد</h2>
                        <div className="space-y-2">
                            <Step number="1" title="الفرع" subtitle="الدولة والمدينة والعنوان" done={!!branchId} active={currentStep === 1} />
                            <Step number="2" title="القسم" subtitle="تخزين، تركيب، نحت، شحن..." done={!!sectionId} active={currentStep === 2} />
                            <Step number="3" title="الدولاب" subtitle="أعمدة وخانات داخل القسم" done={!!cabinetId} active={currentStep === 3} />
                            <Step number="4" title="الخانات" subtitle="ترقيم وباركود تلقائي" done={!!cabinetId} active={currentStep === 4} />
                        </div>
                    </div>
                    <div className="grid grid-cols-3 gap-2">
                        <div className="rounded-2xl border border-slate-200 bg-white p-3 text-center"><Package size={20} className="mx-auto text-violet-600" /><strong className="num mt-1 block text-lg">{sections.length}</strong><span className="text-[10px] text-slate-400">قسم</span></div>
                        <div className="rounded-2xl border border-slate-200 bg-white p-3 text-center"><Cube size={20} className="mx-auto text-violet-600" /><strong className="num mt-1 block text-lg">{allCabinets.length}</strong><span className="text-[10px] text-slate-400">دولاب</span></div>
                        <div className="rounded-2xl border border-slate-200 bg-white p-3 text-center"><GearSix size={20} className="mx-auto text-violet-600" /><strong className="num mt-1 block text-lg">{totalLocations}</strong><span className="text-[10px] text-slate-400">خانة</span></div>
                    </div>
                </aside>
            </section>

            {dialog === "branch" && (
                <Modal title="إضافة فرع" subtitle="رقم الفرع ورمزه يُنشآن تلقائيًا داخل المدينة." onClose={() => setDialog(null)}>
                    <form onSubmit={createBranch} className="space-y-4">
                        <div className="grid gap-3 md:grid-cols-2">
                            <Field label="اسم الفرع"><Input required value={branchForm.name} onChange={(event) => setBranchForm({ ...branchForm, name: event.target.value })} placeholder="مثال: فرع بيت العود" /></Field>
                            <Field label="الدولة"><Select value={branchForm.country} onChange={(event) => changeCountry(event.target.value)}>{catalog.countries.map((row) => <option key={row.name} value={row.name}>{row.name}</option>)}</Select></Field>
                            <Field label="المدينة"><Select value={branchForm.city} onChange={(event) => setBranchForm({ ...branchForm, city: event.target.value })}>{(countryRow?.cities || []).map((city) => <option key={city} value={city}>{city}</option>)}</Select></Field>
                            <Field label="رقم الفرع" hint="تلقائي داخل كل مدينة"><Input disabled value="تلقائي — يبدأ من 1" /></Field>
                            <Field label="الحي"><Input required value={branchForm.district} onChange={(event) => setBranchForm({ ...branchForm, district: event.target.value })} /></Field>
                            <Field label="الشارع"><Input required value={branchForm.street} onChange={(event) => setBranchForm({ ...branchForm, street: event.target.value })} /></Field>
                        </div>
                        <button disabled={loading} className="w-full rounded-xl bg-violet-700 px-4 py-3 text-sm font-extrabold text-white disabled:opacity-50">إنشاء الفرع</button>
                    </form>
                </Modal>
            )}

            {dialog === "section" && (
                <Modal title="إضافة قسم" subtitle={`داخل ${selectedBranch?.name || "الفرع"}. كل قسم يدعم الدواليب ويمكن أن يجمع عدة وظائف.`} onClose={() => setDialog(null)}>
                    <form onSubmit={createSection} className="space-y-4">
                        <div className="grid gap-3 md:grid-cols-2">
                            <Field label="اسم القسم"><Input required value={sectionForm.name} onChange={(event) => setSectionForm({ ...sectionForm, name: event.target.value })} placeholder="مثال: قسم التركيب والنحت" /></Field>
                            <Field label="قالب سريع"><Select value={sectionForm.template} onChange={(event) => applyTemplate(event.target.value)}>{Object.entries(TEMPLATE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select></Field>
                        </div>
                        <div>
                            <p className="mb-2 text-xs font-extrabold text-slate-700">وظائف القسم</p>
                            <div className="grid gap-2 sm:grid-cols-2">
                                {capabilityOptions.map((item) => {
                                    const fixed = item.value === "cabinets";
                                    const checked = fixed || sectionForm.capabilities.includes(item.value);
                                    return (
                                        <label key={item.value} className={`flex items-center gap-2 rounded-xl border px-3 py-2.5 text-sm font-bold ${checked ? "border-violet-300 bg-violet-50 text-violet-900" : "border-slate-200 text-slate-600"}`}>
                                            <input type="checkbox" checked={checked} disabled={fixed} onChange={() => toggleCapability(item.value)} />
                                            {item.label}{fixed && <span className="text-[10px] text-violet-500">إلزامي</span>}
                                        </label>
                                    );
                                })}
                            </div>
                        </div>
                        <Field label="ملاحظات (اختياري)"><Input value={sectionForm.notes} onChange={(event) => setSectionForm({ ...sectionForm, notes: event.target.value })} /></Field>
                        <button disabled={loading} className="w-full rounded-xl bg-violet-700 px-4 py-3 text-sm font-extrabold text-white disabled:opacity-50">إنشاء القسم</button>
                    </form>
                </Modal>
            )}

            {dialog === "cabinet" && (
                <Modal title="إضافة دولاب" subtitle={`داخل قسم ${selectedSection?.name || ""}. الخانات والترقيم والباركود تُنشأ تلقائيًا.`} onClose={() => setDialog(null)}>
                    <form onSubmit={createCabinet} className="space-y-4">
                        <div className="rounded-2xl border border-violet-100 bg-violet-50 p-4">
                            <p className="text-xs font-bold text-violet-600">الموقع</p>
                            <p className="mt-1 font-black text-violet-950">{selectedBranch?.name} ← {selectedSection?.name}</p>
                        </div>
                        <Field label="اسم الدولاب (اختياري)" hint="الرقم والكود تلقائيان"><Input value={cabinetForm.cabinet_name} onChange={(event) => setCabinetForm({ ...cabinetForm, cabinet_name: event.target.value })} placeholder="مثال: دولاب السلاسل" /></Field>
                        <div className="grid gap-3 md:grid-cols-2">
                            <Field label="عدد الأعمدة"><Input required type="number" min="1" max="100" value={cabinetForm.columns} onChange={(event) => setCabinetForm({ ...cabinetForm, columns: event.target.value })} /></Field>
                            <Field label="عدد الخانات في كل عمود"><Input required type="number" min="1" max="100" value={cabinetForm.slots_per_column} onChange={(event) => setCabinetForm({ ...cabinetForm, slots_per_column: event.target.value })} /></Field>
                            <Field label="نوع الاستخدام"><Select value={cabinetForm.purpose} onChange={(event) => setCabinetForm({ ...cabinetForm, purpose: event.target.value })}>{PURPOSES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select></Field>
                            <Field label="سعة كل خانة (اختياري)"><Input type="number" min="1" value={cabinetForm.max_items_per_location} onChange={(event) => setCabinetForm({ ...cabinetForm, max_items_per_location: event.target.value })} /></Field>
                        </div>
                        <div className="rounded-2xl bg-slate-950 p-4 text-white">
                            <div className="flex items-center justify-between"><span className="text-sm font-bold">إجمالي الخانات التي ستنشأ</span><strong className="num text-3xl">{cabinetTotal}</strong></div>
                            <p className="mt-1 text-xs text-slate-300">{cabinetForm.columns} أعمدة × {cabinetForm.slots_per_column} خانات في العمود</p>
                        </div>
                        <button disabled={loading || cabinetTotal < 1 || cabinetTotal > 5000} className="w-full rounded-xl bg-violet-700 px-4 py-3 text-sm font-extrabold text-white disabled:opacity-50">إنشاء الدولاب والخانات</button>
                    </form>
                </Modal>
            )}

            {dialog === "reset" && (
                <Modal title="بدء الفروع والمخازن من الصفر" subtitle="سيُحذف الفرع الحالي وجميع الأقسام والدواليب والخانات وبيانات التجربة، ثم تبدأ كل الأرقام من 1." onClose={() => { setDialog(null); setResetConfirmation(""); }} maxWidth="max-w-lg">
                    <form onSubmit={resetWorkspace} className="space-y-4">
                        <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm leading-6 text-rose-800">هذا الإجراء خاص ببيانات الفروع والمخازن فقط، ولا يحذف الطلبات أو المنتجات أو بيانات سلة وقيود.</div>
                        <Field label="اكتب كلمة حذف للتأكيد"><Input autoFocus value={resetConfirmation} onChange={(event) => setResetConfirmation(event.target.value)} placeholder="حذف" /></Field>
                        <button disabled={loading || resetConfirmation !== "حذف"} className="flex w-full items-center justify-center gap-2 rounded-xl bg-rose-700 px-4 py-3 text-sm font-extrabold text-white disabled:opacity-40"><Trash size={18} /> حذف بيانات التجربة والبدء من الصفر</button>
                    </form>
                </Modal>
            )}
        </div>
    );
}
