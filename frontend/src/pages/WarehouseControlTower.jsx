import { useEffect, useMemo, useState } from "react";
import {
    ArrowRight,
    CaretDown,
    CheckCircle,
    Cube,
    GearSix,
    House,
    MapPin,
    Package,
    Plus,
    Printer,
    Warehouse,
    Wrench,
    X,
} from "@phosphor-icons/react";
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
    administration: ["office", "cabinets"],
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
    count: 1,
    template: "storage",
    capabilities: ["cabinets"],
    notes: "",
};

const EMPTY_STORAGE = {
    target: "main",
    cabinet_name: "",
    length: 4,
    width: 6,
    purpose: "permanent_storage",
    max_items_per_location: "",
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
    return (
        <input
            {...props}
            className={`w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-100 ${props.className || ""}`}
        />
    );
}

function Select(props) {
    return (
        <select
            {...props}
            className={`w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-100 ${props.className || ""}`}
        />
    );
}

function Modal({ title, subtitle, onClose, children }) {
    return (
        <div className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-950/45 p-4" onMouseDown={onClose}>
            <div
                className="max-h-[92vh] w-full max-w-2xl overflow-y-auto rounded-3xl border border-slate-200 bg-white shadow-2xl"
                onMouseDown={(event) => event.stopPropagation()}
                dir="rtl"
            >
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

function Metric({ label, value, hint, Icon }) {
    return (
        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex items-center justify-between gap-3">
                <div>
                    <p className="text-xs font-bold text-slate-500">{label}</p>
                    <p className="num mt-1 text-2xl font-black text-slate-950">{value}</p>
                    {hint && <p className="mt-1 text-[11px] text-slate-400">{hint}</p>}
                </div>
                <div className="rounded-2xl bg-violet-50 p-2.5 text-violet-700">
                    <Icon size={22} weight="duotone" />
                </div>
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

function printStoragePack(branch, section, cabinet) {
    if (!branch || !cabinet) return;
    const cabinetNumber = cabinet.cabinet_number || cabinet.code;
    const total = Number(cabinet.total_locations || 0);
    const width = Number(cabinet.width || 0);
    const baseCode = `${branch.code}-${cabinet.code}`;
    const areaLabel = section ? `قسم ${section.section_number || section.room_number} — ${section.name}` : "المساحة الرئيسية";
    const branchLabel = `${branch.name} — ${branch.city} — فرع رقم ${branch.warehouse_number} — ${areaLabel}`;

    const cover = printablePage({
        eyebrow: "رقم وحدة التخزين",
        value: cabinetNumber,
        subtitle: branchLabel,
        code: baseCode,
    });
    const columnPages = Array.from({ length: width }, (_, index) => printablePage({
        eyebrow: `وحدة التخزين ${cabinetNumber}`,
        value: `عمود ${index + 1}`,
        subtitle: branchLabel,
        code: `${baseCode}-C${String(index + 1).padStart(2, "0")}`,
    })).join("");
    const locationPages = Array.from({ length: total }, (_, index) => {
        const number = String(index + 1).padStart(3, "0");
        const code = `${baseCode}-${number}`;
        return printablePage({
            eyebrow: `وحدة التخزين ${cabinetNumber} — الخانة`,
            value: number,
            subtitle: "يجب مسح الباركود قبل وضع أي منتج",
            code,
            barcode: true,
        });
    }).join("");

    const printWindow = window.open("", "_blank");
    if (!printWindow) {
        toast.error("اسمح بفتح النوافذ المنبثقة لطباعة الملصقات");
        return;
    }
    printWindow.document.write(`<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8" />
<title>${escapeHtml(branch.name)} — ${escapeHtml(String(cabinetNumber))}</title>
<style>
@page { size: A4 portrait; margin: 0; }
* { box-sizing: border-box; }
body { margin: 0; font-family: Arial, Tahoma, sans-serif; color: #0f172a; background: white; }
.print-page { position: relative; width: 210mm; height: 297mm; page-break-after: always; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 18mm; text-align: center; border: 8mm solid #5b21b6; }
.print-page:last-child { page-break-after: auto; }
.brand { position: absolute; top: 14mm; font-size: 20pt; font-weight: 900; letter-spacing: 2px; color: #5b21b6; }
.eyebrow { font-size: 26pt; font-weight: 800; margin-bottom: 8mm; }
.big-value { font-size: 96pt; line-height: 1; font-weight: 900; direction: ltr; }
.subtitle { margin-top: 10mm; font-size: 18pt; font-weight: 700; max-width: 170mm; }
.code { margin-top: 6mm; font: 900 22pt monospace; direction: ltr; letter-spacing: 1px; }
.barcode { width: 165mm; height: 34mm; margin-top: 12mm; }
@media screen { body { background: #e2e8f0; } .print-page { margin: 10px auto; background: white; box-shadow: 0 4px 18px rgba(15,23,42,.18); } }
</style></head><body>${cover}${columnPages}${locationPages}<script>window.onload=()=>window.print();</script></body></html>`);
    printWindow.document.close();
}

function purposeLabel(value) {
    return PURPOSES.find(([key]) => key === value)?.[1] || value || "غير محدد";
}

function NodeButton({ active, indent = false, icon: Icon, title, subtitle, onClick, badge }) {
    return (
        <button
            type="button"
            onClick={onClick}
            className={`flex w-full items-center gap-2 rounded-xl border px-3 py-2.5 text-right transition ${indent ? "me-4 w-[calc(100%-1rem)]" : ""} ${active ? "border-violet-300 bg-violet-50 text-violet-950" : "border-transparent text-slate-700 hover:border-slate-200 hover:bg-slate-50"}`}
        >
            <Icon size={18} weight="duotone" className={active ? "text-violet-700" : "text-slate-400"} />
            <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-extrabold">{title}</span>
                {subtitle && <span className="block truncate text-[11px] text-slate-400">{subtitle}</span>}
            </span>
            {badge != null && <span className="num rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-black text-slate-600">{badge}</span>}
        </button>
    );
}

export default function WarehouseControlTower() {
    const [catalog, setCatalog] = useState(FALLBACK_CATALOG);
    const [branches, setBranches] = useState([]);
    const [branchId, setBranchId] = useState("");
    const [branchDetail, setBranchDetail] = useState(null);
    const [sections, setSections] = useState([]);
    const [capabilityOptions, setCapabilityOptions] = useState(FALLBACK_CAPABILITIES);
    const [templates, setTemplates] = useState(FALLBACK_TEMPLATES);
    const [loading, setLoading] = useState(false);
    const [dialog, setDialog] = useState(null);
    const [addOpen, setAddOpen] = useState(false);
    const [selectedNode, setSelectedNode] = useState({ type: "branch", id: null });
    const [branchForm, setBranchForm] = useState(EMPTY_BRANCH);
    const [sectionForm, setSectionForm] = useState(EMPTY_SECTION);
    const [storageForm, setStorageForm] = useState(EMPTY_STORAGE);

    const selectedBranch = useMemo(
        () => branches.find((row) => row.id === branchId) || branchDetail?.warehouse || null,
        [branches, branchDetail, branchId],
    );

    const countryRow = useMemo(
        () => catalog.countries.find((row) => row.name === branchForm.country) || catalog.countries[0],
        [branchForm.country, catalog.countries],
    );

    const directCabinets = useMemo(
        () => (branchDetail?.cabinets || []).filter((row) => !row.room_id && !row.section_id),
        [branchDetail],
    );

    const sectionCabinets = useMemo(
        () => sections.flatMap((section) => (section.cabinets || []).map((cabinet) => ({ ...cabinet, _section: section }))),
        [sections],
    );

    const allCabinets = useMemo(() => [...directCabinets, ...sectionCabinets], [directCabinets, sectionCabinets]);
    const totalLocations = useMemo(
        () => allCabinets.reduce((sum, row) => sum + Number(row.total_locations || 0), 0),
        [allCabinets],
    );
    const activeCapabilities = useMemo(
        () => new Set(sections.flatMap((row) => row.capabilities || [])).size,
        [sections],
    );

    const storageTargets = useMemo(() => [
        { value: "main", label: "المساحة الرئيسية في الفرع" },
        ...sections
            .filter((row) => (row.capabilities || []).includes("cabinets"))
            .map((row) => ({ value: `section:${row.id}`, label: `قسم ${row.section_number || row.room_number} — ${row.name}` })),
    ], [sections]);

    const selectedSection = useMemo(() => {
        if (selectedNode.type === "section") return sections.find((row) => row.id === selectedNode.id) || null;
        if (selectedNode.type === "cabinet" && selectedNode.sectionId) return sections.find((row) => row.id === selectedNode.sectionId) || null;
        return null;
    }, [sections, selectedNode]);

    const selectedCabinet = useMemo(() => {
        if (selectedNode.type !== "cabinet") return null;
        return allCabinets.find((row) => row.id === selectedNode.id) || null;
    }, [allCabinets, selectedNode]);

    async function loadBranches(preferredId = "") {
        const response = await api.get("/warehouse-locations/warehouses");
        const items = response.data?.items || [];
        setBranches(items);
        const nextId = preferredId || (items.some((row) => row.id === branchId) ? branchId : items[0]?.id || "");
        setBranchId(nextId);
        return nextId;
    }

    async function loadWorkspace(id) {
        if (!id) {
            setBranchDetail(null);
            setSections([]);
            setSelectedNode({ type: "branch", id: null });
            return;
        }
        const [detailResponse, sectionsResponse] = await Promise.all([
            api.get(`/warehouse-locations/warehouses/${id}`),
            api.get(`/warehouse-locations-v2/warehouses/${id}/sections`),
        ]);
        setBranchDetail(detailResponse.data || null);
        setSections(sectionsResponse.data?.items || []);
        setSelectedNode((current) => current.id ? current : { type: "branch", id });
    }

    useEffect(() => {
        Promise.all([
            api.get("/warehouse-locations-v2/catalog").then((response) => setCatalog(response.data || FALLBACK_CATALOG)).catch(() => null),
            api.get("/warehouse-locations-v2/section-capabilities").then((response) => {
                setCapabilityOptions(response.data?.items || FALLBACK_CAPABILITIES);
                setTemplates(response.data?.recommended || FALLBACK_TEMPLATES);
            }).catch(() => null),
            loadBranches(),
        ]).catch(() => toast.error("تعذر تحميل شبكة الفروع والمواقع"));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        loadWorkspace(branchId).catch(() => toast.error("تعذر تحميل مخطط الفرع"));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [branchId]);

    async function refreshWorkspace(preferredSelection = null) {
        await Promise.all([loadBranches(branchId), loadWorkspace(branchId)]);
        if (preferredSelection) setSelectedNode(preferredSelection);
    }

    function changeCountry(country) {
        const row = catalog.countries.find((item) => item.name === country);
        setBranchForm((current) => ({
            ...current,
            country,
            city: row?.default_city || row?.cities?.[0] || "",
        }));
    }

    function applyTemplate(value) {
        setSectionForm((current) => ({
            ...current,
            template: value,
            capabilities: value === "custom" ? current.capabilities : templates[value] || [],
        }));
    }

    function toggleCapability(value) {
        setSectionForm((current) => ({
            ...current,
            template: "custom",
            capabilities: current.capabilities.includes(value)
                ? current.capabilities.filter((item) => item !== value)
                : [...current.capabilities, value],
        }));
    }

    function openSectionDialog() {
        if (!branchId) return toast.warning("أنشئ فرعًا أو اختر فرعًا أولًا");
        setSectionForm({ ...EMPTY_SECTION, capabilities: templates.storage || ["cabinets"] });
        setDialog("section");
        setAddOpen(false);
    }

    function openStorageDialog(target = null) {
        if (!branchId) return toast.warning("أنشئ فرعًا أو اختر فرعًا أولًا");
        let resolvedTarget = target;
        if (!resolvedTarget && selectedNode.type === "section") resolvedTarget = `section:${selectedNode.id}`;
        if (!resolvedTarget && selectedNode.type === "cabinet" && selectedNode.sectionId) resolvedTarget = `section:${selectedNode.sectionId}`;
        setStorageForm({ ...EMPTY_STORAGE, target: resolvedTarget || "main" });
        setDialog("storage");
        setAddOpen(false);
    }

    async function createBranch(event) {
        event.preventDefault();
        setLoading(true);
        try {
            const response = await api.post("/warehouse-locations-v2/warehouses", branchForm);
            toast.success(`تم إنشاء فرع ${response.data.name}`);
            setBranchForm({ ...EMPTY_BRANCH, country: catalog.default_country, city: catalog.default_city });
            setDialog(null);
            await loadBranches(response.data.id);
            setSelectedNode({ type: "branch", id: response.data.id });
        } catch (error) {
            const code = error?.response?.data?.detail?.code;
            toast.error(code === "city_not_in_country" ? "المدينة لا تتبع الدولة المحددة" : "تعذر إنشاء الفرع");
        } finally {
            setLoading(false);
        }
    }

    async function createSection(event) {
        event.preventDefault();
        setLoading(true);
        try {
            const count = Number(sectionForm.count || 1);
            let selectedId = null;
            if (count > 1) {
                const response = await api.post("/warehouse-locations-v2/sections/bulk", {
                    warehouse_id: branchId,
                    count,
                    default_capabilities: sectionForm.capabilities,
                });
                selectedId = response.data?.items?.[0]?.id || null;
                toast.success(`تم إنشاء ${response.data.created_count} أقسام`);
            } else {
                const response = await api.post("/warehouse-locations-v2/sections", {
                    warehouse_id: branchId,
                    name: sectionForm.name || null,
                    capabilities: sectionForm.capabilities,
                    notes: sectionForm.notes || null,
                });
                selectedId = response.data.id;
                toast.success(`تم إنشاء القسم رقم ${response.data.section_number}`);
            }
            setDialog(null);
            await loadWorkspace(branchId);
            if (selectedId) setSelectedNode({ type: "section", id: selectedId });
        } catch (error) {
            toast.error(error?.response?.data?.detail?.message || "تعذر إنشاء القسم");
        } finally {
            setLoading(false);
        }
    }

    async function createStorage(event) {
        event.preventDefault();
        setLoading(true);
        try {
            const payload = {
                cabinet_name: storageForm.cabinet_name || null,
                length: Number(storageForm.length),
                width: Number(storageForm.width),
                purpose: storageForm.purpose,
                max_items_per_location: storageForm.max_items_per_location ? Number(storageForm.max_items_per_location) : null,
            };
            let response;
            let sectionId = null;
            if (storageForm.target.startsWith("section:")) {
                sectionId = storageForm.target.split(":")[1];
                response = await api.post(`/warehouse-locations-v2/sections/${sectionId}/cabinets`, payload);
            } else {
                response = await api.post("/warehouse-locations-v2/cabinets", {
                    ...payload,
                    warehouse_id: branchId,
                });
            }
            toast.success(`تم إنشاء وحدة التخزين رقم ${response.data.cabinet.cabinet_number}`);
            setDialog(null);
            await loadWorkspace(branchId);
            setSelectedNode({ type: "cabinet", id: response.data.cabinet.id, sectionId });
        } catch (error) {
            toast.error(error?.response?.data?.detail?.message || "تعذر إنشاء وحدة التخزين");
        } finally {
            setLoading(false);
        }
    }

    const address = selectedBranch
        ? [selectedBranch.country, selectedBranch.city, selectedBranch.district, selectedBranch.street].filter(Boolean).join(" — ")
        : "";

    function renderInspector() {
        if (!selectedBranch) {
            return (
                <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-6 text-center">
                    <Warehouse size={36} weight="duotone" className="mx-auto text-slate-300" />
                    <h3 className="mt-3 font-black text-slate-900">ابدأ بإنشاء فرع</h3>
                    <p className="mt-1 text-sm text-slate-500">بعد إنشاء الفرع ستدير الأقسام ووحدات التخزين من مكان واحد.</p>
                    <button type="button" onClick={() => setDialog("branch")} className="mt-4 rounded-xl bg-violet-700 px-4 py-2 text-sm font-bold text-white">إنشاء فرع</button>
                </div>
            );
        }

        if (selectedNode.type === "cabinet" && selectedCabinet) {
            const section = selectedCabinet._section || selectedSection;
            return (
                <div className="space-y-4">
                    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                        <div className="flex items-start justify-between gap-3">
                            <div>
                                <p className="text-xs font-bold text-violet-600">وحدة تخزين</p>
                                <h3 className="mt-1 text-xl font-black text-slate-950">{selectedCabinet.name || `دولاب ${selectedCabinet.cabinet_number}`}</h3>
                                <p className="num mt-1 text-xs text-slate-400">{selectedBranch.code}-{selectedCabinet.code}</p>
                            </div>
                            <Cube size={26} weight="duotone" className="text-violet-700" />
                        </div>
                        <div className="mt-4 grid grid-cols-2 gap-2 text-sm">
                            <div className="rounded-xl bg-slate-50 p-3"><span className="block text-[11px] text-slate-400">الموقع</span><strong>{section ? section.name : "المساحة الرئيسية"}</strong></div>
                            <div className="rounded-xl bg-slate-50 p-3"><span className="block text-[11px] text-slate-400">الخانات</span><strong className="num">{selectedCabinet.total_locations || 0}</strong></div>
                            <div className="rounded-xl bg-slate-50 p-3"><span className="block text-[11px] text-slate-400">الشبكة</span><strong className="num">{selectedCabinet.length} × {selectedCabinet.width}</strong></div>
                            <div className="rounded-xl bg-slate-50 p-3"><span className="block text-[11px] text-slate-400">الاستخدام</span><strong>{purposeLabel(selectedCabinet.purpose)}</strong></div>
                        </div>
                        <button type="button" onClick={() => printStoragePack(selectedBranch, section, selectedCabinet)} className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl border border-violet-200 bg-violet-50 px-4 py-2.5 text-sm font-extrabold text-violet-800 hover:bg-violet-100">
                            <Printer size={18} weight="duotone" />
                            طباعة رقم الدولاب والأعمدة وباركودات الخانات
                        </button>
                    </div>
                    <div className="rounded-2xl border border-slate-200 bg-white p-4">
                        <p className="mb-3 text-xs font-extrabold text-slate-500">معاينة الخانات</p>
                        <div className="grid gap-1.5" style={{ gridTemplateColumns: `repeat(${Math.min(Number(selectedCabinet.width || 1), 8)}, minmax(0, 1fr))` }}>
                            {Array.from({ length: Math.min(Number(selectedCabinet.total_locations || 0), 64) }, (_, index) => (
                                <div key={index} className="flex aspect-square items-center justify-center rounded-lg border border-emerald-100 bg-emerald-50 text-[9px] font-black text-emerald-700 num">
                                    {String(index + 1).padStart(3, "0")}
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            );
        }

        if (selectedNode.type === "section" && selectedSection) {
            return (
                <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                    <div className="flex items-start justify-between gap-3">
                        <div>
                            <p className="text-xs font-bold text-violet-600">قسم تشغيلي</p>
                            <h3 className="mt-1 text-xl font-black text-slate-950">{selectedSection.name}</h3>
                            <p className="num mt-1 text-xs text-slate-400">{selectedBranch.code}-{selectedSection.code}</p>
                        </div>
                        <Wrench size={26} weight="duotone" className="text-violet-700" />
                    </div>
                    <div className="mt-4 flex flex-wrap gap-2">
                        {(selectedSection.capabilities || []).map((value) => (
                            <span key={value} className="rounded-full bg-violet-50 px-2.5 py-1 text-[11px] font-bold text-violet-800">
                                {capabilityOptions.find((item) => item.value === value)?.label || value}
                            </span>
                        ))}
                    </div>
                    <div className="mt-4 rounded-xl bg-slate-50 p-3 text-sm">
                        <span className="text-slate-500">وحدات التخزين داخل القسم:</span>
                        <strong className="num me-2">{selectedSection.cabinet_count || 0}</strong>
                    </div>
                    <div className="mt-4 grid gap-2">
                        {(selectedSection.capabilities || []).includes("cabinets") && (
                            <button type="button" onClick={() => openStorageDialog(`section:${selectedSection.id}`)} className="flex items-center justify-center gap-2 rounded-xl bg-violet-700 px-4 py-2.5 text-sm font-bold text-white">
                                <Plus size={17} weight="bold" /> إضافة وحدة تخزين
                            </button>
                        )}
                        <button type="button" onClick={openSectionDialog} className="rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-bold text-slate-700 hover:bg-slate-50">إضافة قسم آخر</button>
                    </div>
                </div>
            );
        }

        if (selectedNode.type === "main") {
            return (
                <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                    <div className="flex items-start justify-between gap-3">
                        <div>
                            <p className="text-xs font-bold text-emerald-600">مساحة بسيطة تلقائية</p>
                            <h3 className="mt-1 text-xl font-black text-slate-950">المساحة الرئيسية</h3>
                            <p className="mt-1 text-sm text-slate-500">تستخدمها عندما يكون الفرع مخزنًا مفتوحًا بلا أقسام.</p>
                        </div>
                        <House size={26} weight="duotone" className="text-emerald-700" />
                    </div>
                    <div className="mt-4 rounded-xl bg-slate-50 p-3 text-sm">
                        <span className="text-slate-500">وحدات التخزين المباشرة:</span>
                        <strong className="num me-2">{directCabinets.length}</strong>
                    </div>
                    <button type="button" onClick={() => openStorageDialog("main")} className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-emerald-700 px-4 py-2.5 text-sm font-bold text-white">
                        <Plus size={17} weight="bold" /> إضافة وحدة تخزين
                    </button>
                </div>
            );
        }

        return (
            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                <div className="flex items-start justify-between gap-3">
                    <div>
                        <p className="text-xs font-bold text-violet-600">الفرع</p>
                        <h3 className="mt-1 text-xl font-black text-slate-950">{selectedBranch.name}</h3>
                        <p className="num mt-1 text-xs text-slate-400">{selectedBranch.code}</p>
                    </div>
                    <Warehouse size={28} weight="duotone" className="text-violet-700" />
                </div>
                <div className="mt-4 rounded-xl bg-slate-50 p-3 text-sm text-slate-600">
                    <MapPin size={16} weight="duotone" className="ms-1 inline text-violet-600" />
                    {address || "لم يكتمل العنوان"}
                </div>
                <div className="mt-4 grid grid-cols-2 gap-2">
                    <div className="rounded-xl border border-slate-100 p-3"><span className="block text-[11px] text-slate-400">الأقسام</span><strong className="num text-lg">{sections.length}</strong></div>
                    <div className="rounded-xl border border-slate-100 p-3"><span className="block text-[11px] text-slate-400">وحدات التخزين</span><strong className="num text-lg">{allCabinets.length}</strong></div>
                </div>
                <div className="mt-4 grid gap-2">
                    <button type="button" onClick={openSectionDialog} className="flex items-center justify-center gap-2 rounded-xl bg-violet-700 px-4 py-2.5 text-sm font-bold text-white"><Plus size={17} weight="bold" /> إضافة قسم</button>
                    <button type="button" onClick={() => openStorageDialog("main")} className="flex items-center justify-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm font-bold text-emerald-800"><Plus size={17} weight="bold" /> إضافة وحدة تخزين للمساحة الرئيسية</button>
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-5" dir="rtl" data-testid="warehouse-control-tower">
            <section className="rounded-3xl border border-slate-200 bg-gradient-to-l from-white via-white to-violet-50 p-5 shadow-sm">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                    <div>
                        <div className="flex items-center gap-2 text-xs font-extrabold text-violet-700">
                            <Warehouse size={18} weight="duotone" />
                            Mezan WMS Control Tower
                        </div>
                        <h1 className="mt-2 text-2xl font-black text-slate-950 sm:text-3xl">الفروع والمخازن</h1>
                        <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-500">مخطط هرمي موحّد: فرع ← مساحة رئيسية أو أقسام ← وحدات تخزين ← خانات. لا توجد نماذج مكررة؛ كل إجراء يظهر حسب العنصر المحدد.</p>
                    </div>
                    <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                        <Select value={branchId} onChange={(event) => { setBranchId(event.target.value); setSelectedNode({ type: "branch", id: event.target.value }); }} className="min-w-[250px]">
                            <option value="">اختر الفرع</option>
                            {branches.map((row) => <option key={row.id} value={row.id}>{row.name} — {row.city} — رقم {row.warehouse_number}</option>)}
                        </Select>
                        <div className="relative">
                            <button type="button" onClick={() => setAddOpen((value) => !value)} className="flex w-full items-center justify-center gap-2 rounded-xl bg-violet-700 px-4 py-2.5 text-sm font-extrabold text-white shadow-sm hover:bg-violet-800">
                                <Plus size={18} weight="bold" /> إضافة
                                <CaretDown size={14} weight="bold" />
                            </button>
                            {addOpen && (
                                <div className="absolute left-0 top-full z-30 mt-2 w-56 rounded-2xl border border-slate-200 bg-white p-2 shadow-xl">
                                    <button type="button" onClick={() => { setDialog("branch"); setAddOpen(false); }} className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm font-bold text-slate-700 hover:bg-slate-50"><Warehouse size={17} /> فرع جديد</button>
                                    <button type="button" onClick={openSectionDialog} className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm font-bold text-slate-700 hover:bg-slate-50"><Wrench size={17} /> قسم تشغيلي</button>
                                    <button type="button" onClick={() => openStorageDialog()} className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm font-bold text-slate-700 hover:bg-slate-50"><Cube size={17} /> وحدة تخزين</button>
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            </section>

            <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <Metric label="الأقسام التشغيلية" value={sections.length} hint="داخل الفرع المحدد" Icon={Wrench} />
                <Metric label="وحدات التخزين" value={allCabinets.length} hint="المباشرة وداخل الأقسام" Icon={Cube} />
                <Metric label="إجمالي الخانات" value={totalLocations} hint="مواقع قابلة للباركود" Icon={Package} />
                <Metric label="القدرات المفعلة" value={activeCapabilities} hint="تخزين، إنتاج، شحن..." Icon={GearSix} />
            </section>

            <section className="grid gap-4 xl:grid-cols-[280px_minmax(0,1fr)_320px]">
                <aside className="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
                    <div className="mb-3 flex items-center justify-between px-2">
                        <div>
                            <h2 className="font-black text-slate-950">هيكل الفرع</h2>
                            <p className="text-[11px] text-slate-400">اختر أي عنصر لإدارته</p>
                        </div>
                        <ArrowRight size={18} className="text-slate-300" />
                    </div>
                    {!selectedBranch ? (
                        <div className="rounded-xl border border-dashed p-5 text-center text-sm text-slate-400">لا يوجد فرع محدد.</div>
                    ) : (
                        <div className="space-y-1">
                            <NodeButton active={selectedNode.type === "branch"} icon={Warehouse} title={selectedBranch.name} subtitle={selectedBranch.code} badge={sections.length + 1} onClick={() => setSelectedNode({ type: "branch", id: selectedBranch.id })} />
                            <NodeButton active={selectedNode.type === "main"} indent icon={House} title="المساحة الرئيسية" subtitle="للفروع البسيطة بلا أقسام" badge={directCabinets.length} onClick={() => setSelectedNode({ type: "main", id: "main" })} />
                            {directCabinets.map((cabinet) => (
                                <NodeButton key={cabinet.id} active={selectedNode.type === "cabinet" && selectedNode.id === cabinet.id} indent icon={Cube} title={cabinet.name || `دولاب ${cabinet.cabinet_number}`} subtitle={`${cabinet.total_locations || 0} خانة`} onClick={() => setSelectedNode({ type: "cabinet", id: cabinet.id, sectionId: null })} />
                            ))}
                            {sections.map((section) => (
                                <div key={section.id} className="space-y-1">
                                    <NodeButton active={selectedNode.type === "section" && selectedNode.id === section.id} indent icon={Wrench} title={section.name} subtitle={`قسم ${section.section_number || section.room_number} · ${section.code}`} badge={section.cabinet_count || 0} onClick={() => setSelectedNode({ type: "section", id: section.id })} />
                                    {(section.cabinets || []).map((cabinet) => (
                                        <NodeButton key={cabinet.id} active={selectedNode.type === "cabinet" && selectedNode.id === cabinet.id} indent icon={Cube} title={cabinet.name || `دولاب ${cabinet.cabinet_number}`} subtitle={`${cabinet.total_locations || 0} خانة`} onClick={() => setSelectedNode({ type: "cabinet", id: cabinet.id, sectionId: section.id })} />
                                    ))}
                                </div>
                            ))}
                        </div>
                    )}
                </aside>

                <main className="min-w-0 rounded-2xl border border-slate-200 bg-slate-50/70 p-4 shadow-sm">
                    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                        <div>
                            <h2 className="text-lg font-black text-slate-950">مخطط التشغيل والمواقع</h2>
                            <p className="text-xs text-slate-500">صورة واحدة للفرع بدل نماذج منفصلة ومتكررة.</p>
                        </div>
                        {selectedBranch && <span className="num rounded-full bg-white px-3 py-1 text-xs font-bold text-slate-600 shadow-sm">{selectedBranch.code}</span>}
                    </div>

                    {!selectedBranch ? (
                        <div className="flex min-h-[420px] flex-col items-center justify-center rounded-2xl border border-dashed border-slate-300 bg-white p-8 text-center">
                            <Warehouse size={54} weight="duotone" className="text-slate-300" />
                            <h3 className="mt-4 text-xl font-black text-slate-900">أنشئ أول فرع</h3>
                            <p className="mt-2 max-w-md text-sm leading-6 text-slate-500">لن تظهر الدواليب أو الأقسام قبل اختيار الفرع؛ يبدأ التخطيط من الأعلى ثم يتدرج تلقائيًا.</p>
                            <button type="button" onClick={() => setDialog("branch")} className="mt-5 rounded-xl bg-violet-700 px-5 py-2.5 text-sm font-bold text-white">إنشاء فرع</button>
                        </div>
                    ) : (
                        <div className="grid gap-4 lg:grid-cols-2">
                            <article className={`rounded-2xl border bg-white p-4 transition ${selectedNode.type === "main" ? "border-emerald-400 ring-2 ring-emerald-100" : "border-slate-200"}`}>
                                <div className="flex items-start justify-between gap-3">
                                    <button type="button" onClick={() => setSelectedNode({ type: "main", id: "main" })} className="text-right">
                                        <p className="text-[11px] font-extrabold text-emerald-600">MAIN AREA</p>
                                        <h3 className="mt-1 text-lg font-black text-slate-950">المساحة الرئيسية</h3>
                                        <p className="mt-1 text-xs text-slate-500">مساحة تلقائية للفروع التي لا تحتاج تقسيمًا.</p>
                                    </button>
                                    <House size={25} weight="duotone" className="text-emerald-700" />
                                </div>
                                <div className="mt-4 flex items-center justify-between rounded-xl bg-emerald-50 p-3 text-sm">
                                    <span className="font-bold text-emerald-800">وحدات التخزين</span>
                                    <strong className="num text-lg text-emerald-950">{directCabinets.length}</strong>
                                </div>
                                <div className="mt-3 space-y-2">
                                    {directCabinets.slice(0, 4).map((cabinet) => (
                                        <button key={cabinet.id} type="button" onClick={() => setSelectedNode({ type: "cabinet", id: cabinet.id, sectionId: null })} className="flex w-full items-center justify-between rounded-xl border border-slate-100 px-3 py-2 text-sm hover:border-emerald-200 hover:bg-emerald-50/50">
                                            <span className="font-bold text-slate-700">{cabinet.name || `دولاب ${cabinet.cabinet_number}`}</span>
                                            <span className="num text-xs text-slate-400">{cabinet.total_locations} خانة</span>
                                        </button>
                                    ))}
                                    {!directCabinets.length && <p className="rounded-xl border border-dashed p-4 text-center text-xs text-slate-400">لا توجد وحدات تخزين مباشرة.</p>}
                                </div>
                                <button type="button" onClick={() => openStorageDialog("main")} className="mt-3 flex w-full items-center justify-center gap-2 rounded-xl border border-emerald-200 px-3 py-2 text-xs font-extrabold text-emerald-800 hover:bg-emerald-50"><Plus size={15} weight="bold" /> إضافة وحدة تخزين</button>
                            </article>

                            {sections.map((section) => {
                                const active = selectedNode.type === "section" && selectedNode.id === section.id;
                                return (
                                    <article key={section.id} className={`rounded-2xl border bg-white p-4 transition ${active ? "border-violet-400 ring-2 ring-violet-100" : "border-slate-200"}`}>
                                        <div className="flex items-start justify-between gap-3">
                                            <button type="button" onClick={() => setSelectedNode({ type: "section", id: section.id })} className="min-w-0 text-right">
                                                <p className="num text-[11px] font-extrabold text-violet-600">{section.code}</p>
                                                <h3 className="mt-1 truncate text-lg font-black text-slate-950">{section.name}</h3>
                                                <p className="mt-1 text-xs text-slate-500">قسم ديناميكي متعدد القدرات</p>
                                            </button>
                                            <Wrench size={25} weight="duotone" className="text-violet-700" />
                                        </div>
                                        <div className="mt-3 flex flex-wrap gap-1.5">
                                            {(section.capabilities || []).slice(0, 5).map((value) => (
                                                <span key={value} className="rounded-full bg-violet-50 px-2 py-1 text-[10px] font-bold text-violet-800">{capabilityOptions.find((item) => item.value === value)?.label || value}</span>
                                            ))}
                                        </div>
                                        <div className="mt-4 flex items-center justify-between rounded-xl bg-slate-50 p-3 text-sm">
                                            <span className="font-bold text-slate-600">وحدات التخزين</span>
                                            <strong className="num text-lg text-slate-950">{section.cabinet_count || 0}</strong>
                                        </div>
                                        <div className="mt-3 space-y-2">
                                            {(section.cabinets || []).slice(0, 4).map((cabinet) => (
                                                <button key={cabinet.id} type="button" onClick={() => setSelectedNode({ type: "cabinet", id: cabinet.id, sectionId: section.id })} className="flex w-full items-center justify-between rounded-xl border border-slate-100 px-3 py-2 text-sm hover:border-violet-200 hover:bg-violet-50/50">
                                                    <span className="font-bold text-slate-700">{cabinet.name || `دولاب ${cabinet.cabinet_number}`}</span>
                                                    <span className="num text-xs text-slate-400">{cabinet.total_locations} خانة</span>
                                                </button>
                                            ))}
                                        </div>
                                        {(section.capabilities || []).includes("cabinets") && (
                                            <button type="button" onClick={() => openStorageDialog(`section:${section.id}`)} className="mt-3 flex w-full items-center justify-center gap-2 rounded-xl border border-violet-200 px-3 py-2 text-xs font-extrabold text-violet-800 hover:bg-violet-50"><Plus size={15} weight="bold" /> إضافة وحدة تخزين</button>
                                        )}
                                    </article>
                                );
                            })}

                            <button type="button" onClick={openSectionDialog} className="flex min-h-[210px] flex-col items-center justify-center rounded-2xl border-2 border-dashed border-violet-200 bg-violet-50/40 p-5 text-center hover:border-violet-400 hover:bg-violet-50">
                                <Plus size={30} weight="duotone" className="text-violet-700" />
                                <span className="mt-2 font-black text-violet-950">إضافة قسم تشغيلي</span>
                                <span className="mt-1 text-xs leading-5 text-violet-600">تخزين، تركيب، نحت، شحن أو خط إنتاج داخل القسم نفسه.</span>
                            </button>
                        </div>
                    )}
                </main>

                <aside>
                    <div className="mb-3 px-1">
                        <h2 className="font-black text-slate-950">لوحة العنصر</h2>
                        <p className="text-[11px] text-slate-400">البيانات والإجراءات حسب اختيارك</p>
                    </div>
                    {renderInspector()}
                </aside>
            </section>

            {dialog === "branch" && (
                <Modal title="إنشاء فرع جديد" subtitle="الموقع والرقم والكود تُدار تلقائيًا حسب المدينة." onClose={() => setDialog(null)}>
                    <form onSubmit={createBranch} className="space-y-4">
                        <div className="grid gap-4 sm:grid-cols-2">
                            <Field label="اسم الفرع"><Input required value={branchForm.name} onChange={(event) => setBranchForm({ ...branchForm, name: event.target.value })} placeholder="مثال: بيت العود" /></Field>
                            <Field label="الدولة">
                                <Select value={branchForm.country} onChange={(event) => changeCountry(event.target.value)}>
                                    {catalog.countries.map((row) => <option key={row.name} value={row.name}>{row.name}</option>)}
                                </Select>
                            </Field>
                            <Field label="المدينة">
                                <Select value={branchForm.city} onChange={(event) => setBranchForm({ ...branchForm, city: event.target.value })}>
                                    {(countryRow?.cities || []).map((city) => <option key={city} value={city}>{city}</option>)}
                                </Select>
                            </Field>
                            <Field label="رقم الفرع" hint="يُنشأ تلقائيًا داخل المدينة"><Input disabled value="تلقائي" /></Field>
                            <Field label="الحي"><Input required value={branchForm.district} onChange={(event) => setBranchForm({ ...branchForm, district: event.target.value })} /></Field>
                            <Field label="الشارع"><Input required value={branchForm.street} onChange={(event) => setBranchForm({ ...branchForm, street: event.target.value })} /></Field>
                        </div>
                        <div className="flex justify-end gap-2 pt-2">
                            <button type="button" onClick={() => setDialog(null)} className="rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-bold text-slate-600">إلغاء</button>
                            <button disabled={loading} className="rounded-xl bg-violet-700 px-5 py-2.5 text-sm font-bold text-white disabled:opacity-50">إنشاء الفرع</button>
                        </div>
                    </form>
                </Modal>
            )}

            {dialog === "section" && (
                <Modal title="إضافة قسم تشغيلي" subtitle="القسم مرن ويمكن أن يجمع التخزين والتركيب والنحت والتغليف وخط الإنتاج." onClose={() => setDialog(null)}>
                    <form onSubmit={createSection} className="space-y-4">
                        <div className="grid gap-4 sm:grid-cols-2">
                            <Field label="عدد الأقسام" hint="عند إنشاء أكثر من قسم تُسمّى وتُرقّم تلقائيًا"><Input type="number" min="1" max="50" value={sectionForm.count} onChange={(event) => setSectionForm({ ...sectionForm, count: event.target.value })} /></Field>
                            <Field label="قالب سريع">
                                <Select value={sectionForm.template} onChange={(event) => applyTemplate(event.target.value)}>
                                    {Object.keys(TEMPLATE_LABELS).map((value) => <option key={value} value={value}>{TEMPLATE_LABELS[value]}</option>)}
                                </Select>
                            </Field>
                            {Number(sectionForm.count || 1) === 1 && <Field label="اسم القسم" hint="اختياري؛ النظام يولد قسم 1، قسم 2..."><Input value={sectionForm.name} onChange={(event) => setSectionForm({ ...sectionForm, name: event.target.value })} placeholder="مثال: التركيب والنحت" /></Field>}
                            {Number(sectionForm.count || 1) === 1 && <Field label="ملاحظات"><Input value={sectionForm.notes} onChange={(event) => setSectionForm({ ...sectionForm, notes: event.target.value })} /></Field>}
                        </div>
                        <div>
                            <p className="mb-2 text-xs font-extrabold text-slate-700">قدرات القسم</p>
                            <div className="grid gap-2 sm:grid-cols-2">
                                {capabilityOptions.map((item) => {
                                    const checked = sectionForm.capabilities.includes(item.value);
                                    return (
                                        <label key={item.value} className={`flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-2.5 text-sm font-bold transition ${checked ? "border-violet-300 bg-violet-50 text-violet-900" : "border-slate-200 text-slate-600 hover:bg-slate-50"}`}>
                                            <input type="checkbox" checked={checked} onChange={() => toggleCapability(item.value)} />
                                            {item.label}
                                        </label>
                                    );
                                })}
                            </div>
                        </div>
                        <div className="flex justify-end gap-2 pt-2">
                            <button type="button" onClick={() => setDialog(null)} className="rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-bold text-slate-600">إلغاء</button>
                            <button disabled={loading} className="rounded-xl bg-violet-700 px-5 py-2.5 text-sm font-bold text-white disabled:opacity-50">إنشاء القسم</button>
                        </div>
                    </form>
                </Modal>
            )}

            {dialog === "storage" && (
                <Modal title="إضافة وحدة تخزين" subtitle="نموذج واحد فقط؛ اختر موقع الوحدة داخل المساحة الرئيسية أو أحد الأقسام." onClose={() => setDialog(null)}>
                    <form onSubmit={createStorage} className="space-y-4">
                        <Field label="الموقع داخل الفرع">
                            <Select value={storageForm.target} onChange={(event) => setStorageForm({ ...storageForm, target: event.target.value })}>
                                {storageTargets.map((target) => <option key={target.value} value={target.value}>{target.label}</option>)}
                            </Select>
                        </Field>
                        <div className="grid gap-4 sm:grid-cols-2">
                            <Field label="اسم الوحدة" hint="اختياري؛ الرقم والكود تلقائيان"><Input value={storageForm.cabinet_name} onChange={(event) => setStorageForm({ ...storageForm, cabinet_name: event.target.value })} placeholder="مثال: دولاب السلاسل" /></Field>
                            <Field label="نوع الاستخدام">
                                <Select value={storageForm.purpose} onChange={(event) => setStorageForm({ ...storageForm, purpose: event.target.value })}>
                                    {PURPOSES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                                </Select>
                            </Field>
                            <Field label="عدد الصفوف"><Input type="number" min="1" max="100" required value={storageForm.length} onChange={(event) => setStorageForm({ ...storageForm, length: event.target.value })} /></Field>
                            <Field label="الخانات في كل صف"><Input type="number" min="1" max="100" required value={storageForm.width} onChange={(event) => setStorageForm({ ...storageForm, width: event.target.value })} /></Field>
                            <Field label="سعة الخانة" hint="اختياري"><Input type="number" min="1" value={storageForm.max_items_per_location} onChange={(event) => setStorageForm({ ...storageForm, max_items_per_location: event.target.value })} /></Field>
                            <div className="rounded-xl border border-violet-100 bg-violet-50 p-3">
                                <span className="block text-[11px] font-bold text-violet-600">إجمالي الخانات</span>
                                <strong className="num mt-1 block text-2xl font-black text-violet-950">{Number(storageForm.length || 0) * Number(storageForm.width || 0)}</strong>
                            </div>
                        </div>
                        <div className="flex justify-end gap-2 pt-2">
                            <button type="button" onClick={() => setDialog(null)} className="rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-bold text-slate-600">إلغاء</button>
                            <button disabled={loading} className="rounded-xl bg-violet-700 px-5 py-2.5 text-sm font-bold text-white disabled:opacity-50">إنشاء وحدة التخزين والخانات</button>
                        </div>
                    </form>
                </Modal>
            )}
        </div>
    );
}
