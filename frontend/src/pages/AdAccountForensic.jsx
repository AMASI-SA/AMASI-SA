/**
 * Iter-250b P0 — Ad Account Forensic dashboard (Read-Only).
 *
 * Surfaces three diagnostic endpoints behind buttons so the operator
 * doesn't need to write curl/jq:
 *
 *   1. GET /audit/iter250a-post-deploy-check
 *   2. GET /audit/ad-account-write-paths-catalog
 *   3. GET /audit/ad-account-balance-forensic?ad_account_id=<id>
 *
 * Strictly read-only: no POST / PUT / DELETE. Includes Copy JSON
 * and Download JSON for each result block.
 */
import { useEffect, useState } from "react";
import {
    Stethoscope, MagnifyingGlass, CheckCircle, XCircle,
    Warning, CopySimple, DownloadSimple, Spinner, Database,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

const errMsg = (e, fb) =>
    formatApiErrorDetail(e?.response?.data?.detail) || fb || "حدث خطأ";

const fmtMoney = (n) => {
    const v = Number(n || 0);
    return v.toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
};

function copyJson(obj, label) {
    try {
        navigator.clipboard.writeText(JSON.stringify(obj, null, 2));
        toast.success(`تم نسخ ${label || "JSON"}`);
    } catch {
        toast.error("فشل النسخ");
    }
}

function downloadJson(obj, filename) {
    const blob = new Blob([JSON.stringify(obj, null, 2)],
        { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
}

function JsonActions({ payload, filename, testidPrefix }) {
    if (!payload) return null;
    return (
        <div className="flex gap-2">
            <button
                onClick={() => copyJson(payload, filename)}
                data-testid={`${testidPrefix}-copy`}
                className="inline-flex items-center gap-1.5 px-3 py-1.5
                           rounded-lg border border-slate-300
                           hover:bg-slate-50 text-xs font-bold text-slate-700">
                <CopySimple size={14} /> Copy JSON
            </button>
            <button
                onClick={() => downloadJson(payload, filename)}
                data-testid={`${testidPrefix}-download`}
                className="inline-flex items-center gap-1.5 px-3 py-1.5
                           rounded-lg border border-slate-300
                           hover:bg-slate-50 text-xs font-bold text-slate-700">
                <DownloadSimple size={14} /> Download
            </button>
        </div>
    );
}

function SectionCard({ title, icon: Icon, children, actions }) {
    return (
        <section className="rounded-2xl border border-slate-200 bg-white
                            shadow-sm overflow-hidden">
            <header className="flex items-center justify-between gap-3
                                px-5 py-3 border-b border-slate-100
                                bg-slate-50/60">
                <div className="flex items-center gap-2">
                    {Icon && <Icon size={18} className="text-emerald-600"
                                   weight="duotone" />}
                    <h2 className="text-base font-extrabold text-slate-900"
                        style={{ fontFamily: "Tajawal" }}>
                        {title}
                    </h2>
                </div>
                {actions}
            </header>
            <div className="p-5">{children}</div>
        </section>
    );
}

function VerdictRow({ verdict, idx }) {
    const ok = verdict.matches;
    return (
        <tr data-testid={`verdict-row-${idx}`}
            className={ok ? "bg-emerald-50/30" : "bg-rose-50/40"}>
            <td className="px-3 py-2 text-xs text-slate-700">
                {verdict.check}
            </td>
            <td className="px-3 py-2 text-xs num text-slate-900 font-bold">
                {typeof verdict.left === "number"
                    ? fmtMoney(verdict.left) : String(verdict.left)}
            </td>
            <td className="px-3 py-2 text-xs num text-slate-900 font-bold">
                {typeof verdict.right === "number"
                    ? fmtMoney(verdict.right) : String(verdict.right)}
            </td>
            <td className="px-3 py-2 text-xs num font-extrabold"
                style={{ color: Math.abs(verdict.delta) > 0.02
                                ? "#dc2626" : "#059669" }}>
                {typeof verdict.delta === "number"
                    ? fmtMoney(verdict.delta) : verdict.delta}
            </td>
            <td className="px-3 py-2 text-xs text-center">
                {ok
                    ? <CheckCircle size={18} weight="fill"
                                   className="text-emerald-600 inline" />
                    : <XCircle size={18} weight="fill"
                               className="text-rose-600 inline" />}
            </td>
        </tr>
    );
}

function HealthBadge({ health }) {
    const cls = {
        HEALTHY: "bg-emerald-100 text-emerald-700 border-emerald-300",
        PARTIAL: "bg-amber-100 text-amber-700 border-amber-300",
        BROKEN:  "bg-rose-100 text-rose-700 border-rose-300",
    }[health] || "bg-slate-100 text-slate-700 border-slate-300";
    return (
        <span className={`px-3 py-1 rounded-full border text-xs
                          font-extrabold ${cls}`}
              data-testid="ssot-health-badge">
            ssot_health: {health}
        </span>
    );
}

export default function AdAccountForensic() {
    const [deployResult, setDeployResult] = useState(null);
    const [catalogResult, setCatalogResult] = useState(null);
    const [adAccounts, setAdAccounts] = useState([]);
    const [selectedCp, setSelectedCp] = useState("");
    const [forensicResult, setForensicResult] = useState(null);
    const [dryrunResult, setDryrunResult] = useState(null);
    const [recomputeResult, setRecomputeResult] = useState(null);
    const [rootCauseResult, setRootCauseResult] = useState(null);
    const [rootCauseCp, setRootCauseCp] = useState("");
    const [busy, setBusy] = useState({ deploy: false, catalog: false,
                                       accounts: false, forensic: false,
                                       dryrun: false, recompute: false,
                                       rootCause: false });

    const load = async (key, fn) => {
        setBusy((b) => ({ ...b, [key]: true }));
        try { return await fn(); }
        catch (e) { toast.error(errMsg(e)); return null; }
        finally { setBusy((b) => ({ ...b, [key]: false })); }
    };

    // Auto-load ad accounts list on mount.
    useEffect(() => {
        (async () => {
            const r = await load("accounts", () => api.get("/ad-accounts"));
            if (r) {
                const list = Array.isArray(r.data) ? r.data
                    : (r.data?.accounts || []);
                setAdAccounts(list);
            }
        })();
    }, []);

    const runDeploy = () => load("deploy", async () => {
        const r = await api.get("/audit/iter250a-post-deploy-check");
        setDeployResult(r.data);
        toast.success("تم تشغيل Post-Deploy Check");
    });

    const runCatalog = () => load("catalog", async () => {
        const r = await api.get("/audit/ad-account-write-paths-catalog");
        setCatalogResult(r.data);
        toast.success("تم تشغيل Write-Paths Catalog");
    });

    const runForensic = () => {
        if (!selectedCp) {
            toast.error("اختر حساباً إعلانياً أولاً");
            return;
        }
        load("forensic", async () => {
            const r = await api.get(
                `/audit/ad-account-balance-forensic?ad_account_id=${selectedCp}`);
            setForensicResult(r.data);
            toast.success("تم تشغيل Forensic للحساب");
        });
    };

    const runDryrun = () => load("dryrun", async () => {
        const r = await api.get(
            "/audit/ad-account-dryrun-diff?include_clean=true");
        setDryrunResult(r.data);
        toast.success("تم تشغيل Dry-Run Diff لكل الحسابات");
    });

    const runRecompute = () => load("recompute", async () => {
        const r = await api.get(
            "/audit/ad-account-recompute-dryrun?include_clean=true");
        setRecomputeResult(r.data);
        toast.success("تم تشغيل Reconciliation Dry-Run");
    });

    const runRootCause = () => {
        if (!rootCauseCp) {
            toast.error("اختر حساباً للتحقيق العميق أولاً");
            return;
        }
        load("rootCause", async () => {
            const r = await api.get(
                `/audit/ad-account-root-cause?ad_account_id=${rootCauseCp}`);
            setRootCauseResult(r.data);
            toast.success("تم تشغيل Root-Cause للحساب");
        });
    };

    return (
        <div className="space-y-6" data-testid="ad-forensic-page"
             style={{ fontFamily: "Tajawal" }}>
            <header className="rounded-2xl border border-slate-200
                                bg-gradient-to-br from-white to-slate-50
                                p-5 shadow-sm">
                <div className="flex items-start gap-3">
                    <div className="w-10 h-10 rounded-xl bg-emerald-500/15
                                    text-emerald-700 flex items-center
                                    justify-center">
                        <Stethoscope size={22} weight="duotone" />
                    </div>
                    <div>
                        <div className="text-[11px] font-bold text-emerald-700
                                        uppercase tracking-wide">
                            Iter-250a / 250b — Read-Only
                        </div>
                        <h1 className="text-2xl font-extrabold text-slate-900">
                            تشخيص الحسابات الإعلانية (Forensic)
                        </h1>
                        <p className="text-xs text-slate-500 mt-1">
                            ثلاث تقارير قراءة فقط. لا توجد POST / PUT /
                            DELETE. لا migration. لا cleanup.
                        </p>
                    </div>
                </div>
            </header>

            {/* ─────────── 1. Post-Deploy Check ─────────── */}
            <SectionCard
                title="1. Post-Deploy Check (Iter-250a)"
                icon={CheckCircle}
                actions={
                    <div className="flex items-center gap-2">
                        <JsonActions payload={deployResult}
                            filename="post-deploy-check.json"
                            testidPrefix="deploy" />
                        <button
                            onClick={runDeploy}
                            disabled={busy.deploy}
                            data-testid="run-deploy-check-btn"
                            className="inline-flex items-center gap-2 px-4 py-2
                                        rounded-xl bg-emerald-600
                                        hover:bg-emerald-700 disabled:opacity-50
                                        text-white text-sm font-bold">
                            {busy.deploy ? <Spinner className="animate-spin"
                                                    size={16} />
                                         : <MagnifyingGlass size={16} />}
                            تشغيل الفحص
                        </button>
                    </div>
                }>
                {!deployResult && (
                    <p className="text-sm text-slate-500">
                        اضغط &quot;تشغيل الفحص&quot; لمشاهدة سلامة Iter-250a وإحصاءات
                        الـ ledger لكل قطاع (بنوك، إعلانات، موردين،
                        موظفين، شحن، BNPL).
                    </p>
                )}
                {deployResult && (
                    <div className="space-y-4">
                        <div className="flex flex-wrap gap-3 items-center">
                            <span data-testid="deploy-set-match"
                                  className={`px-3 py-1 rounded-full text-xs
                                              font-extrabold ${
                                  deployResult.A_inventory_integrity?.set_match
                                      ? "bg-emerald-100 text-emerald-700"
                                      : "bg-rose-100 text-rose-700"
                              }`}>
                                set_match: {String(
                                    deployResult.A_inventory_integrity?.set_match)}
                            </span>
                            <span className="text-xs text-slate-600">
                                total_pages:&nbsp;
                                <b className="num">
                                    {deployResult.A_inventory_integrity?.total_pages}
                                </b>
                            </span>
                            <span className="text-xs text-slate-600">
                                checked_at:&nbsp;
                                <b>{deployResult.checked_at}</b>
                            </span>
                        </div>
                        <div className="overflow-x-auto">
                            <table className="w-full text-xs"
                                   data-testid="areas-table">
                                <thead>
                                    <tr className="text-slate-600 border-b">
                                        <th className="px-3 py-2 text-right">القطاع</th>
                                        <th className="px-3 py-2">posted_rows</th>
                                        <th className="px-3 py-2">debits_sum</th>
                                        <th className="px-3 py-2">credits_sum</th>
                                        <th className="px-3 py-2">net_balance</th>
                                        <th className="px-3 py-2">new_24h</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {Object.entries(
                                        deployResult.B_areas_snapshot || {}
                                    ).map(([k, v]) => (
                                        <tr key={k} className="border-b
                                                                border-slate-100"
                                            data-testid={`area-${k}`}>
                                            <td className="px-3 py-2 font-bold
                                                            text-slate-900">
                                                {k}
                                            </td>
                                            <td className="px-3 py-2 num text-center">
                                                {v.posted_rows}
                                            </td>
                                            <td className="px-3 py-2 num text-center">
                                                {fmtMoney(v.debits_sum)}
                                            </td>
                                            <td className="px-3 py-2 num text-center">
                                                {fmtMoney(v.credits_sum)}
                                            </td>
                                            <td className="px-3 py-2 num
                                                            text-center font-bold">
                                                {fmtMoney(v.net_balance)}
                                            </td>
                                            <td className="px-3 py-2 num text-center">
                                                {v.rows_created_last_24h > 0 ?
                                                    <b className="text-amber-700">
                                                        {v.rows_created_last_24h}
                                                    </b>
                                                    : "0"}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                        {(deployResult.C_legacy_recent_writes || [])
                            .filter(x => x.count_last_24h > 0)
                            .length > 0 && (
                            <div className="rounded-xl bg-rose-50 border
                                            border-rose-200 p-3">
                                <div className="flex items-center gap-2
                                                 font-extrabold text-rose-700
                                                 text-sm mb-1">
                                    <Warning size={16} weight="fill" />
                                    تنبيه: صفحات Legacy ما زالت تستقبل كتابات
                                </div>
                                <ul className="text-xs text-rose-700 space-y-1">
                                    {deployResult.C_legacy_recent_writes
                                        .filter(x => x.count_last_24h > 0)
                                        .map((x, i) => (
                                        <li key={i}>
                                            <b>{x.label}</b>: {x.count_last_24h}
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        )}
                    </div>
                )}
            </SectionCard>

            {/* ─────────── 2. Write-Paths Catalog ─────────── */}
            <SectionCard
                title="2. Write-Paths Catalog (Iter-250b P0)"
                icon={Database}
                actions={
                    <div className="flex items-center gap-2">
                        <JsonActions payload={catalogResult}
                            filename="ad-write-paths-catalog.json"
                            testidPrefix="catalog" />
                        <button
                            onClick={runCatalog}
                            disabled={busy.catalog}
                            data-testid="run-catalog-btn"
                            className="inline-flex items-center gap-2 px-4 py-2
                                        rounded-xl bg-emerald-600
                                        hover:bg-emerald-700 disabled:opacity-50
                                        text-white text-sm font-bold">
                            {busy.catalog ? <Spinner className="animate-spin"
                                                     size={16} />
                                          : <MagnifyingGlass size={16} />}
                            تشغيل الكتالوج
                        </button>
                    </div>
                }>
                {!catalogResult && (
                    <p className="text-sm text-slate-500">
                        كتالوج ثابت لكل مواقع الكتابة على /ad-accounts —
                        لا يفحص DB.
                    </p>
                )}
                {catalogResult && (
                    <div className="space-y-3">
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                            {Object.entries(catalogResult.summary?.by_ssot_status
                                            || {}).map(([k, v]) => (
                                <div key={k} className="rounded-lg border
                                                       border-slate-200 p-3
                                                       bg-slate-50/60"
                                     data-testid={`catalog-ssot-${k}`}>
                                    <div className="text-[10px] font-bold
                                                     text-slate-600">{k}</div>
                                    <div className="num text-xl font-extrabold
                                                    text-slate-900">{v}</div>
                                </div>
                            ))}
                        </div>
                        <div className="text-xs text-slate-600">
                            إجمالي مواقع الكتابة:&nbsp;
                            <b className="num">
                                {catalogResult.summary?.total_write_sites}
                            </b>
                            &nbsp;عبر&nbsp;
                            <b className="num">
                                {catalogResult.summary?.distinct_endpoints}
                            </b>
                            &nbsp;endpoint.
                            &nbsp;HIGH risk:&nbsp;
                            <b className="num text-rose-700">
                                {catalogResult.summary?.by_risk?.HIGH || 0}
                            </b>
                        </div>
                        <details className="mt-2">
                            <summary className="cursor-pointer text-xs
                                                font-bold text-emerald-700">
                                عرض الكتالوج التفصيلي
                                ({catalogResult.write_paths?.length || 0}
                                &nbsp;سطر)
                            </summary>
                            <div className="overflow-x-auto mt-3">
                                <table className="w-full text-[11px]">
                                    <thead>
                                        <tr className="text-slate-600 border-b">
                                            <th className="px-2 py-1 text-right">
                                                Endpoint</th>
                                            <th className="px-2 py-1">Collection</th>
                                            <th className="px-2 py-1">Op</th>
                                            <th className="px-2 py-1">SSOT</th>
                                            <th className="px-2 py-1">Risk</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {(catalogResult.write_paths || [])
                                            .map((w, i) => (
                                            <tr key={i}
                                                className="border-b border-slate-100">
                                                <td className="px-2 py-1 font-mono">
                                                    {w.endpoint}
                                                </td>
                                                <td className="px-2 py-1 font-mono
                                                                text-slate-600">
                                                    {w.collection}
                                                </td>
                                                <td className="px-2 py-1">
                                                    {w.op}
                                                </td>
                                                <td className="px-2 py-1">
                                                    <span className={`px-1.5
                                                        rounded font-bold ${
                                                        w.ssot_status === "SSOT"
                                                            ? "bg-emerald-100 text-emerald-700"
                                                        : w.ssot_status === "DUPLICATE"
                                                            ? "bg-rose-100 text-rose-700"
                                                        : "bg-amber-100 text-amber-700"
                                                    }`}>
                                                        {w.ssot_status}
                                                    </span>
                                                </td>
                                                <td className="px-2 py-1">
                                                    {w.risk}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </details>
                    </div>
                )}
            </SectionCard>

            {/* ─────────── 3. Per-Account Forensic ─────────── */}
            <SectionCard
                title="3. Balance Forensic لكل حساب إعلاني"
                icon={MagnifyingGlass}
                actions={
                    <JsonActions payload={forensicResult}
                        filename={`forensic-${selectedCp || "x"}.json`}
                        testidPrefix="forensic" />
                }>
                <div className="flex flex-wrap items-center gap-3 mb-4">
                    <select
                        data-testid="ad-account-select"
                        className="rounded-lg border border-slate-300
                                   px-3 py-2 text-sm min-w-[280px]"
                        value={selectedCp}
                        onChange={(e) => setSelectedCp(e.target.value)}>
                        <option value="">
                            — اختر حساباً إعلانياً ({adAccounts.length}) —
                        </option>
                        {adAccounts.map((a) => (
                            <option key={a.id} value={a.id}>
                                {a.name}
                                {a.platform ? ` · ${a.platform}` : ""}
                                {a.currency ? ` · ${a.currency}` : ""}
                            </option>
                        ))}
                    </select>
                    <button
                        onClick={runForensic}
                        disabled={busy.forensic || !selectedCp}
                        data-testid="run-forensic-btn"
                        className="inline-flex items-center gap-2 px-4 py-2
                                    rounded-xl bg-emerald-600
                                    hover:bg-emerald-700 disabled:opacity-50
                                    text-white text-sm font-bold">
                        {busy.forensic ? <Spinner className="animate-spin"
                                                  size={16} />
                                       : <MagnifyingGlass size={16} />}
                        تشغيل Forensic
                    </button>
                </div>

                {!forensicResult && (
                    <p className="text-sm text-slate-500">
                        اختر حساباً ثم اضغط &quot;تشغيل Forensic&quot; لعرض مقارنة:
                        counterparties vs ledger(balance) vs ledger(debt)
                        vs liabilities vs ad_account_ledger.
                    </p>
                )}

                {forensicResult && (
                    <div className="space-y-5">
                        <div className="flex flex-wrap items-center gap-3
                                         pb-3 border-b border-slate-200">
                            <HealthBadge health={forensicResult.ssot_health} />
                            <span className="text-xs text-slate-600">
                                <b>{forensicResult.ad_account?.name}</b>
                                {forensicResult.ad_account?.platform && (
                                    <> · {forensicResult.ad_account.platform}</>
                                )}
                                {forensicResult.ad_account?.currency && (
                                    <> · {forensicResult.ad_account.currency}</>
                                )}
                            </span>
                        </div>

                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3"
                             data-testid="forensic-cached-grid">
                            <div className="rounded-lg border border-slate-200
                                             p-3 bg-slate-50/60">
                                <div className="text-[10px] font-bold text-slate-600">
                                    opening_balance
                                </div>
                                <div className="num text-lg font-extrabold">
                                    {fmtMoney(forensicResult.ad_account?.opening_balance)}
                                </div>
                            </div>
                            <div className="rounded-lg border border-slate-200
                                             p-3 bg-slate-50/60">
                                <div className="text-[10px] font-bold text-slate-600">
                                    current_balance (cached)
                                </div>
                                <div className="num text-lg font-extrabold">
                                    {fmtMoney(forensicResult.ad_account?.current_balance_cached)}
                                </div>
                            </div>
                            <div className="rounded-lg border border-slate-200
                                             p-3 bg-slate-50/60">
                                <div className="text-[10px] font-bold text-slate-600">
                                    debt_balance (cached)
                                </div>
                                <div className="num text-lg font-extrabold">
                                    {fmtMoney(forensicResult.ad_account?.debt_balance_cached)}
                                </div>
                            </div>
                            <div className="rounded-lg border border-emerald-200
                                             p-3 bg-emerald-50/40">
                                <div className="text-[10px] font-bold text-emerald-700">
                                    ledger(debt).net
                                </div>
                                <div className="num text-lg font-extrabold">
                                    {fmtMoney(forensicResult.general_ledger
                                              ?.sub_account_debt?.net)}
                                </div>
                            </div>
                        </div>

                        <div>
                            <h3 className="text-sm font-extrabold text-slate-900
                                            mb-2">Verdicts</h3>
                            <div className="overflow-x-auto">
                                <table className="w-full text-xs border
                                                   border-slate-200 rounded-lg
                                                   overflow-hidden"
                                       data-testid="verdicts-table">
                                    <thead>
                                        <tr className="bg-slate-100 text-slate-700">
                                            <th className="px-3 py-2 text-right">
                                                الفحص</th>
                                            <th className="px-3 py-2">Cache</th>
                                            <th className="px-3 py-2">Ledger</th>
                                            <th className="px-3 py-2">Δ</th>
                                            <th className="px-3 py-2">Match</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {(forensicResult.verdicts || [])
                                            .map((v, i) => (
                                                <VerdictRow key={i}
                                                            verdict={v}
                                                            idx={i} />
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-3 gap-3"
                             data-testid="forensic-legacy-grid">
                            {Object.entries(
                                forensicResult.legacy_collections || {}
                            ).map(([k, v]) => (
                                <div key={k}
                                     className={`rounded-lg border p-3 ${
                                       v.still_written
                                           ? "border-rose-300 bg-rose-50/40"
                                           : "border-emerald-200 bg-emerald-50/30"
                                     }`}
                                     data-testid={`legacy-coll-${k}`}>
                                    <div className="text-[10px] font-bold
                                                     text-slate-600
                                                     uppercase tracking-wide">
                                        {k}
                                    </div>
                                    <div className="num text-xl font-extrabold
                                                    text-slate-900">
                                        {v.row_count} سطر
                                    </div>
                                    {("sum" in v) && (
                                        <div className="text-[10px] text-slate-500">
                                            sum: {fmtMoney(v.sum)}
                                        </div>
                                    )}
                                    {("net" in v) && (
                                        <div className="text-[10px] text-slate-500">
                                            net: {fmtMoney(v.net)}
                                        </div>
                                    )}
                                    <div className="mt-1 text-[10px] font-bold">
                                        {v.still_written
                                            ? <span className="text-rose-700">
                                                ⚠️ ما زال يُكتب فيه
                                              </span>
                                            : <span className="text-emerald-700">
                                                ✓ نظيف
                                              </span>}
                                    </div>
                                </div>
                            ))}
                        </div>

                        <div className="rounded-xl border border-slate-200
                                        bg-slate-50/60 p-4">
                            <div className="text-[11px] font-extrabold
                                             text-slate-700 mb-1">
                                التوصية
                            </div>
                            <p className="text-xs text-slate-700 leading-relaxed"
                               data-testid="forensic-recommendation">
                                {forensicResult.recommendation}
                            </p>
                        </div>
                    </div>
                )}
            </SectionCard>

            {/* ─────────── 4. Dry-Run Diff (Iter-250b P0) ─────────── */}
            <SectionCard
                title="4. Forward-Fix Dry-Run Diff (لكل الحسابات)"
                icon={Warning}
                actions={
                    <div className="flex items-center gap-2">
                        <JsonActions payload={dryrunResult}
                            filename="ad-dryrun-diff.json"
                            testidPrefix="dryrun" />
                        <button
                            onClick={runDryrun}
                            disabled={busy.dryrun}
                            data-testid="run-dryrun-btn"
                            className="inline-flex items-center gap-2 px-4 py-2
                                        rounded-xl bg-emerald-600
                                        hover:bg-emerald-700 disabled:opacity-50
                                        text-white text-sm font-bold">
                            {busy.dryrun ? <Spinner className="animate-spin"
                                                    size={16} />
                                         : <MagnifyingGlass size={16} />}
                            تشغيل Dry-Run
                        </button>
                    </div>
                }>
                {!dryrunResult && (
                    <p className="text-sm text-slate-500">
                        يقرأ legacy + GL لكل حساب إعلاني ويُصنّف كل
                        حساب بـ freeze_safety. لا يُنفّذ أي كتابة.
                        راجع
                        &nbsp;<code className="text-emerald-700 text-[11px]">
                            /app/docs/ITER250B_P0_PLAN.md
                        </code>
                        &nbsp;للخطة الكاملة قبل المراحل التالية.
                    </p>
                )}
                {dryrunResult && (
                    <div className="space-y-4">
                        {dryrunResult.accounts_source && (
                            <div className="rounded-xl border
                                            border-emerald-200
                                            bg-emerald-50/30 p-3"
                                 data-testid="dryrun-discovery">
                                <div className="text-[11px] font-extrabold
                                                text-emerald-700 mb-2">
                                    🔍 اكتشاف الحسابات (Discovery)
                                </div>
                                <div className="grid grid-cols-2
                                                md:grid-cols-4 gap-2 text-xs">
                                    {Object.entries(
                                        dryrunResult.accounts_source
                                    ).map(([k, v]) => (
                                        (typeof v === "number") && (
                                            <div key={k}
                                                 className="bg-white rounded
                                                            border border-slate-200
                                                            p-2">
                                                <div className="text-[9px]
                                                                text-slate-500
                                                                font-bold">
                                                    {k}
                                                </div>
                                                <div className="num text-lg
                                                                font-extrabold
                                                                text-slate-900">
                                                    {v}
                                                </div>
                                            </div>
                                        )
                                    ))}
                                </div>
                                {dryrunResult.accounts_source
                                    ?.no_accounts_reason && (
                                    <div className="text-xs text-rose-700
                                                    mt-2 font-bold">
                                        ⚠️ {dryrunResult.accounts_source
                                                .no_accounts_reason}
                                    </div>
                                )}
                                {(dryrunResult.accounts_source
                                    ?.gl_only_orphans || []).length > 0 && (
                                    <div className="text-[10px] text-amber-700
                                                    mt-2">
                                        <b>GL-only orphans:</b>&nbsp;
                                        {dryrunResult.accounts_source
                                            .gl_only_orphans.join(", ")}
                                    </div>
                                )}
                            </div>
                        )}
                        <div className="flex flex-wrap items-center gap-3
                                         pb-3 border-b border-slate-200">
                            <span data-testid="dryrun-overall"
                                  className={`px-3 py-1 rounded-full border
                                              text-xs font-extrabold ${
                                dryrunResult.overall_recommendation
                                    === "safe_to_apply_forward_fix"
                                    ? "bg-emerald-100 text-emerald-700 border-emerald-300"
                                : dryrunResult.overall_recommendation
                                    === "all_clean"
                                    ? "bg-emerald-100 text-emerald-700 border-emerald-300"
                                : dryrunResult.overall_recommendation
                                    === "needs_reconciliation_before_apply"
                                    ? "bg-rose-100 text-rose-700 border-rose-300"
                                : "bg-amber-100 text-amber-700 border-amber-300"
                            }`}>
                                overall: {dryrunResult.overall_recommendation}
                            </span>
                            <span className="text-xs text-slate-600">
                                scanned:&nbsp;
                                <b>{dryrunResult.totals?.accounts_scanned}</b>
                                &nbsp;· clean:&nbsp;
                                <b className="text-emerald-700">
                                    {dryrunResult.totals?.accounts_clean}
                                </b>
                                &nbsp;· with_diff:&nbsp;
                                <b className="text-amber-700">
                                    {dryrunResult.totals?.accounts_with_diff}
                                </b>
                            </span>
                            <span className="text-xs text-slate-600">
                                |Δ debt|:&nbsp;
                                <b className="num">
                                    {fmtMoney(dryrunResult.totals
                                              ?.total_abs_delta_debt)}
                                </b>
                                &nbsp;· |Δ balance|:&nbsp;
                                <b className="num">
                                    {fmtMoney(dryrunResult.totals
                                              ?.total_abs_delta_balance)}
                                </b>
                            </span>
                        </div>
                        <div className="overflow-x-auto">
                            <table className="w-full text-xs border
                                              border-slate-200 rounded-lg
                                              overflow-hidden"
                                   data-testid="dryrun-table">
                                <thead>
                                    <tr className="bg-slate-100 text-slate-700">
                                        <th className="px-3 py-2 text-right">
                                            الحساب</th>
                                        <th className="px-3 py-2">Cache.debt</th>
                                        <th className="px-3 py-2">GL.debt</th>
                                        <th className="px-3 py-2">Δ debt</th>
                                        <th className="px-3 py-2">liab.sum</th>
                                        <th className="px-3 py-2">legacy_rows</th>
                                        <th className="px-3 py-2">freeze_safety</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {(dryrunResult.accounts || []).map(
                                        (a, i) => (
                                        <tr key={a.ad_account_id}
                                            data-testid={`dryrun-row-${i}`}
                                            className={`border-b ${
                                                a.is_clean
                                                    ? "bg-emerald-50/30"
                                                    : "bg-rose-50/20"
                                            }`}>
                                            <td className="px-3 py-2 font-bold">
                                                {a.name}
                                                <div className="text-[10px]
                                                                text-slate-500">
                                                    {a.platform}
                                                </div>
                                            </td>
                                            <td className="px-3 py-2 num">
                                                {fmtMoney(a.counterparties_cache
                                                          ?.debt_balance)}
                                            </td>
                                            <td className="px-3 py-2 num">
                                                {fmtMoney(a.general_ledger
                                                          ?.debt_net)}
                                            </td>
                                            <td className="px-3 py-2 num font-bold"
                                                style={{
                                                    color: Math.abs(
                                                        a.deltas?.debt_balance_minus_GL_debt
                                                        || 0) > 0.02
                                                        ? "#dc2626"
                                                        : "#059669"
                                                }}>
                                                {fmtMoney(a.deltas
                                                  ?.debt_balance_minus_GL_debt)}
                                            </td>
                                            <td className="px-3 py-2 num">
                                                {fmtMoney(a.legacy_collections
                                                  ?.liabilities?.sum_balance)}
                                            </td>
                                            <td className="px-3 py-2 num
                                                            text-center">
                                                {a.legacy_collections
                                                  ?.ad_account_ledger?.row_count}
                                                {" / "}
                                                {a.legacy_collections
                                                  ?.liabilities?.row_count}
                                                {" / "}
                                                {a.legacy_collections
                                                  ?.account_transactions
                                                  ?.row_count}
                                            </td>
                                            <td className="px-3 py-2">
                                                <span className={`px-1.5 py-0.5
                                                    rounded text-[10px]
                                                    font-extrabold ${
                                                    a.freeze_safety
                                                        === "SAFE_TO_FREEZE_LEGACY"
                                                    ? "bg-emerald-100 text-emerald-700"
                                                    : a.freeze_safety
                                                        === "NEEDS_RECONCILIATION_FIRST"
                                                    ? "bg-rose-100 text-rose-700"
                                                    : "bg-amber-100 text-amber-700"
                                                }`}>
                                                    {a.freeze_safety}
                                                </span>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                            <div className="text-[10px] text-slate-500 mt-1">
                                legacy_rows = ad_account_ledger / liabilities
                                / account_transactions
                            </div>
                        </div>
                        {(dryrunResult.accounts || []).map((a, i) => (
                            a.freeze_safety !== "SAFE_TO_FREEZE_LEGACY" && (
                                <div key={`exp-${i}`}
                                     className="rounded-lg border
                                                 border-amber-200
                                                 bg-amber-50/40 p-3 text-xs"
                                     data-testid={`dryrun-rationale-${i}`}>
                                    <b className="text-amber-700">
                                        {a.name}
                                    </b>: {a.rationale}
                                </div>
                            )
                        ))}
                    </div>
                )}
            </SectionCard>

            {/* ─── 5. Reconciliation Dry-Run (Phase 0.5) ─── */}
            <SectionCard
                title="5. Reconciliation Dry-Run (Phase 0.5)"
                icon={Stethoscope}
                actions={
                    <div className="flex items-center gap-2">
                        <JsonActions payload={recomputeResult}
                            filename="ad-recompute-dryrun.json"
                            testidPrefix="recompute" />
                        <button
                            onClick={runRecompute}
                            disabled={busy.recompute}
                            data-testid="run-recompute-btn"
                            className="inline-flex items-center gap-2 px-4 py-2
                                        rounded-xl bg-emerald-600
                                        hover:bg-emerald-700 disabled:opacity-50
                                        text-white text-sm font-bold">
                            {busy.recompute
                                ? <Spinner className="animate-spin" size={16} />
                                : <MagnifyingGlass size={16} />}
                            تشغيل Reconciliation Dry-Run
                        </button>
                    </div>
                }>
                {!recomputeResult && (
                    <p className="text-sm text-slate-500">
                        يعرض لكل حساب: قيمة الكاش الحالية، القيمة المقترحة
                        من GL، الفرق، وعدد سطور legacy التي ستبقى deprecated.
                        لا يُنفّذ recompute فعلي.
                    </p>
                )}
                {recomputeResult && (
                    <div className="space-y-4">
                        <div className="flex flex-wrap items-center gap-3
                                         pb-3 border-b border-slate-200">
                            <span data-testid="recompute-overall"
                                  className={`px-3 py-1 rounded-full border
                                              text-xs font-extrabold ${
                                recomputeResult.overall_recommendation
                                    === "all_safe_to_recompute"
                                    ? "bg-emerald-100 text-emerald-700 border-emerald-300"
                                : recomputeResult.overall_recommendation
                                    === "all_need_manual_review"
                                    ? "bg-rose-100 text-rose-700 border-rose-300"
                                : "bg-amber-100 text-amber-700 border-amber-300"
                            }`}>
                                overall: {recomputeResult.overall_recommendation}
                            </span>
                            <span className="text-xs text-slate-600">
                                scanned:&nbsp;
                                <b>{recomputeResult.totals?.accounts_scanned}</b>
                                &nbsp;· cache_only:&nbsp;
                                <b className="text-emerald-700">
                                    {recomputeResult.totals?.recompute_cache_only}
                                </b>
                                &nbsp;· manual_review:&nbsp;
                                <b className="text-rose-700">
                                    {recomputeResult.totals?.manual_review_required}
                                </b>
                            </span>
                            <span className="text-xs text-slate-600">
                                |Δ balance|:&nbsp;
                                <b className="num">
                                    {fmtMoney(recomputeResult.totals
                                        ?.total_abs_delta_current_balance)}
                                </b>
                                &nbsp;· |Δ debt|:&nbsp;
                                <b className="num">
                                    {fmtMoney(recomputeResult.totals
                                        ?.total_abs_delta_debt_balance)}
                                </b>
                            </span>
                        </div>

                        <div className="rounded-xl border border-emerald-200
                                        bg-emerald-50/30 p-3">
                            <div className="text-[11px] font-extrabold
                                             text-emerald-700 mb-1">
                                ✅ ضمانات هذا التشغيل
                            </div>
                            <ul className="text-[11px] text-slate-700 space-y-0.5
                                            list-disc list-inside">
                                {(recomputeResult.constraints_honored || [])
                                    .map((c, i) => (
                                    <li key={i}>{c}</li>
                                ))}
                            </ul>
                        </div>

                        <div className="overflow-x-auto">
                            <table className="w-full text-xs border
                                              border-slate-200 rounded-lg
                                              overflow-hidden"
                                   data-testid="recompute-table">
                                <thead>
                                    <tr className="bg-slate-100 text-slate-700">
                                        <th className="px-3 py-2 text-right">الحساب</th>
                                        <th className="px-3 py-2">cache.balance</th>
                                        <th className="px-3 py-2">→ GL.balance</th>
                                        <th className="px-3 py-2">Δ</th>
                                        <th className="px-3 py-2">cache.debt</th>
                                        <th className="px-3 py-2">→ GL.debt</th>
                                        <th className="px-3 py-2">Δ</th>
                                        <th className="px-3 py-2">legacy_rows</th>
                                        <th className="px-3 py-2">التوصية</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {(recomputeResult.accounts || []).map(
                                        (a, i) => (
                                        <tr key={a.ad_account_id}
                                            data-testid={`recompute-row-${i}`}
                                            className={`border-b ${
                                                a.recommendation
                                                    === "RECOMPUTE_CACHE_ONLY"
                                                    ? "bg-emerald-50/30"
                                                    : "bg-rose-50/30"
                                            }`}>
                                            <td className="px-3 py-2 font-bold">
                                                {a.name}
                                                <div className="text-[10px]
                                                                 text-slate-500">
                                                    {a.platform || a.source}
                                                </div>
                                            </td>
                                            <td className="px-3 py-2 num">
                                                {fmtMoney(a.current_cache
                                                    ?.current_balance)}
                                            </td>
                                            <td className="px-3 py-2 num
                                                            font-bold
                                                            text-emerald-700">
                                                {fmtMoney(a.proposed_from_gl
                                                    ?.current_balance)}
                                            </td>
                                            <td className="px-3 py-2 num
                                                            font-extrabold"
                                                style={{
                                                  color: Math.abs(
                                                    a.deltas
                                                      ?.current_balance_delta
                                                      || 0) > 0.02
                                                    ? "#dc2626" : "#059669"
                                                }}>
                                                {fmtMoney(a.deltas
                                                    ?.current_balance_delta)}
                                            </td>
                                            <td className="px-3 py-2 num">
                                                {fmtMoney(a.current_cache
                                                    ?.debt_balance)}
                                            </td>
                                            <td className="px-3 py-2 num
                                                            font-bold
                                                            text-emerald-700">
                                                {fmtMoney(a.proposed_from_gl
                                                    ?.debt_balance)}
                                            </td>
                                            <td className="px-3 py-2 num
                                                            font-extrabold"
                                                style={{
                                                  color: Math.abs(
                                                    a.deltas?.debt_balance_delta
                                                      || 0) > 0.02
                                                    ? "#dc2626" : "#059669"
                                                }}>
                                                {fmtMoney(a.deltas
                                                    ?.debt_balance_delta)}
                                            </td>
                                            <td className="px-3 py-2 num
                                                            text-center
                                                            text-[10px]">
                                                {a.legacy_rows_remaining
                                                    ?.ad_account_ledger}
                                                {" / "}
                                                {a.legacy_rows_remaining
                                                    ?.liabilities}
                                                {" / "}
                                                {a.legacy_rows_remaining
                                                    ?.account_transactions}
                                            </td>
                                            <td className="px-3 py-2">
                                                <span className={`px-1.5 py-0.5
                                                    rounded text-[10px]
                                                    font-extrabold ${
                                                    a.recommendation
                                                        === "RECOMPUTE_CACHE_ONLY"
                                                    ? "bg-emerald-100 text-emerald-700"
                                                    : "bg-rose-100 text-rose-700"
                                                }`}>
                                                    {a.recommendation}
                                                </span>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                            <div className="text-[10px] text-slate-500 mt-1">
                                legacy_rows = ad_account_ledger / liabilities
                                / account_transactions (كلها ستبقى deprecated
                                بدون حذف)
                            </div>
                        </div>

                        {(recomputeResult.accounts || [])
                            .filter(a => a.reasons?.length > 0)
                            .map((a, i) => (
                                <div key={`reason-${i}`}
                                     className="rounded-lg border border-rose-200
                                                 bg-rose-50/40 p-3 text-xs"
                                     data-testid={`recompute-reasons-${i}`}>
                                    <div className="font-extrabold text-rose-700
                                                     mb-1">
                                        ⚠️ {a.name} — أسباب MANUAL_REVIEW:
                                    </div>
                                    <ul className="list-disc list-inside
                                                    text-slate-700 space-y-1">
                                        {a.reasons.map((r, j) => (
                                            <li key={j}>{r}</li>
                                        ))}
                                    </ul>
                                    <div className="mt-2 text-[11px]
                                                     text-slate-600">
                                        <b>التفصيل:</b>&nbsp;
                                        {a.recommendation_detail}
                                    </div>
                                </div>
                        ))}
                    </div>
                )}
            </SectionCard>

            {/* ─── 6. Root-Cause Forensic (Phase 0.6) ─── */}
            <SectionCard
                title="6. Root-Cause Forensic لكل حساب MANUAL_REVIEW"
                icon={Database}
                actions={
                    <JsonActions payload={rootCauseResult}
                        filename={`root-cause-${rootCauseCp || "x"}.json`}
                        testidPrefix="rootcause" />
                }>
                <div className="flex flex-wrap items-center gap-3 mb-4">
                    <select
                        data-testid="root-cause-select"
                        className="rounded-lg border border-slate-300
                                   px-3 py-2 text-sm min-w-[300px]"
                        value={rootCauseCp}
                        onChange={(e) => setRootCauseCp(e.target.value)}>
                        <option value="">
                            — اختر حساباً للتحقيق العميق —
                        </option>
                        {adAccounts.map((a) => (
                            <option key={a.id} value={a.id}>
                                {a.name}
                                {a.platform ? ` · ${a.platform}` : ""}
                                {a.currency ? ` · ${a.currency}` : ""}
                            </option>
                        ))}
                    </select>
                    <button
                        onClick={runRootCause}
                        disabled={busy.rootCause || !rootCauseCp}
                        data-testid="run-root-cause-btn"
                        className="inline-flex items-center gap-2 px-4 py-2
                                    rounded-xl bg-emerald-600
                                    hover:bg-emerald-700 disabled:opacity-50
                                    text-white text-sm font-bold">
                        {busy.rootCause
                            ? <Spinner className="animate-spin" size={16} />
                            : <MagnifyingGlass size={16} />}
                        تشغيل Root-Cause
                    </button>
                </div>

                {!rootCauseResult && (
                    <p className="text-sm text-slate-500">
                        تحقيق عميق Read-Only: آخر 30 قيد، تجميعات
                        sub_account / entry_type / source / reference_id /
                        txn_group_id، Topups vs Spends، اكتشاف
                        misattribution، اقتراح تسوية محاسبية. لا يُجرى
                        أي تعديل.
                    </p>
                )}

                {rootCauseResult && (
                    <div className="space-y-5"
                         data-testid="root-cause-results">
                        <div className="pb-3 border-b border-slate-200">
                            <h3 className="text-base font-extrabold
                                            text-slate-900">
                                {rootCauseResult.ad_account?.name}
                            </h3>
                            <div className="text-[11px] text-slate-500
                                            flex flex-wrap gap-3 mt-1">
                                <span>currency:&nbsp;
                                    <b>{rootCauseResult.ad_account?.currency
                                        || "—"}</b></span>
                                <span>platform:&nbsp;
                                    <b>{rootCauseResult.ad_account
                                        ?.platform_from_counterparty || "<null>"}</b></span>
                                <span>cached_balance:&nbsp;
                                    <b className="num">
                                        {fmtMoney(rootCauseResult.ad_account
                                            ?.current_balance_cached)}</b></span>
                                <span>cached_debt:&nbsp;
                                    <b className="num">
                                        {fmtMoney(rootCauseResult.ad_account
                                            ?.debt_balance_cached)}</b></span>
                            </div>
                        </div>

                        {/* Identity hints */}
                        <div className="rounded-xl bg-slate-50/60 border
                                        border-slate-200 p-3">
                            <div className="text-[11px] font-extrabold
                                             text-slate-700 mb-1">
                                🆔 Identity Hints
                            </div>
                            <ul className="text-xs text-slate-700
                                            list-disc list-inside space-y-1">
                                {(rootCauseResult.identity_hints || [])
                                    .map((h, i) => <li key={i}>{h}</li>)}
                            </ul>
                            {Object.keys(rootCauseResult
                                .platform_hints_from_gl_metadata || {})
                                .length > 0 && (
                                <div className="mt-2 text-[11px]
                                                text-slate-600">
                                    <b>GL metadata.platform:</b>&nbsp;
                                    {Object.entries(rootCauseResult
                                        .platform_hints_from_gl_metadata)
                                        .map(([k, v]) => (
                                            <span key={k}
                                                  className="inline-block
                                                              px-1.5 py-0.5
                                                              rounded
                                                              bg-white
                                                              border
                                                              border-slate-200
                                                              ml-1">
                                                {k}: <b>{v}</b>
                                            </span>
                                    ))}
                                </div>
                            )}
                        </div>

                        {/* Topups vs Spends */}
                        <div>
                            <h4 className="text-sm font-extrabold mb-2">
                                💸 Topups vs Spends
                            </h4>
                            <div className="grid grid-cols-2 md:grid-cols-4
                                            gap-2 text-xs"
                                 data-testid="topups-vs-spends-grid">
                                {Object.entries(rootCauseResult
                                    .topups_vs_spends || {}).map(([k, v]) => (
                                    <div key={k}
                                         className="bg-white rounded border
                                                     border-slate-200 p-2">
                                        <div className="text-[9px] font-bold
                                                         text-slate-500">{k}</div>
                                        <div className="num text-base
                                                         font-extrabold
                                                         text-slate-900">
                                            {typeof v === "number"
                                                ? fmtMoney(v) : v}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>

                        {/* Aggregations */}
                        <details>
                            <summary className="cursor-pointer text-sm
                                                font-extrabold text-emerald-700">
                                📊 التجميعات بحسب sub_account / entry_type
                                / source / txn_group
                            </summary>
                            <div className="mt-3 space-y-3 text-xs">
                                {Object.entries(rootCauseResult
                                    .aggregations || {}).map(([k, agg]) => (
                                    <div key={k}>
                                        <div className="font-extrabold
                                                         text-slate-700 mb-1">
                                            {k}
                                        </div>
                                        <pre className="bg-slate-50 rounded
                                                        p-2 overflow-x-auto
                                                        text-[10px]">
{JSON.stringify(agg, null, 2)}
                                        </pre>
                                    </div>
                                ))}
                            </div>
                        </details>

                        {/* Misattribution */}
                        {(rootCauseResult.misattribution_signals || [])
                            .length > 0 && (
                            <div className="rounded-xl border border-amber-300
                                            bg-amber-50/40 p-3">
                                <div className="text-[11px] font-extrabold
                                                 text-amber-700 mb-1">
                                    ⚠️ إشارات Misattribution
                                </div>
                                <ul className="text-xs text-slate-700
                                                list-disc list-inside
                                                space-y-1">
                                    {rootCauseResult.misattribution_signals
                                        .map((m, i) => (
                                        <li key={i}>
                                            <b>{m.kind}</b>:&nbsp;
                                            <span className="num">
                                                {m.count}
                                            </span>
                                            &nbsp;— {m.note}
                                            {m.ids && (
                                                <div className="text-[10px]
                                                                text-slate-500
                                                                ml-4">
                                                    ids: {m.ids.join(", ")}
                                                </div>
                                            )}
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        )}

                        {/* Last 30 entries */}
                        <details>
                            <summary className="cursor-pointer text-sm
                                                font-extrabold text-emerald-700">
                                📜 آخر 30 قيد GL
                                ({rootCauseResult.last_30_gl_entries?.length
                                  || 0})
                            </summary>
                            <div className="mt-3 overflow-x-auto">
                                <table className="w-full text-[11px] border
                                                  border-slate-200 rounded">
                                    <thead>
                                        <tr className="bg-slate-100">
                                            <th className="px-2 py-1">posted_at</th>
                                            <th className="px-2 py-1">entry_type</th>
                                            <th className="px-2 py-1">sub_acct</th>
                                            <th className="px-2 py-1">side</th>
                                            <th className="px-2 py-1">amount</th>
                                            <th className="px-2 py-1">notes</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {(rootCauseResult.last_30_gl_entries
                                            || []).map((e, i) => (
                                            <tr key={i}
                                                className="border-b border-slate-100">
                                                <td className="px-2 py-1
                                                                text-[10px]">
                                                    {(e.posted_at || "")
                                                        .slice(0, 16)}
                                                </td>
                                                <td className="px-2 py-1">
                                                    {e.entry_type}
                                                </td>
                                                <td className="px-2 py-1">
                                                    {e.sub_account}
                                                </td>
                                                <td className={`px-2 py-1 ${
                                                    e.side === "debit"
                                                        ? "text-emerald-700"
                                                        : "text-rose-700"}`}>
                                                    {e.side}
                                                </td>
                                                <td className="px-2 py-1 num">
                                                    {fmtMoney(e.amount)}
                                                </td>
                                                <td className="px-2 py-1
                                                                text-slate-500
                                                                text-[10px]">
                                                    {(e.notes || "")
                                                        .slice(0, 40)}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </details>

                        {/* Accounting suggestions */}
                        {(rootCauseResult.accounting_suggestions || [])
                            .length > 0 && (
                            <div className="space-y-2"
                                 data-testid="accounting-suggestions">
                                <h4 className="text-sm font-extrabold">
                                    💡 الاقتراح المحاسبي
                                </h4>
                                {rootCauseResult.accounting_suggestions
                                    .map((s, i) => (
                                    <div key={i}
                                         className="rounded-lg border
                                                     border-emerald-200
                                                     bg-emerald-50/30 p-3">
                                        <div className="font-extrabold
                                                         text-emerald-700">
                                            {s.title}
                                        </div>
                                        <div className="text-xs text-slate-700
                                                         mt-1">
                                            {s.explain}
                                        </div>
                                        <div className="text-[11px]
                                                         text-slate-600 mt-2
                                                         pt-2 border-t
                                                         border-emerald-200">
                                            <b>الإجراء المُقترَح:</b>&nbsp;
                                            {s.action_hint}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}

                        {/* Decision matrix */}
                        <div className="rounded-xl border border-slate-200
                                         bg-slate-50/60 p-3">
                            <div className="text-[11px] font-extrabold
                                             text-slate-700 mb-2">
                                🎯 Decision Matrix
                            </div>
                            <div className="grid grid-cols-2 md:grid-cols-4
                                            gap-2 text-xs">
                                {Object.entries(rootCauseResult
                                    .decision_matrix || {}).map(([k, v]) => (
                                    <div key={k}
                                         className={`rounded p-2 border ${
                                            v
                                                ? "bg-amber-50 border-amber-300 text-amber-700"
                                                : "bg-slate-50 border-slate-200 text-slate-500"
                                         }`}>
                                        <div className="text-[10px] font-bold">
                                            {k}
                                        </div>
                                        <div className="font-extrabold">
                                            {v ? "نعم" : "لا"}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    </div>
                )}
            </SectionCard>
        </div>
    );
}
