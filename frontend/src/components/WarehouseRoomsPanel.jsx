import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const FALLBACK_CAPABILITIES = [
    ["cabinets", "دواليب وخانات"],
    ["workstations", "محطات عمل"],
    ["assembly", "تركيب"],
    ["engraving", "نحت ونقش"],
    ["packing", "تغليف"],
    ["shipping_labeling", "شحن وعنونة"],
    ["quality_control", "فحص جودة"],
    ["waiting_areas", "مناطق انتظار"],
    ["equipment", "أجهزة ومعدات"],
    ["production_line", "خط إنتاج"],
    ["office", "إدارة ومكاتب"],
    ["worker_housing", "سكن عمال"],
    ["returns", "مرتجعات"],
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
    custom: "مخصص",
    storage: "تخزين",
    assembly: "تركيب",
    engraving: "نحت ونقش",
    shipping: "شحن وعنونة",
    production: "خط إنتاج",
    administration: "إدارة",
};

function Select(props) {
    return <select {...props} className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-brand" />;
}

function Input(props) {
    return <input {...props} className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-brand" />;
}

function Field({ label, hint, children }) {
    return (
        <label className="block">
            <span className="mb-1 block text-xs font-bold text-slate-600">{label}</span>
            {children}
            {hint && <span className="mt-1 block text-[11px] text-slate-400">{hint}</span>}
        </label>
    );
}

export default function WarehouseRoomsPanel() {
    const [branches, setBranches] = useState([]);
    const [branchId, setBranchId] = useState("");
    const [sections, setSections] = useState([]);
    const [sectionId, setSectionId] = useState("");
    const [capabilityOptions, setCapabilityOptions] = useState(FALLBACK_CAPABILITIES.map(([value, label]) => ({ value, label })));
    const [templates, setTemplates] = useState(FALLBACK_TEMPLATES);
    const [loading, setLoading] = useState(false);
    const [bulkCount, setBulkCount] = useState(1);
    const [template, setTemplate] = useState("storage");
    const [sectionForm, setSectionForm] = useState({ name: "", capabilities: ["cabinets"], notes: "" });
    const [cabinetForm, setCabinetForm] = useState({ cabinet_name: "", length: 4, width: 6, purpose: "permanent_storage", max_items_per_location: "" });

    const selectedSection = useMemo(
        () => sections.find((row) => row.id === sectionId) || null,
        [sections, sectionId],
    );
    const sectionAllowsCabinets = selectedSection?.capabilities?.includes("cabinets");

    const loadBranches = async () => {
        const response = await api.get("/warehouse-locations/warehouses");
        const items = response.data?.items || [];
        setBranches(items);
        if (!branchId && items.length) setBranchId(items[0].id);
    };

    const loadSections = async (id) => {
        if (!id) {
            setSections([]);
            setSectionId("");
            return;
        }
        const response = await api.get(`/warehouse-locations-v2/warehouses/${id}/sections`);
        const items = response.data?.items || [];
        setSections(items);
        if (sectionId && !items.some((row) => row.id === sectionId)) setSectionId("");
    };

    useEffect(() => {
        Promise.all([
            loadBranches(),
            api.get("/warehouse-locations-v2/section-capabilities").then((response) => {
                setCapabilityOptions(response.data?.items || capabilityOptions);
                setTemplates(response.data?.recommended || FALLBACK_TEMPLATES);
            }).catch(() => null),
        ]).catch(() => toast.error("تعذر تحميل الفروع والأقسام"));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        loadSections(branchId).catch(() => toast.error("تعذر تحميل أقسام الفرع"));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [branchId]);

    const applyTemplate = (value) => {
        setTemplate(value);
        if (value !== "custom") {
            setSectionForm((current) => ({ ...current, capabilities: templates[value] || [] }));
        }
    };

    const toggleCapability = (value) => {
        setTemplate("custom");
        setSectionForm((current) => ({
            ...current,
            capabilities: current.capabilities.includes(value)
                ? current.capabilities.filter((item) => item !== value)
                : [...current.capabilities, value],
        }));
    };

    const createSingleSection = async (event) => {
        event.preventDefault();
        if (!branchId) return toast.warning("اختر الفرع أولًا");
        setLoading(true);
        try {
            const response = await api.post("/warehouse-locations-v2/sections", {
                warehouse_id: branchId,
                name: sectionForm.name || null,
                capabilities: sectionForm.capabilities,
                notes: sectionForm.notes || null,
            });
            toast.success(`تم إنشاء القسم رقم ${response.data.section_number}`);
            setSectionForm({ name: "", capabilities: templates.storage || ["cabinets"], notes: "" });
            setTemplate("storage");
            await loadSections(branchId);
            setSectionId(response.data.id);
        } catch (error) {
            toast.error(error?.response?.data?.detail?.message || "تعذر إنشاء القسم");
        } finally {
            setLoading(false);
        }
    };

    const createBulkSections = async () => {
        if (!branchId) return toast.warning("اختر الفرع أولًا");
        setLoading(true);
        try {
            const response = await api.post("/warehouse-locations-v2/sections/bulk", {
                warehouse_id: branchId,
                count: Number(bulkCount),
                default_capabilities: sectionForm.capabilities,
            });
            toast.success(`تم إنشاء ${response.data.created_count} أقسام وترقيمها تلقائيًا`);
            await loadSections(branchId);
        } catch (error) {
            toast.error(error?.response?.data?.detail?.message || "تعذر إنشاء الأقسام");
        } finally {
            setLoading(false);
        }
    };

    const createCabinet = async (event) => {
        event.preventDefault();
        if (!sectionId) return toast.warning("اختر القسم أولًا");
        setLoading(true);
        try {
            const response = await api.post(`/warehouse-locations-v2/sections/${sectionId}/cabinets`, {
                ...cabinetForm,
                length: Number(cabinetForm.length),
                width: Number(cabinetForm.width),
                max_items_per_location: cabinetForm.max_items_per_location ? Number(cabinetForm.max_items_per_location) : null,
            });
            toast.success(`تم إنشاء الدولاب رقم ${response.data.cabinet.cabinet_number} داخل القسم`);
            setCabinetForm({ cabinet_name: "", length: 4, width: 6, purpose: "permanent_storage", max_items_per_location: "" });
            await loadSections(branchId);
        } catch (error) {
            toast.error(error?.response?.data?.detail?.message || "تعذر إنشاء الدولاب داخل القسم");
        } finally {
            setLoading(false);
        }
    };

    return (
        <section className="space-y-5" dir="rtl">
            <div className="rounded-xl border border-violet-100 bg-violet-50 p-4">
                <h2 className="text-lg font-extrabold text-violet-950">أقسام الفرع الديناميكية</h2>
                <p className="mt-1 text-sm text-violet-800">كل قسم يمكن أن يجمع بين التخزين والتركيب والنحت والشحن وخط الإنتاج. لا يوجد نوع واحد يقيّد القسم.</p>
            </div>

            <div className="rounded-xl border bg-white p-5">
                <div className="grid gap-3 md:grid-cols-3">
                    <Field label="الفرع">
                        <Select value={branchId} onChange={(event) => setBranchId(event.target.value)} required>
                            <option value="">اختر الفرع</option>
                            {branches.map((row) => <option key={row.id} value={row.id}>{row.name} — {row.city} — رقم {row.warehouse_number}</option>)}
                        </Select>
                    </Field>
                    <Field label="عدد الأقسام" hint="ينشئها النظام دفعة واحدة مع ترقيم تلقائي">
                        <Input type="number" min="1" max="50" value={bulkCount} onChange={(event) => setBulkCount(event.target.value)} />
                    </Field>
                    <div className="flex items-end">
                        <button type="button" disabled={loading} onClick={createBulkSections} className="w-full rounded-lg border border-violet-200 bg-violet-50 px-4 py-2 text-sm font-bold text-violet-800 disabled:opacity-50">إنشاء الأقسام تلقائيًا</button>
                    </div>
                </div>
            </div>

            <div className="grid gap-5 xl:grid-cols-2">
                <form onSubmit={createSingleSection} className="rounded-xl border bg-white p-5">
                    <h3 className="mb-4 text-lg font-extrabold">إضافة قسم وتحديد قدراته</h3>
                    <div className="grid gap-3 md:grid-cols-2">
                        <Field label="اسم القسم" hint="اختياري؛ النظام يقترح قسم 1، قسم 2... تلقائيًا">
                            <Input placeholder="مثال: التركيب والنحت" value={sectionForm.name} onChange={(event) => setSectionForm({ ...sectionForm, name: event.target.value })} />
                        </Field>
                        <Field label="قالب سريع">
                            <Select value={template} onChange={(event) => applyTemplate(event.target.value)}>
                                {Object.keys(TEMPLATE_LABELS).map((value) => <option key={value} value={value}>{TEMPLATE_LABELS[value]}</option>)}
                            </Select>
                        </Field>
                    </div>

                    <div className="mt-4">
                        <p className="mb-2 text-xs font-bold text-slate-600">قدرات القسم — اختر أكثر من خيار</p>
                        <div className="grid gap-2 sm:grid-cols-2">
                            {capabilityOptions.map((item) => {
                                const checked = sectionForm.capabilities.includes(item.value);
                                return (
                                    <label key={item.value} className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm font-bold transition ${checked ? "border-violet-300 bg-violet-50 text-violet-900" : "border-slate-200 bg-white text-slate-600"}`}>
                                        <input type="checkbox" checked={checked} onChange={() => toggleCapability(item.value)} />
                                        {item.label}
                                    </label>
                                );
                            })}
                        </div>
                    </div>

                    <div className="mt-3"><Field label="ملاحظات (اختياري)"><Input value={sectionForm.notes} onChange={(event) => setSectionForm({ ...sectionForm, notes: event.target.value })} /></Field></div>
                    <button disabled={loading} className="mt-4 rounded-lg bg-violet-700 px-4 py-2 text-sm font-bold text-white disabled:opacity-50">إنشاء القسم</button>
                </form>

                <form onSubmit={createCabinet} className="rounded-xl border bg-white p-5">
                    <h3 className="mb-4 text-lg font-extrabold">إضافة دولاب داخل قسم</h3>
                    <Field label="القسم">
                        <Select value={sectionId} onChange={(event) => setSectionId(event.target.value)} required>
                            <option value="">اختر القسم</option>
                            {sections.map((row) => <option key={row.id} value={row.id}>{row.name} — قسم رقم {row.section_number || row.room_number}{row.capabilities?.includes("cabinets") ? "" : " — بدون قدرة الدواليب"}</option>)}
                        </Select>
                    </Field>
                    {selectedSection && !sectionAllowsCabinets && <p className="mt-3 rounded-lg bg-amber-50 p-3 text-sm font-bold text-amber-800">هذا القسم لا يملك قدرة «دواليب وخانات». لن يسمح النظام بإضافة دولاب إليه.</p>}
                    <div className="mt-3 grid gap-3 md:grid-cols-2">
                        <Field label="اسم الدولاب" hint="اختياري؛ الرقم والكود تلقائيان"><Input value={cabinetForm.cabinet_name} onChange={(event) => setCabinetForm({ ...cabinetForm, cabinet_name: event.target.value })} /></Field>
                        <Field label="عدد الخانات بالطول"><Input type="number" min="1" max="100" required value={cabinetForm.length} onChange={(event) => setCabinetForm({ ...cabinetForm, length: event.target.value })} /></Field>
                        <Field label="عدد الخانات بالعرض"><Input type="number" min="1" max="100" required value={cabinetForm.width} onChange={(event) => setCabinetForm({ ...cabinetForm, width: event.target.value })} /></Field>
                        <Field label="سعة الخانة (اختياري)"><Input type="number" min="1" value={cabinetForm.max_items_per_location} onChange={(event) => setCabinetForm({ ...cabinetForm, max_items_per_location: event.target.value })} /></Field>
                    </div>
                    <button disabled={loading || !sectionAllowsCabinets} className="mt-4 rounded-lg bg-brand px-4 py-2 text-sm font-bold text-white disabled:opacity-50">إنشاء الدولاب والخانات</button>
                </form>
            </div>

            <div className="rounded-xl border bg-white p-5">
                <div className="mb-4 flex items-center justify-between"><h3 className="text-lg font-extrabold">أقسام الفرع</h3><span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-bold">{sections.length} قسم</span></div>
                {!sections.length ? <div className="rounded-lg border border-dashed p-8 text-center text-sm text-slate-500">لا توجد أقسام. يمكن أن يبقى الفرع مفتوحًا بدواليب مباشرة، أو تنشئ الأقسام عند الحاجة.</div> : (
                    <div className="grid gap-4 lg:grid-cols-2">
                        {sections.map((section) => (
                            <article key={section.id} className="rounded-xl border p-4">
                                <div className="flex items-start justify-between gap-3">
                                    <div><h4 className="font-extrabold">قسم {section.section_number || section.room_number} — {section.name}</h4><p className="mt-1 text-xs text-slate-500">{section.code}</p></div>
                                    <span className="rounded-full bg-emerald-50 px-2 py-1 text-[11px] font-bold text-emerald-700">{section.cabinet_count || 0} دواليب</span>
                                </div>
                                <div className="mt-3 flex flex-wrap gap-1.5">
                                    {(section.capabilities || []).map((value) => <span key={value} className="rounded-full bg-violet-50 px-2 py-1 text-[11px] font-bold text-violet-800">{capabilityOptions.find((item) => item.value === value)?.label || value}</span>)}
                                </div>
                                {!!section.cabinets?.length && <div className="mt-3 flex flex-wrap gap-2">{section.cabinets.map((cabinet) => <span key={cabinet.id} className="rounded-lg border bg-slate-50 px-3 py-2 text-xs font-bold">دولاب {cabinet.cabinet_number} — {cabinet.total_locations} خانة</span>)}</div>}
                            </article>
                        ))}
                    </div>
                )}
            </div>
        </section>
    );
}
