import React from "react";
import {
  attemptAutomaticRecovery,
  recordSpaFailure,
} from "../spaRuntimeRecovery";

export default class AppCrashBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = {
      error: null,
      failureId: null,
    };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    const failure = recordSpaFailure("react_render_error", error, {
      componentStack: info?.componentStack,
      source: "react_error_boundary",
    });
    this.setState({ failureId: failure.id });
    attemptAutomaticRecovery({
      reason: "react_render_error",
      delayMs: 900,
    });
  }

  reloadPage = () => {
    window.location.reload();
  };

  openDashboard = () => {
    window.location.assign("/dashboard-v2");
  };

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <main
        className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-10"
        dir="rtl"
        data-mezan-crash-recovery="true"
        data-testid="app-crash-recovery"
      >
        <section className="w-full max-w-xl rounded-3xl border border-slate-200 bg-white p-7 text-center shadow-xl sm:p-10">
          <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-amber-100 text-3xl">
            ⚠️
          </div>
          <h1 className="mt-5 text-2xl font-black text-slate-950">
            تعذر عرض الصفحة مؤقتًا
          </h1>
          <p className="mt-3 text-sm font-semibold leading-7 text-slate-600">
            واجهة ميزان واجهت خطأ أثناء التنقل. سيحاول النظام استعادة الصفحة تلقائيًا مرة واحدة، ويمكنك إعادة تحميلها الآن دون انتظار.
          </p>
          <div className="mt-7 flex flex-col justify-center gap-3 sm:flex-row">
            <button
              type="button"
              onClick={this.reloadPage}
              className="rounded-xl bg-emerald-700 px-5 py-3 text-sm font-black text-white transition hover:bg-emerald-800"
            >
              إعادة تحميل الصفحة
            </button>
            <button
              type="button"
              onClick={this.openDashboard}
              className="rounded-xl border border-slate-300 bg-white px-5 py-3 text-sm font-black text-slate-700 transition hover:bg-slate-100"
            >
              العودة إلى الرئيسية
            </button>
          </div>
          {this.state.failureId && (
            <div className="mt-6 font-mono text-[10px] text-slate-400">
              رقم التشخيص: {this.state.failureId}
            </div>
          )}
        </section>
      </main>
    );
  }
}
