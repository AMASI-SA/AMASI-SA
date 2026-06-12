/**
 * Iter-144-stub — Placeholder page for the new shipping ledger.
 *
 * Created during the sidebar restructure so the new nav links don't
 * 404.  The real ledger UI (per-company unified balance table) is
 * implemented in Iter-144 proper after the user signs off on the
 * vision document.
 */
import { Link } from "react-router-dom";
import { Truck } from "@phosphor-icons/react";


export default function ShippingLedgerStub() {
    return (
        <div className="p-8 max-w-4xl mx-auto" data-testid="shipping-ledger-stub">
            <div className="rounded-2xl border-2 border-dashed border-slate-300 bg-slate-50 p-8 text-center">
                <Truck size={48} weight="duotone" className="mx-auto text-slate-400 mb-3" />
                <h1 className="text-2xl font-extrabold text-slate-900 mb-2">
                    أرصدة شركات الشحن (موحَّد)
                </h1>
                <p className="text-sm text-slate-600 mb-4 leading-relaxed">
                    هذه الصفحة قيد التطوير ضمن <strong>Iter-144</strong>. سيظهر هنا
                    جدول موحَّد لكل شركة شحن يحتوي على:
                </p>
                <ul className="text-right text-sm text-slate-700 max-w-xl mx-auto space-y-1 mb-5 list-disc pr-6">
                    <li>اسم الشركة</li>
                    <li>COD معتمد لنا (طلبات تم توصيلها)</li>
                    <li>COD غير معتمد/معلَّق (للمتابعة فقط)</li>
                    <li>أجور الشحن المستحقة (طلبات تم توصيلها)</li>
                    <li>رسوم COD</li>
                    <li>تحويلات الشركة إلى البنك</li>
                    <li>صافي الرصيد (موجب = لنا، سالب = علينا)</li>
                </ul>
                <div className="flex items-center justify-center gap-3">
                    <Link
                        to="/shipping-accounts"
                        className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-bold rounded-lg"
                        data-testid="ledger-stub-go-current"
                    >
                        الذهاب للحسابات الحالية ←
                    </Link>
                    <Link
                        to="/financial-position"
                        className="px-4 py-2 bg-slate-200 hover:bg-slate-300 text-slate-800 text-sm font-bold rounded-lg"
                        data-testid="ledger-stub-go-position"
                    >
                        المركز المالي
                    </Link>
                </div>
            </div>
        </div>
    );
}
