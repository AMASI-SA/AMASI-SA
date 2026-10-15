import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { UploadSimple, FileXls, X } from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";
import { todayISO } from "../lib/format";
import DateInput from "../components/DateInput";

const MAX_ANALYSIS_FILE_BYTES = 15 * 1024 * 1024;

const EMPTY_ILL = "https://static.prod-images.emergentagent.com/jobs/ab0374e5-2a04-4e34-b24c-447b0238a858/images/80b8ed57e8b03ecce86ccb201f79210f057fc21e43eab9fd1aa0648fc95c6bb3.png";

export default function UploadExcel() {
    const navigate = useNavigate();
    const inputRef = useRef(null);
    const [file, setFile] = useState(null);
    const [dragOver, setDragOver] = useState(false);
    const [busy, setBusy] = useState(false);

    const [name, setName] = useState("");
    const [date, setDate] = useState(todayISO());
    const [productCosts, setProductCosts] = useState("");

    const selectFile = (selected) => {
        if (!selected) {
            setFile(null);
            return;
        }
        if (!selected.name.toLowerCase().endsWith(".xlsx")) {
            toast.error("الصيغة المدعومة هي .xlsx فقط");
            setFile(null);
            return;
        }
        if (selected.size > MAX_ANALYSIS_FILE_BYTES) {
            toast.error("حجم الملف يتجاوز الحد المسموح (15 ميجابايت)");
            setFile(null);
            return;
        }
        setFile(selected);
    };

    const onDrop = (e) => {
        e.preventDefault();
        setDragOver(false);
        selectFile(e.dataTransfer.files?.[0]);
    };

    const submit = async (e) => {
        e.preventDefault();
        if (!file) {
            toast.error("الرجاء اختيار ملف Excel أولاً");
            return;
        }
        setBusy(true);
        try {
            const fd = new FormData();
            fd.append("file", file);
            const params = new URLSearchParams({
                name,
                date,
                product_costs: productCosts || "0",
            });
            const { data } = await api.post(`/analyses?${params.toString()}`, fd, {
                headers: { "Content-Type": "multipart/form-data" },
            });
            toast.success("تم استلام الملف — جاري المعالجة في الخلفية");
            navigate(`/import-jobs`);
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذر تحليل الملف");
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="space-y-8 animate-fade-in-up" data-testid="upload-page">
            <div>
                <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight text-foreground" style={{ fontFamily: "Tajawal" }}>
                    تحليل ملف Excel
                </h1>
                <p className="text-muted-foreground mt-2 text-base">
                    ارفع ملف Excel المُصدَّر من منصة سلة وأدخل تكلفة المنتجات للحصول على تقرير محاسبي كامل. تكاليف الإعلانات تُدار من صفحة "التكاليف اليومية".
                </p>
            </div>

            <form onSubmit={submit} className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* Dropzone */}
                <div className="lg:col-span-2">
                    <div
                        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                        onDragLeave={() => setDragOver(false)}
                        onDrop={onDrop}
                        onClick={() => inputRef.current?.click()}
                        data-testid="dropzone"
                        className={[
                            "rounded-xl border-2 border-dashed p-12 text-center cursor-pointer transition-colors bg-white",
                            dragOver ? "border-brand bg-accent" : "border-border hover:border-brand/50",
                        ].join(" ")}
                    >
                        <input
                            ref={inputRef}
                            type="file"
                            accept=".xlsx"
                            className="hidden"
                            onChange={(e) => selectFile(e.target.files?.[0])}
                            data-testid="file-input"
                        />
                        {file ? (
                            <div className="flex flex-col items-center gap-3">
                                <FileXls size={48} weight="duotone" className="text-brand" />
                                <div className="text-lg font-bold">{file.name}</div>
                                <div className="text-sm text-muted-foreground">
                                    {(file.size / 1024).toFixed(1)} كيلوبايت
                                </div>
                                <button
                                    type="button"
                                    onClick={(e) => { e.stopPropagation(); setFile(null); }}
                                    className="inline-flex items-center gap-1.5 text-sm text-red-600 hover:underline mt-1"
                                    data-testid="clear-file-btn"
                                >
                                    <X size={16} /> إزالة الملف
                                </button>
                            </div>
                        ) : (
                            <div className="flex flex-col items-center gap-3">
                                <img src={EMPTY_ILL} alt="upload" className="w-32 h-32 object-contain opacity-90" />
                                <UploadSimple size={28} className="text-brand" />
                                <div className="text-lg font-bold text-foreground">اسحب الملف هنا أو انقر للاختيار</div>
                                <div className="text-sm text-muted-foreground">الصيغة المدعومة: .xlsx (بحد أقصى 15 ميجابايت)</div>
                            </div>
                        )}
                    </div>
                </div>

                {/* Side panel — metadata + daily costs */}
                <div className="space-y-5">
                    <div className="rounded-xl border border-border bg-white p-5">
                        <h3 className="text-lg font-bold mb-4" style={{ fontFamily: "Tajawal" }}>تفاصيل التحليل</h3>
                        <div className="space-y-4">
                            <div>
                                <label className="text-sm font-semibold mb-1.5 block">اسم التحليل (اختياري)</label>
                                <input
                                    type="text"
                                    value={name}
                                    onChange={(e) => setName(e.target.value)}
                                    placeholder="مثال: مبيعات أكتوبر"
                                    className="w-full px-3 py-2.5 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                                    data-testid="analysis-name-input"
                                />
                            </div>
                            <div>
                                <label className="text-sm font-semibold mb-1.5 block">التاريخ</label>
                                <DateInput
                                    value={date}
                                    onChange={(e) => setDate(e.target.value)}
                                    data-testid="analysis-date-input"
                                />
                            </div>
                            <div>
                                <label className="text-sm font-semibold mb-1.5 block">تكاليف المنتجات (ر.س)</label>
                                <div className="flex items-center border border-border rounded-lg bg-white focus-within:ring-2 focus-within:ring-brand overflow-hidden">
                                    <input
                                        type="number"
                                        min={0}
                                        step="0.01"
                                        value={productCosts}
                                        onChange={(e) => setProductCosts(e.target.value)}
                                        placeholder="0.00"
                                        dir="ltr"
                                        className="flex-1 min-w-0 px-3 py-2.5 text-base bg-transparent focus:outline-none num text-right"
                                        data-testid="product-costs-input"
                                    />
                                    <span className="px-3 py-2.5 text-xs font-bold text-muted-foreground bg-accent/60 border-s border-border whitespace-nowrap">ر.س</span>
                                </div>
                                <p className="text-xs text-muted-foreground mt-1.5">إجمالي تكلفة شراء/إنتاج المنتجات في هذا الملف.</p>
                            </div>
                        </div>
                    </div>

                    <button
                        type="submit"
                        disabled={busy || !file}
                        className="w-full py-3.5 px-4 bg-brand text-white font-bold rounded-lg bg-brand-hover transition-colors disabled:opacity-60"
                        data-testid="analyze-submit-btn"
                    >
                        {busy ? "جاري التحليل…" : "تحليل الملف"}
                    </button>
                </div>
            </form>
        </div>
    );
}
