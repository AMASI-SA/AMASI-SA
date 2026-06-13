/**
 * Iter-159l — API Permissions Diagnostic
 * --------------------------------------
 * One-click health check for ad-platform billing API permissions.
 * Used as a pre-flight before enabling historical-debt sync.
 */
import { useState } from "react";
import { Stethoscope, CheckCircle, XCircle, WarningCircle, ArrowsClockwise } from "@phosphor-icons/react";
import api from "../lib/api";


export default function ApiPermissionsDiagnostic() {
    const [busy, setBusy] = useState(false);
    const [results, setResults] = useState(null);

    const run = async () => {
        setBusy(true);
        const out = { snapchat: null, meta: null };
        try {
            try {
                const { data } = await api.get("/snapchat/diagnose-billing-permissions");
                out.snapchat = data;
            } catch (e) {
                out.snapchat = { connected: false, summary: e.response?.data?.detail || "خطأ في الفحص" };
            }
            try {
                const { data } = await api.get("/meta/diagnose-billing-permissions");
                out.meta = data;
            } catch (e) {
                out.meta = { connected: false, summary: e.response?.data?.detail || "خطأ في الفحص" };
            }
            setResults(out);
        } finally { setBusy(false); }
    };

    return (
        <div className="space-y-6" dir="rtl" data-testid="diag-page">
            <div>
                <h1 className="text-2xl font-extrabold text-slate-900 flex items-center gap-2.5">
                    <Stethoscope size={28} weight="fill" className="text-emerald-600" />
                    فحص صلاحيات API
                </h1>
                <p className="text-sm text-slate-600 mt-1">
                    فحص شامل لصلاحيات OAuth في كل منصة إعلانية مربوطة — يكتشف ما إذا كان توكنك يستطيع قراءة بيانات الفوترة والمديونيات.
                </p>
            </div>

            <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
                <div className="flex items-center justify-between">
                    <div>
                        <div className="text-sm font-bold text-slate-900">شغّل الفحص الآن</div>
                        <div className="text-xs text-slate-500 mt-0.5">يستغرق ~5 ثواني — يفحص Snapchat + Meta معاً.</div>
                    </div>
                    <button
                        onClick={run}
                        disabled={busy}
                        className="inline-flex items-center gap-2 px-4 py-2 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50 font-bold text-sm"
                        data-testid="diag-run-btn"
                    >
                        <ArrowsClockwise size={16} weight="bold" className={busy ? "animate-spin" : ""} />
                        {busy ? "جاري الفحص..." : "🔍 فحص الصلاحيات"}
                    </button>
                </div>
            </div>

            {results && (
                <div className="space-y-4">
                    <PlatformResult name="Snapchat" data={results.snapchat} />
                    <PlatformResult name="Meta" data={results.meta} />
                </div>
            )}
        </div>
    );
}

function PlatformResult({ name, data }) {
    if (!data) return null;
    const levelColor = data.level === "ok" ? "border-emerald-300 bg-emerald-50"
                       : data.level === "partial" ? "border-amber-300 bg-amber-50"
                       : data.level === "missing_scope" ? "border-amber-300 bg-amber-50"
                       : "border-rose-300 bg-rose-50";
    return (
        <div className={`border-2 rounded-xl p-4 ${levelColor}`} data-testid={`diag-${name.toLowerCase()}`}>
            <div className="flex items-center justify-between mb-3">
                <h3 className="font-extrabold text-slate-900">{name}</h3>
                <span className={`text-[11px] px-2 py-0.5 rounded font-bold border ${
                    !data.connected ? "bg-slate-100 text-slate-700 border-slate-300"
                    : data.level === "ok" ? "bg-emerald-100 text-emerald-800 border-emerald-300"
                    : "bg-amber-100 text-amber-800 border-amber-300"
                }`}>
                    {!data.connected ? "غير مربوط" : data.level === "ok" ? "كل الصلاحيات متاحة" : "ينقص بعض الصلاحيات"}
                </span>
            </div>

            <div className="text-sm font-bold text-slate-800 mb-3">{data.summary}</div>

            {data.checks && data.checks.length > 0 && (
                <ul className="space-y-1.5">
                    {data.checks.map((c, i) => (
                        <li key={i} className="bg-white border border-slate-200 rounded p-2 text-xs">
                            <div className="flex items-start justify-between gap-2">
                                <div className="flex-1">
                                    <div className="font-bold text-slate-800">{c.name}</div>
                                    {c.endpoint && (
                                        <div className="text-[10px] font-mono text-slate-400 mt-0.5" dir="ltr">{c.endpoint}</div>
                                    )}
                                    {c.scopes && c.scopes.length > 0 && (
                                        <div className="mt-1 flex flex-wrap gap-1">
                                            {c.scopes.map(s => (
                                                <span key={s} className="text-[9px] bg-slate-100 text-slate-700 px-1.5 py-0.5 rounded font-mono">{s}</span>
                                            ))}
                                        </div>
                                    )}
                                    {c.sample && (
                                        <div className="text-[10px] text-slate-500 mt-1">
                                            عيّنة: {Object.entries(c.sample).map(([k,v]) => `${k}=${v ?? "—"}`).join(" • ")}
                                        </div>
                                    )}
                                    {c.detail && (
                                        <div className="text-[10px] text-rose-600 mt-1 font-mono break-all">{c.detail}</div>
                                    )}
                                </div>
                                <div className="text-xs font-bold flex-shrink-0">{c.status}</div>
                            </div>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}
