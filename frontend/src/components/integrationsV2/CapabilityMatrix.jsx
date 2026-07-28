import { CheckCircle, Clock, LockKey, Question, WarningCircle } from "@phosphor-icons/react";
import ProviderMark from "./ProviderMark";

const STATE = {
    available: {
        label: "متاح",
        Icon: CheckCircle,
        className: "border-emerald-200 bg-emerald-50 text-emerald-700",
    },
    approval_required: {
        label: "يحتاج اعتماد",
        Icon: LockKey,
        className: "border-amber-200 bg-amber-50 text-amber-800",
    },
    blocked_missing_permission: {
        label: "صلاحية ناقصة",
        Icon: WarningCircle,
        className: "border-rose-200 bg-rose-50 text-rose-700",
    },
    blocked_missing_data: {
        label: "بيانات ناقصة",
        Icon: WarningCircle,
        className: "border-orange-200 bg-orange-50 text-orange-700",
    },
    not_connected: {
        label: "غير متصل",
        Icon: Question,
        className: "border-slate-200 bg-slate-50 text-slate-600",
    },
    planned: {
        label: "مخطط",
        Icon: Clock,
        className: "border-violet-200 bg-violet-50 text-violet-700",
    },
    unknown: {
        label: "غير معروف",
        Icon: Question,
        className: "border-slate-200 bg-slate-50 text-slate-600",
    },
};

function CapabilityState({ entry }) {
    const state = STATE[entry?.state] || STATE.unknown;
    const { Icon } = state;
    return (
        <span
            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-extrabold ${state.className}`}
            title={entry?.reason}
        >
            <Icon size={14} weight="fill" />
            {state.label}
        </span>
    );
}

export default function CapabilityMatrix({ providers }) {
    const rows = (providers || []).filter((provider) => (
        Object.keys(provider.capabilities || {}).length > 0
    ));

    if (!rows.length) {
        return (
            <div className="rounded-xl border border-dashed border-slate-200 bg-white p-10 text-center text-sm text-slate-500">
                لا توجد مصفوفة قدرات متاحة بعد.
            </div>
        );
    }

    return (
        <div className="space-y-4" data-testid="integration-capability-matrix">
            {rows.map((provider) => (
                <section
                    key={provider.provider}
                    className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5"
                >
                    <div className="mb-4 flex items-center gap-3">
                        <ProviderMark provider={provider.provider} size="sm" />
                        <div>
                            <h2 className="font-extrabold text-slate-900">{provider.name_ar}</h2>
                            <p className="text-xs text-slate-400">{provider.name}</p>
                        </div>
                    </div>
                    <div className="divide-y divide-slate-100 rounded-xl border border-slate-100">
                        {Object.entries(provider.capabilities || {}).map(([key, entry]) => (
                            <div
                                key={key}
                                className="grid gap-2 px-3 py-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center sm:px-4"
                                data-testid={`capability-${provider.provider}-${key}`}
                            >
                                <div className="min-w-0">
                                    <div className="break-all font-mono text-xs font-bold text-slate-800">
                                        {key}
                                    </div>
                                    <div className="mt-1 text-xs leading-5 text-slate-500">
                                        {entry.reason}
                                    </div>
                                </div>
                                <div className="justify-self-start sm:justify-self-end">
                                    <CapabilityState entry={entry} />
                                </div>
                            </div>
                        ))}
                    </div>
                </section>
            ))}
        </div>
    );
}
