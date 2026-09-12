import { Link } from "react-router-dom";


/**
 * Retained only as a safe destination for old imports/bookmarks.
 *
 * Legacy Mezan balances are never copied into Mezan 2. App.js redirects the
 * historic route to the P07 workspace; this component intentionally contains
 * no API mutation (or legacy-derived preview) capability.
 */
export default function MigrationWizard() {
    return (
        <div className="p-6 max-w-3xl mx-auto" data-testid="legacy-migration-disabled">
            <div className="rounded-2xl border-2 border-amber-300 bg-amber-50 p-6 text-amber-950">
                <h1 className="text-xl font-extrabold mb-2">ترحيل أرصدة ميزان القديم متوقف</h1>
                <p className="text-sm leading-7">
                    يبدأ ميزان 2 بأرصدة افتتاحية يدوية موثقة عند توقيت قطع واحد.
                    تبقى بيانات النظام القديم متاحة للقراءة والمقارنة فقط، ولا تُنسخ إلى الدفتر الجديد.
                </p>
                <Link
                    to="/integrations-v2?workspace=financial&page=opening-balances"
                    className="inline-flex mt-4 rounded-lg bg-amber-800 px-4 py-2 text-sm font-bold text-white"
                    data-testid="open-p07-opening-balances"
                >
                    الانتقال إلى مسار P07
                </Link>
            </div>
        </div>
    );
}
