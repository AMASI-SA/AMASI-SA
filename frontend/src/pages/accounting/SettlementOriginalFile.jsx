import { useState } from "react";
import api from "../../lib/api";

export default function SettlementOriginalFile({ draftId }) {
    const [preview, setPreview] = useState(null);
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const base = "/financial-provider-apps/accounting-module/settlements/drafts/" + encodeURIComponent(draftId) + "/original";
    async function open(download) {
        setBusy(true); setError("");
        try {
            if (download) {
                const response = await api.get(base, { responseType: "blob" });
                const url = URL.createObjectURL(response.data);
                const link = document.createElement("a");
                link.href = url;
                const name = response.headers["content-disposition"]?.split("filename*=UTF-8''")[1];
                link.download = name ? decodeURIComponent(name) : "statement.xlsx";
                document.body.appendChild(link); link.click(); link.remove();
                setTimeout(() => URL.revokeObjectURL(url), 1000);
            } else {
                setPreview((await api.get(base + "/preview")).data);
            }
        } catch (err) {
            const detail = err?.response?.data?.detail;
            setError(typeof detail === "string" ? detail : detail?.message || "تعذر فتح الملف الأصلي أو لا تملك صلاحية الوصول إليه");
        } finally { setBusy(false); }
    }
    return <div className="space-y-2">
        <div className="flex gap-2">
            <button type="button" disabled={busy} onClick={() => open(false)} className="rounded-lg border px-3 py-2 text-xs font-bold">فتح الملف الأصلي</button>
            <button type="button" disabled={busy} onClick={() => open(true)} className="rounded-lg border px-3 py-2 text-xs font-bold">تنزيل XLSX الأصلي</button>
        </div>
        {error && <p role="alert" className="text-sm text-rose-700">{error}</p>}
        {preview && <section aria-label="معاينة الملف الأصلي" className="rounded-lg border p-3">
            <button type="button" onClick={() => setPreview(null)}>إغلاق معاينة الملف</button>
            <p>{preview.filename} — {preview.size} bytes</p>
            <p className="break-all text-xs" dir="ltr">SHA256: {preview.sha256}</p>
            <p className="text-xs">معاينة أول 100 سطر و30 عمودًا من أول 5 أوراق؛ التنزيل يحتوي الملف الأصلي كاملًا.</p>
            {preview.sheets.map((sheet) => <div key={sheet.name} className="max-h-80 overflow-auto">
                <h4>{sheet.name}</h4><table className="text-xs"><tbody>
                    {sheet.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j} className="border p-1 whitespace-nowrap">{cell}</td>)}</tr>)}
                </tbody></table>
            </div>)}
        </section>}
    </div>;
}

