import { useEffect, useState } from "react";
import { getProductControlCenter, saveProductControlDraft } from "../../services/mezanProductsV2";

const FIELDS = [
    ["name", "اسم المنتج", "text"],
    ["short_description", "الوصف المختصر", "text"],
    ["price", "السعر", "number"],
    ["sale_price", "السعر المخفض", "number"],
];

export default function EmployeeProductDraft({ productId }) {
    const [base, setBase] = useState(null);
    const [form, setForm] = useState({});
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState("");
    const [error, setError] = useState("");

    useEffect(() => {
        let active = true;
        getProductControlCenter(productId).then(({ product, draft }) => {
            if (active) {
                setBase(product);
                setForm({ ...product, ...draft?.changes });
            }
        }).catch(() => { if (active) setError("تعذّر تحميل مسودة المنتج."); });
        return () => { active = false; };
    }, [productId]);

    const changes = Object.fromEntries(FIELDS.flatMap(([key, , type]) => {
        const value = form[key] ?? "";
        if (String(value) === String(base?.[key] ?? "")) return [];
        return [[key, type === "number" ? (value === "" ? null : Number(value)) : value]];
    }));

    async function save(event) {
        event.preventDefault();
        setBusy(true);
        setMessage("");
        setError("");
        try {
            await saveProductControlDraft(productId, { changes });
            setMessage("حُفظت مسودة التعديل في ميزان.");
        } catch {
            setError("تعذّر حفظ مسودة التعديل.");
        } finally {
            setBusy(false);
        }
    }

    return <section className="rounded-2xl border border-emerald-200 p-4">
        <h2 className="font-black">مسودة تعديل المنتج</h2>
        <p className="mt-1 text-xs text-slate-600">يراجع المالك المسودة قبل اعتمادها ونشرها.</p>
        {base && <form className="mt-3 grid gap-3 sm:grid-cols-2" onSubmit={save}>
            {FIELDS.map(([key, label, type]) => <label key={key} className="text-sm font-bold">
                {label}
                <input value={form[key] ?? ""} type={type} disabled={busy}
                    required={key === "name" || key === "price"} maxLength={type === "text" ? 500 : undefined}
                    min={type === "number" ? 0 : undefined} step={type === "number" ? "0.01" : undefined}
                    onChange={(event) => { setMessage(""); setForm({ ...form, [key]: event.target.value }); }}
                    className="mt-1 block w-full rounded-lg border p-2" />
            </label>)}
            <button type="submit" disabled={busy || !Object.keys(changes).length}
                className="rounded-lg bg-emerald-700 p-2 font-bold text-white disabled:opacity-50">حفظ مسودة التعديل</button>
        </form>}
        {message && <p role="status" className="mt-2 text-emerald-700">{message}</p>}
        {error && <p role="alert" className="mt-2 text-rose-700">{error}</p>}
    </section>;
}
