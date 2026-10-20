import { useId, useRef } from "react";
import { Bell, X } from "@phosphor-icons/react";

export default function SpecificationChangeNotice({ specification }) {
    const dialogRef = useRef(null);
    const triggerRef = useRef(null);
    const closeRef = useRef(null);
    const titleId = useId();
    const descriptionId = useId();
    const change = specification.change;
    if (!change) return null;

    return (
        <>
            <button
                type="button"
                ref={triggerRef}
                aria-haspopup="dialog"
                onClick={() => { dialogRef.current.showModal(); closeRef.current.focus(); }}
                aria-label={`عرض تغييرات ${specification.name}`}
                className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-red-50 text-red-700 hover:bg-red-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-700"
            >
                <Bell size={20} weight="fill" aria-hidden="true" />
            </button>
            <dialog
                ref={dialogRef}
                aria-labelledby={titleId}
                aria-describedby={descriptionId}
                onCancel={(event) => { event.preventDefault(); dialogRef.current.close(); }}
                onClose={() => triggerRef.current?.focus()}
                dir="rtl"
                className="m-auto max-h-[85vh] w-[calc(100%_-_2rem)] max-w-md overflow-y-auto rounded-3xl border-0 bg-white p-5 text-right shadow-xl backdrop:bg-slate-950/60"
            >
                <div className="flex items-start justify-between gap-3">
                    <h3 id={titleId} className="text-lg font-black text-slate-950">تغييرات خيار المنتج</h3>
                    <button ref={closeRef} type="button" onClick={() => dialogRef.current.close()} aria-label="إغلاق تفاصيل التغيير" className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-slate-100 text-slate-700">
                        <X size={20} aria-hidden="true" />
                    </button>
                </div>
                <p id={descriptionId} className="mt-2 text-sm font-bold text-slate-600">
                    مقارنة اسم الحقل وقيمته قبل التغيير وبعده.
                </p>
                <div className="mt-4 space-y-3">
                    {[
                        { title: "القديم", name: change.previous_name, value: change.previous_value },
                        { title: "الجديد", name: change.current_name, value: change.current_value },
                    ].map((version) => (
                        <section key={version.title} aria-label={version.title} className="rounded-2xl border border-slate-200 bg-slate-50 p-3">
                            <h4 className="font-black text-slate-900">{version.title}</h4>
                            <dl className="mt-2 space-y-2 text-sm">
                                <div><dt className="text-xs font-bold text-slate-500">اسم الحقل</dt><dd className="break-words font-bold text-slate-900">{version.name || "—"}</dd></div>
                                <div><dt className="text-xs font-bold text-slate-500">القيمة</dt><dd className="break-words font-bold text-slate-900">{version.value || "—"}</dd></div>
                            </dl>
                        </section>
                    ))}
                </div>
            </dialog>
        </>
    );
}
