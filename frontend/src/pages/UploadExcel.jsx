import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { UploadSimple, FileXls, X } from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";
import { todayISO } from "../lib/format";

const EMPTY_ILL = "https://static.prod-images.emergentagent.com/jobs/ab0374e5-2a04-4e34-b24c-447b0238a858/images/80b8ed57e8b03ecce86ccb201f79210f057fc21e43eab9fd1aa0648fc95c6bb3.png";

export default function UploadExcel() {
    const navigate = useNavigate();
    const inputRef = useRef(null);
    const [file, setFile] = useState(null);
    const [dragOver, setDragOver] = useState(false);
    const [busy, setBusy] = useState(false);

    const [name, setName] = useState("");
    const [date, setDate] = useState(todayISO());
    const [snap, setSnap] = useState("");
    const [tiktok, setTiktok] = useState("");
    const [insta, setInsta] = useState("");
    const [products, setProducts] = useState("");

    const onDrop = (e) => {
        e.preventDefault();
        setDragOver(false);
        const f = e.dataTransfer.files?.[0];
        if (f) setFile(f);
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
                snapchat_ads: snap || "0",
                tiktok_ads: tiktok || "0",
                instagram_ads: insta || "0",
                product_costs: products || "0",
            });
            const { data } = await api.post(`/analyses?${params.toString()}`, fd, {
                headers: { "Content-Type": "multipart/form-data" },
            });
            toast.success("تم تحليل الملف بنجاح!");
            navigate(`/analyses/${data.id}`);
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
                    ارفع ملف Excel المُصدَّر من منصة سلة، أضف تكاليف اليوم، واحصل على تقرير محاسبي كامل.
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
                            accept=".xlsx,.xls,.xlsm"
                            className="hidden"
                            onChange={(e) => setFile(e.target.files?.[0] || null)}
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
                                <div className="text-sm text-muted-foreground">صيغ مدعومة: .xlsx, .xls, .xlsm</div>
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
                                <input
                                    type="date"
                                    value={date}
                                    onChange={(e) => setDate(e.target.value)}
                                    className="w-full px-3 py-2.5 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                                    data-testid="analysis-date-input"
                                    dir="ltr"
                                    style={{ textAlign: "right" }}
                                />
                            </div>
                        </div>
                    </div>

                    <div className="rounded-xl border border-border bg-white p-5">
                        <h3 className="text-lg font-bold mb-1" style={{ fontFamily: "Tajawal" }}>التكاليف اليومية</h3>
                        <p className="text-xs text-muted-foreground mb-4">يمكنك إضافتها لاحقاً من صفحة التكاليف</p>
                        <div className="space-y-3">
                            {[
                                { label: "إعلانات سناب شات", value: snap, setter: setSnap, testid: "cost-snap" },
                                { label: "إعلانات تيك توك", value: tiktok, setter: setTiktok, testid: "cost-tiktok" },
                                { label: "إعلانات إنستقرام", value: insta, setter: setInsta, testid: "cost-insta" },
                                { label: "تكاليف المنتجات", value: products, setter: setProducts, testid: "cost-products" },
                            ].map((row) => (
                                <div key={row.testid} className="flex items-center justify-between gap-3">
                                    <label className="text-sm font-medium flex-1">{row.label}</label>
                                    <input
                                        type="number"
                                        min={0}
                                        step="0.01"
                                        value={row.value}
                                        onChange={(e) => row.setter(e.target.value)}
                                        placeholder="0.00"
                                        className="w-28 px-3 py-2 text-sm border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand num"
                                        data-testid={row.testid}
                                    />
                                </div>
                            ))}
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
