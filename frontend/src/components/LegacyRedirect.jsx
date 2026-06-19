import { Link, useLocation } from "react-router-dom";
import { ArrowLeft, Info } from "@phosphor-icons/react";

/**
 * Iter-250a — Legacy page redirect banner.
 *
 * Replaces deprecated financial pages with a "moved" notice that links
 * to the canonical replacement. The backend endpoints for the legacy
 * page remain intact (no code deletion) until a follow-up audit
 * approves removal.
 *
 * Usage in App.js:
 *   <Route path="/transfers" element={
 *     <ProtectedRoute><Layout>
 *       <LegacyRedirect
 *         oldLabel="التحويلات بين الحسابات"
 *         replacement="/new-transaction"
 *         replacementLabel="حركة مالية جديدة (موحدة)"
 *         reason="تم توحيد التحويلات داخل شاشة الإدخال المالي."
 *       />
 *     </Layout></ProtectedRoute>
 *   } />
 */
export default function LegacyRedirect({
    oldLabel,
    replacement,
    replacementLabel,
    reason,
}) {
    const location = useLocation();
    return (
        <div
            className="max-w-2xl mx-auto mt-10 p-8 rounded-2xl
                       border border-amber-200 bg-amber-50/60"
            data-testid="legacy-redirect-page"
        >
            <div className="flex items-start gap-3 mb-5">
                <div className="shrink-0 w-10 h-10 rounded-xl
                                bg-amber-500/15 text-amber-700
                                flex items-center justify-center">
                    <Info size={22} weight="duotone" />
                </div>
                <div>
                    <div className="text-[11px] font-bold text-amber-700
                                    uppercase tracking-wide">
                        🕰️ صفحة قديمة معطّلة
                    </div>
                    <h1 className="text-2xl font-extrabold text-slate-900"
                        style={{ fontFamily: "Tajawal" }}
                        data-testid="legacy-redirect-title">
                        {oldLabel || location.pathname}
                    </h1>
                </div>
            </div>

            <p className="text-sm text-slate-700 leading-relaxed mb-2">
                تم نقل هذه الوظيفة إلى الصفحة الجديدة المعتمدة.
            </p>
            {reason && (
                <p className="text-xs text-slate-500 leading-relaxed mb-6">
                    {reason}
                </p>
            )}

            <Link
                to={replacement}
                className="inline-flex items-center gap-2 px-4 py-2.5
                           rounded-xl bg-emerald-600 hover:bg-emerald-700
                           text-white text-sm font-bold transition"
                data-testid="legacy-redirect-cta"
            >
                <ArrowLeft size={16} weight="bold" />
                الانتقال إلى: {replacementLabel || replacement}
            </Link>

            <div className="mt-6 pt-4 border-t border-amber-200/70">
                <p className="text-[11px] text-slate-500">
                    إن كنت تعتمد على هذه الصفحة في مهمّة لم تجد لها
                    بديلاً مكافئاً، تواصل مع المدير لمراجعة الجرد
                    قبل اتخاذ إجراء.
                </p>
            </div>
        </div>
    );
}
