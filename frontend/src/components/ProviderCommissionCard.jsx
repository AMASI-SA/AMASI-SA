import { useState, useEffect } from "react";
import { CaretDown, CaretUp, CreditCard, Wallet, Receipt, Percent, ArrowUUpLeft } from "@phosphor-icons/react";
import { formatMoney, formatInt } from "../lib/format";

/**
 * Iter-73 — Per-provider commission card.
 *
 * Shows for ONE payment provider (Salla / Tamara / Tabby / Emkan):
 *   • Gross sales (إجمالي قبل العمولة)
 *   • Net after fee  (صافي بعد العمولة)
 *   • Orders count   (عدد الطلبات)
 *   • Commission %   (نسبة العمولة [+ VAT])
 *
 * Collapsible — toggle state persists per-provider in localStorage so the
 * merchant's preferred view sticks across reloads.
 */
export default function ProviderCommissionCard({ provider, accent, Icon = CreditCard, testid }) {
    const storageKey = `provider-commission-collapsed:${provider?.name || "?"}`;
    const [collapsed, setCollapsed] = useState(() => {
        try { return localStorage.getItem(storageKey) === "1"; } catch { return false; }
    });
    useEffect(() => {
        try { localStorage.setItem(storageKey, collapsed ? "1" : "0"); } catch { /* ignore */ }
    }, [collapsed, storageKey]);

    if (!provider) return null;

    const gross = Number(provider.total_sales || 0);
    const fee = Number(provider.fee_amount || provider.base_commission || 0);
    const refunded = Number(provider.refunded_amount || 0);
    const refundsCount = Number(provider.refunds_count || 0);
    // Net after BOTH commission and refunds — matches what actually lands
    // in the merchant's wallet for this provider in this period.
    const net = gross - fee - refunded;
    const orders = Number(provider.orders_count || 0);
    const pct = Number(provider.commission_percent || 0);
    const vat = Number(provider.vat_percent || 0);
    const fixed = Number(provider.fixed_fee || 0);

    const tones = {
        emerald: "bg-emerald-50 border-emerald-200",
        violet:  "bg-violet-50  border-violet-200",
        sky:     "bg-sky-50     border-sky-200",
        amber:   "bg-amber-50   border-amber-200",
    };
    const accentRing = {
        emerald: "text-emerald-700",
        violet:  "text-violet-700",
        sky:     "text-sky-700",
        amber:   "text-amber-700",
    };

    return (
        <div className={`rounded-xl border ${tones[accent] || tones.emerald}`} data-testid={testid}>
            <button
                type="button"
                onClick={() => setCollapsed((c) => !c)}
                className="w-full flex items-center justify-between px-4 py-3"
                data-testid={`${testid}-toggle`}
            >
                <div className="flex items-center gap-2">
                    <Icon size={20} weight="duotone" className={accentRing[accent] || accentRing.emerald} />
                    <span className="font-bold text-foreground" style={{ fontFamily: "Tajawal" }}>
                        {provider.name}
                    </span>
                    <span className="text-[11px] text-muted-foreground">({formatInt(orders)} طلب)</span>
                </div>
                {collapsed ? <CaretDown size={16} /> : <CaretUp size={16} />}
            </button>

            {!collapsed && (
                <div className="px-4 pb-4 grid grid-cols-2 gap-3" data-testid={`${testid}-body`}>
                    <div className="rounded-lg bg-white/70 p-3 border border-white/40">
                        <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                            <Wallet size={12} /> إجمالي قبل العمولة
                        </div>
                        <div className="num text-lg font-extrabold text-foreground mt-1" data-testid={`${testid}-gross`}>
                            {formatMoney(gross)}
                        </div>
                    </div>
                    <div className="rounded-lg bg-white/70 p-3 border border-white/40">
                        <div className={`flex items-center gap-1.5 text-[11px] ${accentRing[accent] || accentRing.emerald}`}>
                            <Receipt size={12} /> صافي بعد العمولة
                        </div>
                        <div className={`num text-lg font-extrabold mt-1 ${accentRing[accent] || accentRing.emerald}`} data-testid={`${testid}-net`}>
                            {formatMoney(net)}
                        </div>
                    </div>
                    <div className="rounded-lg bg-white/70 p-3 border border-white/40">
                        <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                            <CreditCard size={12} /> عدد الطلبات
                        </div>
                        <div className="num text-lg font-extrabold text-foreground mt-1" data-testid={`${testid}-orders`}>
                            {formatInt(orders)}
                        </div>
                    </div>
                    <div className="rounded-lg bg-white/70 p-3 border border-white/40">
                        <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                            <Percent size={12} /> نسبة العمولة
                        </div>
                        <div className="text-lg font-extrabold text-foreground mt-1" data-testid={`${testid}-rate`}>
                            <span className="num">{pct.toFixed(2)}%</span>
                            {vat > 0 && (
                                <span className="text-[10px] text-muted-foreground mr-1 num">
                                    + {vat.toFixed(0)}% ضريبة
                                </span>
                            )}
                            {fixed > 0 && (
                                <span className="text-[10px] text-muted-foreground mr-1 num">
                                    + {fixed.toFixed(2)} ر.س/طلب
                                </span>
                            )}
                        </div>
                    </div>
                    <div className="rounded-lg bg-white/70 p-3 border border-white/40">
                        <div className="flex items-center gap-1.5 text-[11px] text-rose-700">
                            <ArrowUUpLeft size={12} /> المبالغ المسترجعة
                        </div>
                        <div className="num text-lg font-extrabold text-rose-700 mt-1" data-testid={`${testid}-refunded`}>
                            {formatMoney(refunded)}
                        </div>
                        <div className="text-[10px] text-rose-700/70 mt-0.5">
                            {refundsCount > 0 ? `${formatInt(refundsCount)} عملية تسوية` : "لا توجد تسويات"}
                        </div>
                    </div>
                    <div className="col-span-2 text-[10px] text-muted-foreground border-t border-white/40 pt-2">
                        العمولة المخصومة: <span className="num font-bold">{formatMoney(fee)}</span>
                        {refunded > 0 && (
                            <>
                                {" · "}
                                المبالغ المسترجعة: <span className="num font-bold text-rose-700">{formatMoney(refunded)}</span>
                            </>
                        )}
                        {" · "}
                        مصدر النسبة: إعدادات طرق الدفع
                    </div>
                </div>
            )}
        </div>
    );
}
