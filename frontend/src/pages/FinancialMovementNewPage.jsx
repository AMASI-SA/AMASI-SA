// Iter-246 — This page is kept ALIVE only to honour the
// «forward-only / never break old routes» rule.  All entry logic has
// been merged into `/new-transaction` (UnifiedEntryScreen).  Hitting
// this URL now shows a single, dismissable banner pointing the user
// to the unified screen — no form, no duplication.

import { Link } from "react-router-dom";

export default function FinancialMovementNewPage() {
    return (
        <div className="max-w-2xl mx-auto p-6" dir="rtl"
             data-testid="financial-movement-new-redirect">
            <div className="bg-amber-50 border border-amber-300 rounded-lg p-5">
                <h1 className="text-xl font-bold text-amber-900 mb-2">
                    تم دمج هذه الشاشة 🔁
                </h1>
                <p className="text-sm text-amber-900 leading-7">
                    اعتباراً من Iter-246 أصبحت كل الحركات المالية
                    (فاتورة مورد، مصروف عام، أصل ثابت، …) تُسجَّل
                    من «حركة مالية جديدة» الموحَّدة. هذا المسار
                    مُحتفَظ به فقط لتفادي كسر الروابط القديمة.
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                    <Link
                        to="/new-transaction"
                        className="bg-emerald-600 hover:bg-emerald-700 text-white px-4 py-2 rounded text-sm font-bold"
                        data-testid="goto-unified-entry">
                        ⤴ الانتقال إلى الشاشة الموحَّدة
                    </Link>
                    <Link
                        to="/financial-movements"
                        className="border border-emerald-600 text-emerald-700 hover:bg-emerald-50 px-4 py-2 rounded text-sm font-bold"
                        data-testid="goto-movements-list">
                        📑 قائمة الحركات المالية
                    </Link>
                </div>
            </div>
        </div>
    );
}
