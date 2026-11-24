import { useEffect, useMemo, useRef, useState } from "react";
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
    MagnifyingGlass,
    SquaresFour,
    ListBullets,
    CaretDown,
    CaretLeft,
    LockKey,
    ArrowClockwise,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import api from "../lib/api";
import InventorySuiteFrame, {InventoryPageHeader, InventorySheet} from "../components/inventory/suite/InventorySuiteFrame";
import "./warehouseHierarchy.css";

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
    is_primary: false,
    location_numbering: "cabinet-column-row",
    inventory_scope: "standalone",
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

function Field({label,hint,children}) {
    return <label className="mz-wh-field"><span>{label}</span>{children}{hint&&<small>{hint}</small>}</label>;
}
function Input(props) {return <input {...props} className={"mz-wh-input "+(props.className||"")}/>;}
function Select(props) {return <select {...props} className={"mz-wh-input "+(props.className||"")}/>;}

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

function locationCoordinates(cabinet, index, record) {
    const positive = value => Number.isInteger(Number(value)) && Number(value) > 0;
    const width = Number(cabinet.width);
    return {
        row: positive(record?.row) ? Number(record.row) : Math.floor(index / width) + 1,
        column: positive(record?.column) ? Number(record.column) : index % width + 1,
    };
}

export function cabinetLocationLabel(cabinet, index, record) {
    if (typeof record?.display_code === "string" && record.display_code.trim()) return record.display_code;
    const {row, column} = locationCoordinates(cabinet, index, record);
    if (cabinet.location_numbering === "cabinet-column-row") {
        const coordinates = [Number(cabinet.cabinet_number), column, row];
        if (!coordinates.every(value => Number.isInteger(value) && value > 0)) return "—";
        return coordinates.join(coordinates.every(value => value < 10) ? "" : "-");
    }
    return cabinet.location_numbering === "cabinet-row-slot"
        ? `${cabinet.cabinet_number}-${row}-${column}`
        : String(index + 1).padStart(3, "0");
}

function printCabinetPack(branch, section, cabinet, locations = []) {
    if (!branch || !section || !cabinet) return;
    const cabinetNumber = cabinet.cabinet_number || cabinet.code;
    const total = Number(cabinet.total_locations || 0);
    const compact = cabinet.location_numbering === "cabinet-row-slot";
    const byNumber = new Map(locations.map(record => [Number(record.number), record]));
    const columns = Number((compact ? cabinet.length : cabinet.width) || 0);
    const baseCode = `${branch.code}-${cabinet.code}`;
    const branchLabel = `${branch.name} — ${branch.city} — قسم ${section.section_number || section.room_number} — ${section.name}`;
    const cover = printablePage({ eyebrow: "رقم الدولاب", value: cabinetNumber, subtitle: branchLabel, code: baseCode });
    const columnPages = Array.from({ length: columns }, (_, index) => printablePage({
        eyebrow: `الدولاب ${cabinetNumber}`,
        value: `${compact ? "صف" : "عمود"} ${index + 1}`,
        subtitle: branchLabel,
        code: `${baseCode}-C${String(index + 1).padStart(2, "0")}`,
    })).join("");
    const locationPages = Array.from({ length: total }, (_, index) => {
        const number = String(index + 1).padStart(3, "0");
        const record = byNumber.get(index + 1);
        // Keep the creation contract for delegates without occupancy access.
        // Human labels never replace the full scanner value.
        const code = record?.barcode_value || record?.code || `${baseCode}-${number}`;
        return printablePage({ eyebrow: `الدولاب ${cabinetNumber} — الخانة`, value: cabinetLocationLabel(cabinet, index, record), subtitle: `${branchLabel} — امسح الباركود قبل وضع المنتج`, code, barcode: true });
    }).join("");
    const printWindow = window.open("", "_blank");
    if (!printWindow) return toast.error("اسمح بفتح النوافذ المنبثقة لطباعة الملصقات");
    printWindow.document.write(`<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"/><title>${escapeHtml(branch.name)} — ${escapeHtml(String(cabinetNumber))}</title><style>@page{size:A4 portrait;margin:0}*{box-sizing:border-box}body{margin:0;font-family:Arial,Tahoma,sans-serif;color:#0f172a;background:#fff}.print-page{position:relative;width:210mm;height:297mm;page-break-after:always;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:18mm;text-align:center;border:8mm solid #5b21b6}.print-page:last-child{page-break-after:auto}.brand{position:absolute;top:14mm;font-size:20pt;font-weight:900;letter-spacing:2px;color:#5b21b6}.eyebrow{font-size:26pt;font-weight:800;margin-bottom:8mm}.big-value{font-size:96pt;line-height:1;font-weight:900;direction:ltr}.subtitle{margin-top:10mm;font-size:18pt;font-weight:700;max-width:170mm}.code{margin-top:6mm;font:900 22pt monospace;direction:ltr;letter-spacing:1px}.barcode{width:165mm;height:34mm;margin-top:12mm}@media screen{body{background:#e2e8f0}.print-page{margin:10px auto;background:#fff;box-shadow:0 4px 18px rgba(15,23,42,.18)}}</style></head><body>${cover}${columnPages}${locationPages}<script>window.onload=()=>window.print();</script></body></html>`);
    printWindow.document.close();
}

function CapabilityPills({values,options}) {
    return <div className="mz-wh-capability-pills">{(values||[]).map(value=><span key={value}>{options.find(row=>row.value===value)?.label||value}</span>)}</div>;
}
export function compactWarehouseLocation(cabinet,index,record) {
    if(cabinet.location_numbering!=="cabinet-row-slot")return cabinetLocationLabel(cabinet,index,record);
    const {row,column}=locationCoordinates(cabinet,index,record);
    return "د"+cabinet.cabinet_number+" / ر"+row+" / خ"+column;
}
const locationStateLabel=(row)=>!row?"غير مقروءة":row.state==="disabled"?"غير متاحة":row.state==="empty"?"فارغة":row.occupancy?.items?.length||["occupied","partial_order","ready_to_ship"].includes(row.state)?"بها مخزون":({reserved:"محجوزة",maintenance:"صيانة"}[row.state]||"تحتاج مراجعة");

export default function WarehouseHierarchyWorkspace({ accessMode = null }) {
    const canWrite = accessMode !== "read";
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
    const [initialLoading,setInitialLoading]=useState(true),[readError,setReadError]=useState(""),[formError,setFormError]=useState("");
    const [locations,setLocations]=useState([]),[locationLoading,setLocationLoading]=useState(false),[locationError,setLocationError]=useState("");
    const [query,setQuery]=useState(""),[mapView,setMapView]=useState("map"),[binIndex,setBinIndex]=useState(0),[page,setPage]=useState(0);
    const sectionLane=useRef(0);
    const closeDialog=()=>{if(!loading){setDialog(null);setFormError("");setResetConfirmation("");}};
    const openDialog=(value)=>{setFormError("");setDialog(value);};

    const selectedBranch = useMemo(() => branches.find((row) => row.id === branchId) || null, [branches, branchId]);
    const selectedSection = useMemo(() => sections.find((row) => row.id === sectionId) || null, [sections, sectionId]);
    const selectedCabinet = useMemo(() => selectedSection?.cabinets?.find((row) => row.id === cabinetId) || null, [selectedSection, cabinetId]);
    const shortCoordinates = selectedCabinet?.location_numbering === "cabinet-column-row";
    const allCabinets = useMemo(() => sections.flatMap((row) => row.cabinets || []), [sections]);
    const totalLocations = useMemo(() => allCabinets.reduce((sum, row) => sum + Number(row.total_locations || 0), 0), [allCabinets]);
    const countryRow = useMemo(() => catalog.countries.find((row) => row.name === branchForm.country) || catalog.countries[0], [branchForm.country, catalog.countries]);
    const cabinetTotal = Number(cabinetForm.columns || 0) * Number(cabinetForm.slots_per_column || 0);

    async function loadBranches(preferredId = "") {
        const response = await api.get("/warehouse-locations/warehouses");
        const items = response.data?.items || [];
        setBranches(items);
        const nextId = preferredId && items.some((row) => row.id === preferredId) ? preferredId : items[0]?.id||"";
        setBranchId(nextId);
        if (!nextId) {
            setSections([]);
            setSectionId("");
            setCabinetId("");
        }
        return nextId;
    }

    async function loadSections(id,preferredSectionId="",preferredCabinetId="") {
        const lane=++sectionLane.current;
        if(!id){setSections([]);setSectionId("");setCabinetId("");return;}
        const response=await api.get("/warehouse-locations-v2/warehouses/"+id+"/sections");
        if(lane!==sectionLane.current)return;
        const items=response.data?.items||[];
        setSections(items);
        const nextSection=items.find(row=>row.id===preferredSectionId)||items[0];
        setSectionId(nextSection?.id||"");
        const nextCabinet=nextSection?.cabinets?.find(row=>row.id===preferredCabinetId)||nextSection?.cabinets?.[0];
        setCabinetId(nextCabinet?.id||"");
    }
    async function refreshHierarchy(){
        setInitialLoading(true);setReadError("");
        try{const id=await loadBranches(branchId);await loadSections(id,sectionId,cabinetId);}
        catch(error){setReadError(error?.response?.data?.detail?.message||"تعذر تحميل الفروع والمخازن.");}
        finally{setInitialLoading(false);}
    }
    useEffect(()=>{
        let active=true;
        Promise.all([
            api.get("/warehouse-locations-v2/catalog").then(response=>{if(active)setCatalog(response.data||FALLBACK_CATALOG);}).catch(()=>null),
            api.get("/warehouse-locations-v2/section-capabilities").then(response=>{if(active){setCapabilityOptions(response.data?.items||FALLBACK_CAPABILITIES);setTemplates(response.data?.recommended||FALLBACK_TEMPLATES);}}).catch(()=>null),
            loadBranches().then(id=>{if(active)return loadSections(id);}),
        ]).catch(()=>{if(active)setReadError("تعذر تحميل الفروع والمخازن.");}).finally(()=>{if(active)setInitialLoading(false);});
        return ()=>{active=false;sectionLane.current+=1;};
        // eslint-disable-next-line react-hooks/exhaustive-deps
    },[]);
    useEffect(()=>{
        setLocations([]);setLocationError("");setBinIndex(0);setPage(0);setQuery("");
        // Location occupancy is a manager-only reader; warehouse web delegates
        // keep their native scoped hierarchy without an unauthorized extra read.
        if(!cabinetId||accessMode){setLocationLoading(false);return;}
        let active=true;setLocationLoading(true);
        api.get("/warehouse-locations/locations",{params:{cabinet_id:cabinetId,limit:1000}})
            .then(response=>{if(active)setLocations(response.data?.items||[]);})
            .catch(()=>{if(active)setLocationError("تعذر قراءة إشغال الخانات؛ تبقى الخريطة عرضًا للترقيم فقط.");})
            .finally(()=>{if(active)setLocationLoading(false);});
        return ()=>{active=false;};
    },[cabinetId,accessMode]);
    function chooseBranch(id){
        if(loading)return;setBranchId(id);setSections([]);setSectionId("");setCabinetId("");setReadError("");
        loadSections(id).catch(()=>setReadError("تعذر تحميل أقسام الفرع."));
    }
    function chooseSection(id){if(loading)return;setSectionId(id);setCabinetId(sections.find(row=>row.id===id)?.cabinets?.[0]?.id||"");}

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
        if(!canWrite||loading)return;
        setFormError("");
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
            const message=code === "city_not_in_country" ? "المدينة لا تتبع الدولة المحددة" : "تعذر إنشاء الفرع";setFormError(message);toast.error(message);
        } finally {
            setLoading(false);
        }
    }

    async function createSection(event) {
        event.preventDefault();
        if(!canWrite||loading)return;
        setFormError("");
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
            const message=error?.response?.data?.detail?.message || "تعذر إنشاء القسم";setFormError(message);toast.error(message);
        } finally {
            setLoading(false);
        }
    }

    async function createCabinet(event) {
        event.preventDefault();
        if(!canWrite||loading)return;
        setFormError("");
        if (!sectionId) return toast.warning("اختر القسم أولًا");
        if(![cabinetForm.columns,cabinetForm.slots_per_column].every(value=>Number.isInteger(Number(value))&&Number(value)>=1&&Number(value)<=100)||cabinetTotal>5000){setFormError("حدد من 1 إلى 100 صف وعمود، وبحد أقصى 5000 خانة للدولاب.");return;}
        if(cabinetForm.max_items_per_location!==""&&(!Number.isInteger(Number(cabinetForm.max_items_per_location))||Number(cabinetForm.max_items_per_location)<1||Number(cabinetForm.max_items_per_location)>100000)){setFormError("سعة الخانة عدد صحيح من 1 إلى 100000، أو تُترك فارغة.");return;}
        setLoading(true);
        try {
            const response = await api.post(`/warehouse-locations-v2/sections/${sectionId}/cabinets`, {
                cabinet_name: cabinetForm.cabinet_name || null,
                location_numbering: "cabinet-column-row",
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
            const message=error?.response?.data?.detail?.message || "تعذر إنشاء الدولاب";setFormError(message);toast.error(message);
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

    const address=selectedBranch?[selectedBranch.country,selectedBranch.city,selectedBranch.district,selectedBranch.street].filter(Boolean).join(" · "):"";
    const bins=useMemo(()=>{
        if(!selectedCabinet)return [];
        const byNumber=new Map(locations.map(row=>[Number(row.number),row]));
        return Array.from({length:Number(selectedCabinet.total_locations)||0},(_,index)=>{
            const record=byNumber.get(index+1),coordinates=locationCoordinates(selectedCabinet,index,record);
            return {index,row:coordinates.row,slot:coordinates.column,label:compactWarehouseLocation(selectedCabinet,index,record),record};
        });
    },[selectedCabinet,locations]);
    const needle=query.trim().toLocaleLowerCase('ar');
    const matching=bins.filter(bin=>!needle||[bin.label,cabinetLocationLabel(selectedCabinet,bin.index,bin.record),bin.record?.code,bin.record?.barcode_value,...(bin.record?.occupancy?.items||[]).flatMap(item=>[item.product_name,item.name,item.sku])].join(' ').toLocaleLowerCase('ar').includes(needle));
    const pageCount=Math.max(1,Math.ceil(matching.length/120)),safePage=Math.min(page,pageCount-1),visibleBins=matching.slice(safePage*120,(safePage+1)*120);
    const shelves=[...new Set(visibleBins.map(bin=>bin.row))],selectedBin=bins[binIndex]||bins[0];
    const selectedItems=selectedBin?.record?.occupancy?.items||[];
    const empty=(title,description,action,buttonLabel)=><div className="mz-wh-empty"><Warehouse size={43}/><h2>{title}</h2><p>{description}</p>{action&&canWrite&&<button type="button" className="mz-inventory-button is-primary" onClick={action}><Plus size={18}/>{buttonLabel}</button>}</div>;
    const mainContent=initialLoading?<p role="status" className="mz-wh-loading">جارٍ تحميل هيكل المستودعات…</p>:readError?<p className="mz-wh-loading">تعذر عرض الهيكل. أعد القراءة قبل إنشاء فرع أو قسم جديد.</p>:!selectedBranch?empty("ابدأ بإضافة فرع","فرع ثم قسم ثم دولاب، وتُنشأ الخانات بأكوادها تلقائيًا.",()=>openDialog("branch"),"إضافة أول فرع"):selectedCabinet&&selectedSection?<>
        <nav className="mz-wh-breadcrumb" aria-label="مسار المستودع"><button onClick={()=>{setSectionId("");setCabinetId("");}}>{selectedBranch.name}</button><CaretLeft size={13}/><button onClick={()=>setCabinetId("")}>{selectedSection.name}</button><CaretLeft size={13}/><strong>{"د"+selectedCabinet.cabinet_number}</strong></nav>
        <div className="mz-wh-cabinet-header"><div><h2>{selectedCabinet.name||"دولاب "+selectedCabinet.cabinet_number}<span>{"د"+selectedCabinet.cabinet_number}</span></h2><p><MapPin size={15}/>{selectedBranch.city} · {PURPOSES.find(row=>row[0]===selectedCabinet.purpose)?.[1]||"غير محدد"}</p></div><button type="button" className="mz-inventory-button" onClick={()=>printCabinetPack(selectedBranch,selectedSection,selectedCabinet,locations)}><Printer size={17}/>طباعة ملصقات الدولاب</button></div>
        <div className="mz-wh-stats"><div><strong>{selectedCabinet.total_locations}</strong><span>خانة</span></div><div><strong>{selectedCabinet.length}</strong><span>{shortCoordinates?"صفوف":"رفوف"}</span></div><div><strong>{selectedCabinet.width}</strong><span>{shortCoordinates?"أعمدة":"خانات لكل رف"}</span></div><div><strong>{accessMode?"—":locations.length}</strong><span>حالة مقروءة</span></div></div>
        <div className="mz-wh-map-toolbar"><label className="mz-wh-search"><MagnifyingGlass size={19}/><input aria-label="بحث خانات المستودع" value={query} onChange={event=>{setQuery(event.target.value);setPage(0);}} placeholder="رمز الخانة، الصنف أو الرقم المخزني…"/></label><div className="mz-wh-view-switch" aria-label="عرض الخانات"><button type="button" aria-pressed={mapView==="map"} onClick={()=>setMapView("map")}><SquaresFour size={17}/>خريطة</button><button type="button" aria-pressed={mapView==="list"} onClick={()=>setMapView("list")}><ListBullets size={17}/>قائمة</button></div></div>
        {locationLoading&&<p role="status" className="mz-wh-note">جارٍ قراءة إشغال الخانات…</p>}
        {locationError&&<p role="alert" className="mz-wh-note">{locationError}</p>}
        {accessMode&&<p className="mz-wh-note">يعرض هذا النطاق هيكل الخانات وترقيمها؛ الإشغال غير مقروء ضمن هذه الصلاحية.</p>}
        {!matching.length?empty("لا توجد خانات تطابق البحث","ابحث بالرمز أو باسم صنف ضمن بيانات الإشغال المقروءة."):mapView==="map"?<div className="mz-wh-shelves" aria-label={shortCoordinates?"خريطة الصفوف والأعمدة":"خريطة الرفوف والخانات"}>{shelves.map(shelf=><section className="mz-wh-shelf" key={shelf} aria-label={(shortCoordinates?"الصف ":"الرف ")+shelf}><span className="mz-wh-shelf-label">{(shortCoordinates?"ص":"ر")+shelf}</span><div className="mz-wh-bin-grid" style={{'--wh-columns':Math.min(Number(selectedCabinet.width)||1,6),'--wh-mobile-columns':Math.min(Number(selectedCabinet.width)||1,3)}}>{visibleBins.filter(bin=>bin.row===shelf).map(bin=><button type="button" key={bin.index} className={"mz-wh-bin "+(!bin.record?"is-unknown":bin.record.state==="disabled"?"is-disabled":bin.record.state==="empty"?"is-empty":"is-occupied")+(selectedBin?.index===bin.index?" is-selected":"")} aria-pressed={selectedBin?.index===bin.index} aria-label={bin.label+"، "+locationStateLabel(bin.record)} onClick={()=>setBinIndex(bin.index)}><strong>{selectedCabinet.location_numbering==="cabinet-row-slot"?"خ"+bin.slot:cabinetLocationLabel(selectedCabinet,bin.index,bin.record)}</strong><small>{locationStateLabel(bin.record)}</small></button>)}</div></section>)}</div>:<div className="mz-wh-bin-list">{visibleBins.map(bin=><button type="button" key={bin.index} className={selectedBin?.index===bin.index?"is-selected":""} onClick={()=>setBinIndex(bin.index)}><strong>{bin.label}</strong><span>{bin.record?.occupancy?.items?.map(item=>item.product_name||item.name||item.sku).filter(Boolean).join("، ")||locationStateLabel(bin.record)}</span><CaretLeft size={15}/></button>)}</div>}
        <div className="mz-wh-legend"><span><i className="is-empty"/>فارغة</span><span><i className="is-occupied"/>بها مخزون</span><span><i className="is-disabled"/>غير متاحة</span><span><i className="is-unknown"/>غير مقروءة</span></div>
        {pageCount>1&&<div className="mz-wh-pagination"><button type="button" disabled={safePage===0} onClick={()=>setPage(value=>value-1)}>السابق</button><span>{"صفحة "+(safePage+1)+" من "+pageCount+" · "+matching.length+" خانة"}</span><button type="button" disabled={safePage===pageCount-1} onClick={()=>setPage(value=>value+1)}>التالي</button></div>}
        {selectedBin&&<section className="mz-wh-bin-details" aria-label="تفاصيل الخانة المحددة"><div className="mz-wh-bin-details-heading"><div><small>الخانة المحددة</small><h3>{selectedBin.label}</h3></div><span>{locationStateLabel(selectedBin.record)}</span></div>{selectedItems.length?<div className="mz-wh-occupancy">{selectedItems.map((item,index)=><article key={item.receipt_id||item.product_id||index}><Package size={22}/><div><strong>{item.product_name||item.name||item.sku||"صنف مخزني"}</strong><small>{item.sku}{item.preparation_state?" · "+({ready_complete:"جاهز",requires_preparation:"يحتاج تجهيزًا"}[item.preparation_state]||item.preparation_state):""}</small>{item.specifications&&<small>{Object.entries(item.specifications).map(([name,value])=>name+": "+value).join(" · ")}</small>}</div><span>{item.quantity==null?"—":item.quantity}{item.unit?" "+({piece:"قطعة",gram:"غرام",g:"غرام",kilogram:"كجم",kg:"كجم",meter:"متر",m:"متر",liter:"لتر",l:"لتر"}[item.unit]||item.unit):""}</span></article>)}</div>:<p>{selectedBin.record?.state==="empty"?"الخانة فارغة حسب آخر قراءة للمستودع.":selectedBin.record?"راجع حالة الخانة عند الاستلام قبل وضع أي صنف.":"الإشغال غير مثبت في هذا العرض؛ لا يعني ذلك أن الخانة فارغة."}</p>}<footer><span>{selectedBranch.name+" · "+selectedSection.name+" · "+selectedCabinet.name}</span>{selectedBin.record&&<bdi>{selectedBin.record.barcode_value||selectedBin.record.code}</bdi>}</footer></section>}
    </>:selectedSection?<><div className="mz-wh-section-heading"><div><h2>{selectedSection.name}</h2><CapabilityPills values={selectedSection.capabilities} options={capabilityOptions}/></div>{canWrite&&<button type="button" className="mz-inventory-button is-primary" onClick={()=>{setCabinetForm(EMPTY_CABINET);openDialog("cabinet");}}><Plus size={18}/>إضافة دولاب</button>}</div>{!selectedSection.cabinets?.length?empty("القسم جاهز لاستقبال الدواليب","حدد عدد الصفوف والأعمدة لإنشاء الخانات دفعة واحدة.",()=>openDialog("cabinet"),"إضافة أول دولاب"):<div className="mz-wh-cards">{selectedSection.cabinets.map(cabinet=><button type="button" key={cabinet.id} onClick={()=>setCabinetId(cabinet.id)}><Cube size={25}/><h3>{cabinet.name}</h3><strong>{"د"+cabinet.cabinet_number}</strong><small>{cabinet.total_locations+" خانة · "+cabinet.length+(cabinet.location_numbering==="cabinet-column-row"?" صفوف":" رفوف")}</small></button>)}</div>}</>:<><div className="mz-wh-section-heading"><div><h2>{selectedBranch.name}</h2><p>{address}</p></div>{canWrite&&<button type="button" className="mz-inventory-button is-primary" onClick={()=>openDialog("section")}><Plus size={18}/>إضافة قسم</button>}</div>{!sections.length?empty("أضف أول قسم","كل دولاب يرتبط بقسم؛ اختر قالبًا للتخزين أو التجهيز أو الشحن.",()=>openDialog("section"),"إضافة أول قسم"):<div className="mz-wh-cards">{sections.map(section=><button type="button" key={section.id} onClick={()=>chooseSection(section.id)}><Wrench size={25}/><h3>{section.name}</h3><small>{(section.cabinet_count||0)+" دواليب"}</small><CapabilityPills values={section.capabilities} options={capabilityOptions}/></button>)}</div>}</>;
    return <InventorySuiteFrame active="warehouses"><div className="mz-warehouse" data-testid="warehouse-hierarchy-workspace">
        <InventoryPageHeader title="المستودعات" description="كل صنف في مكانه. من الفرع إلى أصغر خانة."><button type="button" className="mz-inventory-button" disabled={initialLoading||loading} onClick={refreshHierarchy} aria-label="تحديث المستودعات"><ArrowClockwise size={18}/>تحديث</button>{canWrite&&<button type="button" className="mz-inventory-button is-primary" disabled={loading||initialLoading||!!readError} onClick={()=>{if(selectedSection){setCabinetForm(EMPTY_CABINET);openDialog("cabinet");}else if(selectedBranch)openDialog("section");else openDialog("branch");}}><Plus size={18}/>{selectedSection?"إضافة دولاب":selectedBranch?"إضافة قسم":"إضافة فرع"}</button>}</InventoryPageHeader>
        {!canWrite&&<p className="mz-wh-readonly"><LockKey size={17}/>للعرض فقط · تظهر الفروع والأقسام المسموح لك بقراءتها.</p>}
        {readError&&<div className="mz-wh-error" role="alert">{readError}<button type="button" onClick={refreshHierarchy}>إعادة المحاولة</button></div>}
        <div className="mz-wh-layout"><aside className="mz-wh-tree" aria-label="هيكل المستودعات"><div className="mz-wh-tree-title"><Warehouse size={25}/><div><h2>مستودعات ميزان</h2><small>{branches.length+" فروع في هذا العرض"}</small></div></div><div className="mz-wh-mobile-branch"><label>الفرع<Select aria-label="اختيار الفرع" value={branchId} onChange={event=>chooseBranch(event.target.value)}>{branches.map(branch=><option key={branch.id} value={branch.id}>{branch.name}</option>)}</Select></label></div><div className="mz-wh-branches">{branches.map(branch=><div className={"mz-wh-branch-group "+(branch.id===branchId?"is-current":"")} key={branch.id}><button type="button" className={"mz-wh-tree-node mz-wh-branch "+(branch.id===branchId?"is-current":"")} disabled={loading} aria-expanded={branch.id===branchId} onClick={()=>chooseBranch(branch.id)}><Warehouse size={19}/><span>{branch.name}</span>{branch.id===branchId?<CaretDown size={14}/>:<CaretLeft size={14}/>}</button>{branch.id===branchId&&<div className="mz-wh-section-tree">{sections.map(section=><div key={section.id}><button type="button" className={"mz-wh-tree-node mz-wh-section "+(section.id===sectionId?"is-current":"")} disabled={loading} onClick={()=>chooseSection(section.id)}><Wrench size={16}/><span>{section.name}</span><small>{section.cabinet_count||0}</small></button>{section.id===sectionId&&<div className="mz-wh-cabinet-tree">{(section.cabinets||[]).map(cabinet=><button type="button" className={"mz-wh-tree-node mz-wh-cabinet "+(cabinet.id===cabinetId?"is-selected":"")} key={cabinet.id} disabled={loading} aria-current={cabinet.id===cabinetId?"true":undefined} onClick={()=>setCabinetId(cabinet.id)}><SquaresFour size={16}/><strong>{"د"+cabinet.cabinet_number}</strong><span>{cabinet.name}</span></button>)}</div>}</div>)}</div>}</div>)}</div>{canWrite&&<div className="mz-wh-tree-actions"><button type="button" disabled={loading||initialLoading||!!readError} onClick={()=>openDialog("branch")}><Plus size={16}/>فرع جديد</button><button type="button" disabled={loading||initialLoading||!!readError||!branchId} onClick={()=>openDialog("section")}><Plus size={16}/>قسم جديد</button></div>}<div className="mz-wh-tree-foot"><CheckCircle size={18}/><span>{!selectedCabinet||shortCoordinates?"دولاب ← عمود ← صف":"دولاب ← رف ← خانة"}<small>تسميات صغيرة، ومكان واضح.</small></span></div>{!accessMode&&!!branches.length&&<details className="mz-wh-advanced"><summary>إعدادات متقدمة</summary><button type="button" disabled={loading} onClick={()=>openDialog("reset")}><Trash size={15}/>بدء من الصفر</button></details>}</aside><main className="mz-wh-workspace" aria-label="مساحة المستودع">{mainContent}</main></div>
        {canWrite&&<><InventorySheet open={dialog==="branch"} onClose={closeDialog} busy={loading} title="إضافة فرع" description="الرقم والكود تلقائيان؛ اختر الدولة والمدينة وحدد عنوان الفرع." footer={<button type="submit" form="warehouse-branch-form" disabled={loading} className="mz-inventory-button is-primary">{loading?"جارٍ الحفظ…":"إنشاء الفرع"}</button>}><form id="warehouse-branch-form" onSubmit={createBranch} className="mz-wh-form">{formError&&<p role="alert" className="mz-wh-error">{formError}</p>}<fieldset disabled={loading}><Field label="اسم الفرع"><Input required value={branchForm.name} onChange={event=>setBranchForm({...branchForm,name:event.target.value})} placeholder="مثال: فرع الرياض"/></Field><div className="mz-wh-form-grid"><Field label="الدولة"><Select value={branchForm.country} onChange={event=>changeCountry(event.target.value)}>{catalog.countries.map(row=><option key={row.name}>{row.name}</option>)}</Select></Field><Field label="المدينة"><Select value={branchForm.city} onChange={event=>setBranchForm({...branchForm,city:event.target.value})}>{(countryRow?.cities||[]).map(city=><option key={city}>{city}</option>)}</Select></Field><Field label="الحي"><Input required value={branchForm.district} onChange={event=>setBranchForm({...branchForm,district:event.target.value})}/></Field><Field label="الشارع"><Input required value={branchForm.street} onChange={event=>setBranchForm({...branchForm,street:event.target.value})}/></Field></div><details><summary>إعدادات المخزن</summary><Field label="استخدام المخزن"><Select value={branchForm.inventory_scope} onChange={event=>setBranchForm({...branchForm,inventory_scope:event.target.value})}><option value="standalone">مخزون مستقل — ربط الطلبات مؤجل</option><option value="shared">مخزن للمسار الحالي</option></Select></Field><p className="mz-wh-note">ترتيب رقم الخانة: دولاب، عمود، صف. مثال: 121 للدولاب 1 والعمود 2 والصف 1.</p></details></fieldset></form></InventorySheet>
        <InventorySheet open={dialog==="section"} onClose={closeDialog} busy={loading} title="إضافة قسم" description={"داخل "+(selectedBranch?.name||"الفرع")+". كل قسم يدعم الدواليب ويمكن أن يجمع عدة وظائف."} footer={<button type="submit" form="warehouse-section-form" disabled={loading} className="mz-inventory-button is-primary">{loading?"جارٍ الحفظ…":"إنشاء القسم"}</button>}><form id="warehouse-section-form" onSubmit={createSection} className="mz-wh-form">{formError&&<p role="alert" className="mz-wh-error">{formError}</p>}<fieldset disabled={loading}><Field label="اسم القسم"><Input required value={sectionForm.name} onChange={event=>setSectionForm({...sectionForm,name:event.target.value})} placeholder="مثال: قسم التخزين"/></Field><Field label="قالب سريع"><Select value={sectionForm.template} onChange={event=>applyTemplate(event.target.value)}>{Object.entries(TEMPLATE_LABELS).map(([value,label])=><option value={value} key={value}>{label}</option>)}</Select></Field><div className="mz-wh-capability-options"><h3>وظائف القسم</h3>{capabilityOptions.map(item=><label key={item.value}><input type="checkbox" checked={item.value==="cabinets"||sectionForm.capabilities.includes(item.value)} disabled={item.value==="cabinets"} onChange={()=>toggleCapability(item.value)}/>{item.label}{item.value==="cabinets"&&<small>إلزامي</small>}</label>)}</div><Field label="ملاحظات (اختياري)"><Input value={sectionForm.notes} onChange={event=>setSectionForm({...sectionForm,notes:event.target.value})}/></Field></fieldset></form></InventorySheet>
        <InventorySheet open={dialog==="cabinet"} onClose={closeDialog} busy={loading} title="إضافة دولاب" description={(selectedBranch?.name||"")+" · "+(selectedSection?.name||"")+". تُنشأ الخانات والترقيم والباركود تلقائيًا."} footer={<button type="submit" form="warehouse-cabinet-form" disabled={loading||cabinetTotal<1||cabinetTotal>5000} className="mz-inventory-button is-primary">{loading?"جارٍ الإنشاء…":"إنشاء الدولاب والخانات"}</button>}><form id="warehouse-cabinet-form" onSubmit={createCabinet} className="mz-wh-form">{formError&&<p role="alert" className="mz-wh-error">{formError}</p>}<fieldset disabled={loading}><Field label="اسم الدولاب (اختياري)" hint="الرقم والكود تلقائيان"><Input value={cabinetForm.cabinet_name} onChange={event=>setCabinetForm({...cabinetForm,cabinet_name:event.target.value})} placeholder="مثال: دولاب الملابس"/></Field><div className="mz-wh-form-grid"><Field label="عدد الصفوف"><Input required type="number" min="1" max="100" value={cabinetForm.slots_per_column} onChange={event=>setCabinetForm({...cabinetForm,slots_per_column:event.target.value})}/></Field><Field label="عدد الأعمدة"><Input required type="number" min="1" max="100" value={cabinetForm.columns} onChange={event=>setCabinetForm({...cabinetForm,columns:event.target.value})}/></Field></div><div className="mz-wh-generation"><strong>{cabinetTotal}</strong><span>خانة ستُنشأ تلقائيًا</span><small>{cabinetForm.slots_per_column+" صفوف × "+cabinetForm.columns+" أعمدة"}</small></div><p className="mz-wh-note">ترتيب رقم الخانة: دولاب، عمود، صف. مثال: <bdi>121</bdi> للدولاب 1 والعمود 2 والصف 1. تفصل شرطات بين الأرقام عندما يتجاوز أحدها 9.</p><Field label="نوع الاستخدام"><Select value={cabinetForm.purpose} onChange={event=>setCabinetForm({...cabinetForm,purpose:event.target.value})}>{PURPOSES.map(([value,label])=><option value={value} key={value}>{label}</option>)}</Select></Field><Field label="سعة كل خانة (اختياري)"><Input type="number" min="1" max="100000" value={cabinetForm.max_items_per_location} onChange={event=>setCabinetForm({...cabinetForm,max_items_per_location:event.target.value})}/></Field></fieldset></form></InventorySheet></>}
        {!accessMode&&<InventorySheet open={dialog==="reset"} onClose={closeDialog} busy={loading} title="بدء الفروع والمخازن من الصفر" description="إجراء حذف نهائي للفروع والأقسام والدواليب والخانات. لا يُنفذ قبل التأكيد الصريح." footer={<button type="submit" form="warehouse-reset-form" disabled={loading||resetConfirmation!=="حذف"} className="mz-wh-danger-button"><Trash size={17}/>حذف بيانات التجربة والبدء من الصفر</button>}><form id="warehouse-reset-form" onSubmit={resetWorkspace} className="mz-wh-form"><p className="mz-wh-error">سيُحذف هيكل المخازن وبيانات التجربة وتبدأ أرقامها من 1. لا يحذف هذا الإجراء الطلبات أو المنتجات أو بيانات سلة وقيود.</p><Field label="اكتب كلمة حذف للتأكيد"><Input value={resetConfirmation} onChange={event=>setResetConfirmation(event.target.value)} placeholder="حذف" disabled={loading}/></Field></form></InventorySheet>}
    </div></InventorySuiteFrame>;
}
