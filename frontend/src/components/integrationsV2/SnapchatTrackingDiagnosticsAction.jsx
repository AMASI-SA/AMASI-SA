import { Pulse, ShieldCheck } from "@phosphor-icons/react";

export default function SnapchatTrackingDiagnosticsAction({
    integration,
    diagnosing = false,
    syncing = false,
    onDiagnose,
}) {
    if (integration?.provider !== "snapchat_ads") return null;
    const enabled = Boolean(integration.actions?.sync_data?.enabled)
        && !diagnosing
        && !syncing;
    const reason = integration.actions?.sync_data?.reason
        || "يتطلب ربط Snapchat Marketing API الأصلي.";
    return (
        <section
            className="rounded-xl border border-violet-200 bg-violet-50 p-3"
            data-testid="snapchat-tracking-diagnostics-action"
        >
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex min-w-0 items-start gap-2">
                    <ShieldCheck
                        size={20}
                        className="mt-0.5 shrink-0 text-violet-700"
                        weight="fill"
                    />
                    <div>
                        <div className="text-xs font-extrabold text-violet-950">
                            جودة Pixel وConversions API
                        </div>
                        <div className="mt-1 text-[11px] leading-5 text-violet-700">
                            قراءة آمنة لآخر 7 أيام: النطاقات، أنواع الأحداث، وملاحظات Signal Readiness.
                        </div>
                    </div>
                </div>
                <button
                    type="button"
                    onClick={() => onDiagnose?.(integration.provider)}
                    disabled={!enabled}
                    title={!enabled ? reason : undefined}
                    className={`inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border px-4 text-xs font-extrabold transition ${
                        enabled
                            ? "border-violet-700 bg-violet-700 text-white hover:bg-violet-800"
                            : "cursor-not-allowed border-violet-100 bg-white/60 text-violet-300"
                    }`}
                    data-testid="integration-snapchat_ads-tracking-diagnostics"
                >
                    <Pulse size={17} weight="bold" />
                    {diagnosing ? "جاري فحص التتبع…" : "فحص Pixel وCAPI"}
                </button>
            </div>
        </section>
    );
}
