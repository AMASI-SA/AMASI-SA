export default function AuthRecoveryScreen({ onRetry }) {
    return (
        <main
            className="min-h-screen bg-slate-50 px-4 py-12 flex items-center justify-center"
            data-testid="auth-unavailable"
            dir="rtl"
        >
            <section
                className="w-full max-w-md rounded-2xl border border-amber-200 bg-white p-6 text-center shadow-sm"
                role="alert"
                aria-labelledby="auth-unavailable-title"
            >
                <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-amber-100 text-2xl">
                    ⚠️
                </div>
                <h1
                    id="auth-unavailable-title"
                    className="mt-4 text-xl font-black text-slate-950"
                >
                    تعذر التحقق من الجلسة مؤقتًا
                </h1>
                <p className="mt-2 text-sm font-semibold leading-6 text-slate-600">
                    يعيد ميزان الاتصال بالخادم تلقائيًا. لم يتم تسجيل خروجك،
                    ولن نعرض أي بيانات محمية حتى ينجح التحقق من الجلسة.
                </p>
                <div
                    className="mt-4 inline-flex items-center gap-2 rounded-full bg-emerald-50 px-3 py-1.5 text-xs font-black text-emerald-800"
                    role="status"
                    aria-live="polite"
                    data-testid="auth-auto-recovery-status"
                >
                    <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-600" />
                    جاري إعادة الاتصال تلقائيًا
                </div>
                <div className="mt-5 grid gap-2 sm:grid-cols-2">
                    <button
                        type="button"
                        onClick={onRetry}
                        className="rounded-xl bg-emerald-700 px-4 py-2.5 text-sm font-black text-white transition hover:bg-emerald-800 focus:outline-none focus:ring-2 focus:ring-emerald-300"
                        data-testid="auth-retry"
                    >
                        إعادة المحاولة الآن
                    </button>
                    <button
                        type="button"
                        onClick={() => window.location.reload()}
                        className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-black text-slate-700 transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-slate-300"
                        data-testid="auth-reload"
                    >
                        تحديث الصفحة
                    </button>
                </div>
            </section>
        </main>
    );
}
