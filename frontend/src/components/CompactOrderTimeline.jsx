import { useMemo, useState } from "react";
import { CalendarBlank, Clock, ClockCounterClockwise, User } from "@phosphor-icons/react";

function isPresent(value) {
    return value !== null && value !== undefined && value !== "";
}

function firstPresent(...values) {
    return values.find(isPresent);
}

function actorName(value) {
    if (!isPresent(value)) return "غير محدد";
    if (typeof value === "string" || typeof value === "number") return String(value);
    if (Array.isArray(value)) return actorName(value[0]);
    return String(firstPresent(
        value.name,
        value.display_name,
        value.full_name,
        value.username,
        value.email,
        value.label,
        value.id,
        "غير محدد",
    ));
}

function eventDate(event) {
    return firstPresent(
        event.created_at,
        event.updated_at,
        event.timestamp,
        event.date,
        event.occurred_at,
        event.time,
    );
}

function eventTitle(event) {
    const previous = firstPresent(event.previous_status, event.old_status, event.from_status, event.from);
    const current = firstPresent(event.new_status, event.status, event.to_status, event.to, event.title, event.name, event.event_name, event.type);
    if (previous && current && String(previous) !== String(current)) return `${previous} ← ${current}`;
    return String(current || "تم تحديث الطلب");
}

function eventActor(event) {
    return actorName(firstPresent(
        event.actor,
        event.user,
        event.employee,
        event.staff,
        event.updated_by,
        event.created_by,
        event.performed_by,
        event.author,
    ));
}

function relativeTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    const seconds = Math.round((date.getTime() - Date.now()) / 1000);
    const formatter = new Intl.RelativeTimeFormat("ar", { numeric: "auto" });
    const ranges = [
        [31536000, "year"],
        [2592000, "month"],
        [604800, "week"],
        [86400, "day"],
        [3600, "hour"],
        [60, "minute"],
        [1, "second"],
    ];
    for (const [amount, unit] of ranges) {
        if (Math.abs(seconds) >= amount || unit === "second") return formatter.format(Math.round(seconds / amount), unit);
    }
    return "";
}

function fullDate(value) {
    if (!value) return "التاريخ غير متوفر";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("ar-SA-u-nu-latn", {
        timeZone: "Asia/Riyadh",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: true,
    }).format(date);
}

function normalizeEvents(order) {
    const candidates = [
        order.timeline,
        order.events,
        order.order_events,
        order.history,
        order.status_history,
        order.activity_log,
        order.audit_log,
    ];
    const source = candidates.find(Array.isArray) || [];
    const events = source.filter((event) => event && typeof event === "object").map((event, index) => ({
        ...event,
        _key: firstPresent(event.id, event.event_id, event.uuid, `${eventDate(event) || "event"}-${index}`),
        _date: eventDate(event),
        _title: eventTitle(event),
        _actor: eventActor(event),
    }));

    if (!events.length && order.created_at) {
        events.push({
            _key: "order-created",
            _date: order.created_at,
            _title: "تم إنشاء الطلب",
            _actor: actorName(firstPresent(order.created_by, order.customer, "العميل")),
        });
    }

    if (order.updated_at && order.updated_at !== order.created_at) {
        const alreadyIncluded = events.some((event) => event._date === order.updated_at);
        if (!alreadyIncluded) {
            events.push({
                _key: "order-updated",
                _date: order.updated_at,
                _title: String(firstPresent(order.status_native, order.status, "تم تحديث الطلب")),
                _actor: actorName(firstPresent(order.updated_by, order.last_updated_by)),
            });
        }
    }

    return events.sort((a, b) => {
        const left = new Date(a._date || 0).getTime();
        const right = new Date(b._date || 0).getTime();
        return right - left;
    });
}

export default function CompactOrderTimeline({ order }) {
    const [selected, setSelected] = useState(null);
    const events = useMemo(() => normalizeEvents(order || {}), [order]);

    return (
        <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm" data-testid="order-v2-timeline">
            <div className="mb-3 flex items-center gap-2">
                <div className="rounded-lg bg-violet-100 p-2 text-violet-700"><ClockCounterClockwise size={20} weight="fill" /></div>
                <h2 className="font-extrabold text-slate-950">سجل الطلب</h2>
            </div>

            {events.length === 0 ? (
                <div className="rounded-xl border border-dashed border-slate-300 px-4 py-3 text-center text-sm text-slate-500">لا توجد أحداث مسجلة لهذا الطلب.</div>
            ) : (
                <div className="overflow-x-auto pb-1">
                    <div className="flex min-w-max items-center gap-2" dir="rtl">
                        {events.map((event) => {
                            const active = selected?._key === event._key;
                            return (
                                <button
                                    key={event._key}
                                    type="button"
                                    onClick={() => setSelected(active ? null : event)}
                                    title={fullDate(event._date)}
                                    className={`inline-flex h-12 shrink-0 items-center gap-2 rounded-xl border px-3 text-sm transition ${active ? "border-teal-300 bg-teal-50 text-teal-900" : "border-slate-200 bg-white text-slate-700 hover:border-teal-200 hover:bg-teal-50/50"}`}
                                >
                                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-teal-50 text-teal-600"><Clock size={18} weight="bold" /></span>
                                    <span className="max-w-[180px] truncate font-extrabold">{event._title}</span>
                                    <span className="inline-flex items-center gap-1 whitespace-nowrap text-slate-500"><User size={15} />{event._actor}</span>
                                    <span className="whitespace-nowrap text-xs text-slate-400">{relativeTime(event._date)}</span>
                                </button>
                            );
                        })}
                    </div>
                </div>
            )}

            {selected && (
                <div className="mt-3 inline-flex max-w-full items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
                    <CalendarBlank size={17} className="shrink-0 text-teal-600" />
                    <span className="num whitespace-nowrap">{fullDate(selected._date)}</span>
                    <span className="text-slate-300">•</span>
                    <span className="truncate">بواسطة: <b>{selected._actor}</b></span>
                </div>
            )}
        </section>
    );
}
