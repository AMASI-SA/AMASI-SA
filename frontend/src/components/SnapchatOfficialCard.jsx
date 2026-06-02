import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowsClockwise, Info, Lightning, ChartLineUp } from "@phosphor-icons/react";
import api from "../lib/api";
import { formatMoney } from "../lib/format";

/**
 * SnapchatOfficialCard — read-only reference card showing official
 * Snapchat numbers using the ad account's NATIVE timezone (typically PDT/PT).
 *
 * This card is **isolated**: it never feeds any system calculation
 * (profits / expenses / dashboard ROAS / reports). It exists purely as a
 * comparison reference, with its own FX rate (3.752) and its own backend
 * collection (`snapchat_reference_stats`).
 */
function formatInt(n) {
    const v = Number(n || 0);
    if (!Number.isFinite(v)) return "0";
    return v.toLocaleString("en-US");
}

function MetricTile({ label, value, suffix, accent = "slate", testid }) {
    const accents = {
        slate: "border-slate-200 bg-white text-slate-900",
        emerald: "border-emerald-200 bg-emerald-50 text-emerald-900",
        amber: "border-amber-200 bg-amber-50 text-amber-900",
        blue: "border-blue-200 bg-blue-50 text-blue-900",
    };
    return (
        <div className={`border rounded-lg p-3 sm:p-4 ${accents[accent] || accents.slate}`} data-testid={testid}>
            <div className="text-xs font-semibold text-slate-500 mb-1" style={{ fontFamily: "Tajawal" }}>{label}</div>
            <div className="num text-xl sm:text-2xl font-extrabold leading-tight" style={{ fontFamily: "Tajawal" }}>
                {value}{suffix ? <span className="text-sm font-bold text-slate-500 ms-1">{suffix}</span> : null}
            </div>
        </div>
    );
}

export default function SnapchatOfficialCard() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [error, setError] = useState(null);
    const mounted = useRef(true);

    const fetchData = useCallback(async (force = false) => {
        if (force) setRefreshing(true); else setLoading(true);
        setError(null);
        try {
            const { data: payload } = await api.get(
                `/snapchat/reference-stats${force ? "?refresh=true" : ""}`,
            );
            if (mounted.current) setData(payload);
        } catch (e) {
            if (!mounted.current) return;
            const msg = e?.response?.data?.detail || e?.message || "تعذّر جلب بيانات Snapchat المرجعية";
            setError(String(msg));
        } finally {
            if (mounted.current) {
                setLoading(false);
                setRefreshing(false);
            }
        }
    }, []);

    useEffect(() => {
        mounted.current = true;
        fetchData(false);
        return () => { mounted.current = false; };
    }, [fetchData]);

    // Don't render anything while loading the very first time — keeps the
    // dashboard layout tight if the merchant hasn't connected Snapchat yet.
    if (loading) {
        return (
            <div
                className="mt-5 pt-5 border-t border-yellow-200 text-xs text-slate-500"
                data-testid="snap-official-loading"
                style={{ fontFamily: "Tajawal" }}
            >
                جاري تحميل بطاقة Snapchat Official (PDT) …
            </div>
        );
    }

    // Failed to fetch (e.g. account not connected): show a compact hint
    // but don't break the rest of the Snapchat section.
    if (error) {
        return (
            <div
                className="mt-5 pt-5 border-t border-yellow-200 text-xs text-amber-700 flex items-center gap-2"
                data-testid="snap-official-error"
                style={{ fontFamily: "Tajawal" }}
            >
                <Info size={14} weight="bold" />
                Snapchat Official (PDT): {error}
                <button
                    type="button"
                    onClick={() => fetchData(true)}
                    className="ms-auto inline-flex items-center gap-1 px-2 py-1 rounded text-amber-700 hover:bg-amber-100 font-semibold"
                    data-testid="snap-official-retry-btn"
                >
                    <ArrowsClockwise size={12} weight="bold" /> إعادة المحاولة
                </button>
            </div>
        );
    }

    if (!data) return null;

    const y = data.yesterday || {};
    const m = data.month || {};
    const accountTz = data.accounts?.[0]?.timezone || "Snapchat TZ";
    const fx = Number(data.fx_rate || 3.752);
    const yRoasBad = (y.spend_sar || 0) > 0 && (y.roas || 0) < 2;
    const mRoasBad = (m.spend_sar || 0) > 0 && (m.roas || 0) < 2;
    const lastSyncLocal = data.last_sync_at
        ? new Date(data.last_sync_at).toLocaleString("ar-SA", { dateStyle: "short", timeStyle: "medium" })
        : "—";

    return (
        <div
            className="mt-6 rounded-xl border-2 border-dashed border-indigo-300 bg-gradient-to-br from-indigo-50 via-white to-slate-50 p-4 sm:p-6"
            data-testid="snap-official-card"
            style={{ fontFamily: "Tajawal" }}
        >
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
                <div className="flex items-center gap-3 min-w-0">
                    <div className="w-10 h-10 rounded-lg bg-indigo-600 text-white flex items-center justify-center font-extrabold flex-shrink-0">
                        <ChartLineUp size={20} weight="bold" />
                    </div>
                    <div className="min-w-0">
                        <h3 className="text-base sm:text-lg font-extrabold text-indigo-900 truncate">
                            📊 Snapchat Official (PDT)
                        </h3>
                        <p className="text-[11px] text-slate-500 leading-tight">
                            بيانات رسمية من Snapchat Ads بتوقيت الحساب الإعلاني · <span className="font-semibold">{accountTz}</span>
                            <span className="mx-1">·</span>
                            عرض ومقارنة فقط
                        </p>
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    <span className="text-[11px] text-slate-500 hidden sm:inline" data-testid="snap-official-last-sync">
                        Last Sync (PDT): <span className="font-semibold text-slate-700">{lastSyncLocal}</span>
                    </span>
                    <button
                        type="button"
                        onClick={() => fetchData(true)}
                        disabled={refreshing}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-xs font-bold transition-colors"
                        data-testid="snap-official-refresh-btn"
                    >
                        <ArrowsClockwise size={12} weight="bold" className={refreshing ? "animate-spin" : ""} />
                        {refreshing ? "تحديث…" : "تحديث الآن"}
                    </button>
                </div>
            </div>

            {/* Yesterday (PDT) */}
            <div className="mb-5">
                <div className="text-xs font-bold text-indigo-800 mb-2 flex items-center gap-1.5">
                    <Lightning size={12} weight="fill" /> آخر يوم مكتمل (PDT): {y.date || "—"}
                </div>
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2 sm:gap-3" data-testid="snap-official-yesterday">
                    <MetricTile label="Spend (USD)" value={`$${formatMoney(y.spend_usd || 0)}`} testid="snap-official-yest-spend-usd" />
                    <MetricTile label="Spend (SAR)" value={formatMoney(y.spend_sar || 0)} suffix="ر.س" testid="snap-official-yest-spend-sar" />
                    <MetricTile label="Impressions" value={formatInt(y.impressions)} testid="snap-official-yest-impressions" />
                    <MetricTile label="Clicks" value={formatInt(y.swipes)} testid="snap-official-yest-clicks" />
                    <MetricTile label="Purchases" value={formatInt(y.purchases)} accent="emerald" testid="snap-official-yest-purchases" />
                    <MetricTile
                        label="ROAS"
                        value={(y.spend_sar || 0) > 0 ? `${Number(y.roas || 0).toFixed(2)}x` : "—"}
                        accent={yRoasBad ? "amber" : "emerald"}
                        testid="snap-official-yest-roas"
                    />
                </div>
            </div>

            {/* Current Month (PDT) */}
            <div>
                <div className="text-xs font-bold text-indigo-800 mb-2 flex items-center gap-1.5">
                    <Lightning size={12} weight="fill" /> الشهر الحالي (PDT): {m.start || "—"} → {m.end || "—"}
                </div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2 sm:gap-3" data-testid="snap-official-month">
                    <MetricTile label="Month Spend (USD)" value={`$${formatMoney(m.spend_usd || 0)}`} testid="snap-official-month-spend-usd" />
                    <MetricTile label="Month Spend (SAR)" value={formatMoney(m.spend_sar || 0)} suffix="ر.س" testid="snap-official-month-spend-sar" />
                    <MetricTile label="Month Purchases" value={formatInt(m.purchases)} accent="emerald" testid="snap-official-month-purchases" />
                    <MetricTile
                        label="Month ROAS"
                        value={(m.spend_sar || 0) > 0 ? `${Number(m.roas || 0).toFixed(2)}x` : "—"}
                        accent={mRoasBad ? "amber" : "emerald"}
                        testid="snap-official-month-roas"
                    />
                </div>
            </div>

            {/* Disclaimer + FX rate */}
            <div className="mt-4 pt-3 border-t border-indigo-200 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 text-[11px] text-slate-600">
                <p className="flex items-start gap-1.5" data-testid="snap-official-disclaimer">
                    <Info size={12} weight="bold" className="text-indigo-500 mt-0.5 flex-shrink-0" />
                    هذه البيانات مرجعية من Snapchat Ads حسب توقيت الحساب الإعلاني PDT ولا تدخل في التقارير المحاسبية أو الربحية.
                </p>
                <span className="text-slate-500" data-testid="snap-official-fx">
                    سعر الصرف المطبَّق: USD × <span className="font-bold text-slate-800">{fx}</span> = SAR
                </span>
            </div>
            {data.account_count > 1 && (
                <div className="mt-2 text-[11px] text-slate-500" data-testid="snap-official-accounts-note">
                    تم تجميع {data.account_count} حسابات إعلانية في هذه البطاقة.
                </div>
            )}
            {data.errors && data.errors.length > 0 && (
                <div className="mt-2 text-[11px] text-amber-700" data-testid="snap-official-warnings">
                    تحذيرات أثناء الجلب: {data.errors.length} — البيانات قد تكون جزئية.
                </div>
            )}
        </div>
    );
}
