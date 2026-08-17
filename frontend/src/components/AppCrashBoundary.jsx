import React from "react";
import {
  attemptAutomaticRecovery,
  recordSpaFailure,
} from "../spaRuntimeRecovery";

const SOFT_RETRY_DELAY_MS = 250;
const STABLE_RESET_DELAY_MS = 10_000;
const HARD_RECOVERY_FALLBACK_MS = 4_000;
const HEALTH_RETRY_INTERVAL_MS = 5_000;
const MAX_HEALTH_RETRY_ATTEMPTS = 24;

export default class AppCrashBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = {
      error: null,
      failureId: null,
      recoveryPhase: "healthy",
      retryNonce: 0,
    };
    this.softRetryUsed = false;
    this.healthRetryUsed = false;
    this.healthRetryAttempts = 0;
    this.softRetryTimer = 0;
    this.stableResetTimer = 0;
    this.hardRecoveryFallbackTimer = 0;
    this.healthRetryTimer = 0;
  }

  static getDerivedStateFromError(error) {
    return {
      error,
      recoveryPhase: "recovering",
    };
  }

  scheduleHealthRecovery = () => {
    if (
      this.healthRetryUsed
      || this.healthRetryTimer
      || this.healthRetryAttempts >= MAX_HEALTH_RETRY_ATTEMPTS
    ) {
      return;
    }

    this.healthRetryTimer = window.setTimeout(async () => {
      this.healthRetryTimer = 0;
      this.healthRetryAttempts += 1;
      try {
        const response = await window.fetch(
          `/api/health?spa_recovery=${Date.now()}`,
          {
            method: "GET",
            credentials: "same-origin",
            cache: "no-store",
            headers: { Accept: "application/json" },
          },
        );
        let payload = null;
        try {
          payload = await response.json();
        } catch {
          payload = null;
        }
        if (response.ok && payload?.ok === true) {
          this.healthRetryUsed = true;
          this.setState((previous) => ({
            error: null,
            failureId: null,
            recoveryPhase: "healthy",
            retryNonce: previous.retryNonce + 1,
          }));
          return;
        }
      } catch {
        // The origin may still be restarting. Keep the manual fallback visible
        // and retry the public health probe with a bounded interval.
      }
      this.scheduleHealthRecovery();
    }, HEALTH_RETRY_INTERVAL_MS);
  };

  componentDidCatch(error, info) {
    const failure = recordSpaFailure("react_render_error", error, {
      componentStack: info?.componentStack,
      source: "react_error_boundary",
    });

    // A navigation can briefly render against stale/transitional state after
    // an origin recovery or route switch. Retry the React subtree once before
    // showing a blocking crash card or reloading the entire application.
    if (!this.softRetryUsed) {
      this.softRetryUsed = true;
      this.setState({
        failureId: failure.id,
        recoveryPhase: "retrying",
      });
      this.softRetryTimer = window.setTimeout(() => {
        this.softRetryTimer = 0;
        this.setState((previous) => ({
          error: null,
          failureId: null,
          recoveryPhase: "healthy",
          retryNonce: previous.retryNonce + 1,
        }));
      }, SOFT_RETRY_DELAY_MS);
      return;
    }

    const reloadScheduled = attemptAutomaticRecovery({
      reason: "react_render_error",
      delayMs: 500,
    });
    this.setState({
      failureId: failure.id,
      recoveryPhase: reloadScheduled ? "reloading" : "failed",
    });

    if (!reloadScheduled) {
      this.scheduleHealthRecovery();
      return;
    }

    // If the browser refuses or cannot complete the reload, do not leave the
    // user on an endless recovery spinner. Fall back to the manual actions and
    // keep watching for the public origin to become healthy again.
    this.hardRecoveryFallbackTimer = window.setTimeout(() => {
      this.hardRecoveryFallbackTimer = 0;
      this.setState({ recoveryPhase: "failed" });
      this.scheduleHealthRecovery();
    }, HARD_RECOVERY_FALLBACK_MS);
  }

  componentDidUpdate(_previousProps, previousState) {
    if (previousState.error && !this.state.error) {
      if (this.stableResetTimer) window.clearTimeout(this.stableResetTimer);
      this.stableResetTimer = window.setTimeout(() => {
        this.stableResetTimer = 0;
        this.softRetryUsed = false;
        this.healthRetryUsed = false;
        this.healthRetryAttempts = 0;
      }, STABLE_RESET_DELAY_MS);
    }
  }

  componentWillUnmount() {
    if (this.softRetryTimer) window.clearTimeout(this.softRetryTimer);
    if (this.stableResetTimer) window.clearTimeout(this.stableResetTimer);
    if (this.hardRecoveryFallbackTimer) {
      window.clearTimeout(this.hardRecoveryFallbackTimer);
    }
    if (this.healthRetryTimer) window.clearTimeout(this.healthRetryTimer);
  }

  reloadPage = () => {
    window.location.reload();
  };

  openDashboard = () => {
    window.location.assign("/dashboard-v2");
  };

  renderRecoveryPending() {
    return (
      <main
        className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-10"
        dir="rtl"
        data-mezan-crash-recovery="true"
        data-testid="app-crash-recovery-pending"
      >
        <section className="w-full max-w-md rounded-3xl border border-slate-200 bg-white p-7 text-center shadow-lg sm:p-9">
          <div className="mx-auto h-11 w-11 animate-spin rounded-full border-4 border-emerald-100 border-t-emerald-700" />
          <h1 className="mt-5 text-xl font-black text-slate-950">
            جاري استعادة الصفحة…
          </h1>
          <p className="mt-2 text-sm font-semibold leading-7 text-slate-500">
            نعيد تهيئة واجهة ميزان تلقائيًا دون فقدان الجلسة.
          </p>
        </section>
      </main>
    );
  }

  render() {
    if (!this.state.error) {
      return (
        <React.Fragment key={this.state.retryNonce}>
          {this.props.children}
        </React.Fragment>
      );
    }

    if (this.state.recoveryPhase !== "failed") {
      return this.renderRecoveryPending();
    }

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
            تعذر على ميزان استعادة الواجهة تلقائيًا. أعد تحميل الصفحة، وإن تكرر الخطأ استخدم رقم التشخيص للمراجعة.
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
