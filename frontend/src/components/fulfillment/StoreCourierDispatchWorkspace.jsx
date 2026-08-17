import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
    ArrowClockwise,
    Barcode,
    CheckCircle,
    Package,
    SpinnerGap,
    Truck,
    UserCircle,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    assignStoreCourierShipment,
    listStoreCourierAssignments,
    listStoreCouriers,
} from "../../services/storeCourierDispatch";
import ShippingBarcodeScanner from "./ShippingBarcodeScanner";


function dateLabel(value) {
    if (!value) return "—";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "—";
    return new Intl.DateTimeFormat("ar-SA-u-nu-latn", {
        dateStyle: "medium",
        timeStyle: "short",
    }).format(parsed);
}


export default function StoreCourierDispatchWorkspace() {
    const [couriers, setCouriers] = useState([]);
    const [selectedCourierId, setSelectedCourierId] = useState("");
    const [assignments, setAssignments] = useState([]);
    const [loading, setLoading] = useState(true);
    const [assignmentsLoading, setAssignmentsLoading] = useState(false);
    const [error, setError] = useState("");
    const [scannerOpen, setScannerOpen] = useState(false);
    const [scannerBusy, setScannerBusy] = useState(false);
    const [scannerError, setScannerError] = useState("");
    const [lastAssignedOrder, setLastAssignedOrder] = useState("");
    const scannerLock = useRef(false);

    const selectedCourier = useMemo(
        () => couriers.find((courier) => courier.id === selectedCourierId) || null,
        [couriers, selectedCourierId],
    );

    const loadCouriers = useCallback(async ({ silent = false } = {}) => {
        if (!silent) setLoading(true);
        setError("");
        try {
            const result = await listStoreCouriers();
            const rows = result.items || [];
            setCouriers(rows);
            setSelectedCourierId((current) => (
                current && rows.some((row) => row.id === current) ? current : ""
            ));
        } catch (loadError) {
            setError(loadError.message);
        } finally {
            if (!silent) setLoading(false);
        }
    }, []);

    const loadAssignments = useCallback(async (courierId, { silent = false } = {}) => {
        if (!courierId) {
            setAssignments([]);
            return;
        }
        if (!silent) setAssignmentsLoading(true);
        try {
            const result = await listStoreCourierAssignments({
                courierUserId: courierId,
                limit: 100,
            });
            setAssignments(result.items || []);
        } catch (loadError) {
            if (!silent) setError(loadError.message);
        } finally {
            if (!silent) setAssignmentsLoading(false);
        }
    }, []);

    useEffect(() => {
        void loadCouriers();
    }, [loadCouriers]);

    useEffect(() => {
        setLastAssignedOrder("");
        setScannerError("");
        void loadAssignments(selectedCourierId);
    }, [loadAssignments, selectedCourierId]);

    useEffect(() => {
        if (!selectedCourierId) return undefined;
        const timer = window.setInterval(() => {
            void loadCouriers({ silent: true });
            void loadAssignments(selectedCourierId, { silent: true });
        }, 15000);
        return () => window.clearInterval(timer);
    }, [loadAssignments, loadCouriers, selectedCourierId]);

    const openScanner = () => {
        if (!selectedCourier) {
            toast.error("اختر الموصل أولًا");
            return;
        }
        setScannerError("");
        setScannerOpen(true);
    };

    const handleBarcode = useCallback(async (barcode) => {
        if (!selectedCourier || scannerLock.current) return;
        scannerLock.current = true;
        setScannerBusy(true);
        setScannerError("");
        try {
            const result = await assignStoreCourierShipment(
                selectedCourier.id,
                barcode,
            );
            const shipment = result.shipment;
            if (shipment) {
                setAssignments((current) => [
                    shipment,
                    ...current.filter((row) => row.order_number !== shipment.order_number),
                ]);
                setLastAssignedOrder(shipment.order_number || "");
            }
            toast.success(
                result.already_assigned
                    ? `الشحنة مسندة إلى ${selectedCourier.name} مسبقًا`
                    : `تم إسناد الشحنة إلى ${selectedCourier.name}`,
            );
            setScannerOpen(false);
            await loadCouriers({ silent: true });
        } catch (scanError) {
            setScannerError(scanError.message);
        } finally {
            scannerLock.current = false;
            setScannerBusy(false);
        }
    }, [loadCouriers, selectedCourier]);

    if (loading) {
        return (
            <div className="flex min-h-72 items-center justify-center" data-testid="store-courier-dispatch-loading">
                <SpinnerGap size={36} className="animate-spin text-violet-700" />
            </div>
        );
    }

    return (
        <section className="mx-auto w-full max-w-6xl space-y-5" dir="rtl" data-testid="store-courier-dispatch-workspace">
            <header className="overflow-hidden rounded-3xl border border-violet-200 bg-white shadow-sm">
                <div className="bg-gradient-to-l from-violet-800 via-violet-700 to-indigo-700 p-5 text-white sm:p-7">
                    <div className="flex items-start gap-4">
                        <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-white/15">
                            <Truck size={31} weight="duotone" />
                        </span>
                        <div>
                            <div className="text-xs font-black text-violet-100">ضمن مسار إدارة التجهيز</div>
                            <h2 className="mt-1 text-2xl font-black sm:text-3xl">إدارة الموصلين</h2>
                            <p className="mt-2 max-w-3xl text-sm font-bold leading-7 text-violet-100">
                                اختر الموصل المسؤول أولًا، ثم صوّر QR الموجود على بوليصة أماسي. تُسند الشحنة فورًا إلى الموصل المختار وتظهر في قائمته فقط.
                            </p>
                        </div>
                    </div>
                </div>
            </header>

            {error && (
                <div className="flex items-start gap-2 rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm font-black text-rose-900" role="alert">
                    <WarningCircle size={22} weight="fill" />
                    <span>{error}</span>
                </div>
            )}

            <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm" data-testid="store-courier-selector">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <div className="text-xs font-black text-violet-700">الخطوة 1</div>
                        <h3 className="mt-1 text-xl font-black text-slate-950">اختر الموصل</h3>
                        <p className="mt-1 text-sm font-bold text-slate-500">يبقى الموصل محددًا حتى تغيّره، لتصوير عدة شحنات متتالية له.</p>
                    </div>
                    <button
                        type="button"
                        onClick={() => void loadCouriers()}
                        className="inline-flex min-h-11 items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 text-sm font-black text-slate-700"
                        data-testid="refresh-store-couriers"
                    >
                        <ArrowClockwise size={20} weight="bold" /> تحديث
                    </button>
                </div>

                {!couriers.length ? (
                    <div className="mt-5 rounded-2xl border-2 border-dashed border-slate-200 p-8 text-center text-slate-500">
                        <UserCircle size={44} className="mx-auto mb-2 text-slate-400" weight="duotone" />
                        <div className="font-black">لا يوجد موظف لديه دور «مندوب توصيل المتجر».</div>
                        <p className="mt-1 text-xs font-bold">أضف الدور للموصل من إدارة الموظفين، ثم حدّث هذه الصفحة.</p>
                    </div>
                ) : (
                    <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                        {couriers.map((courier) => {
                            const active = courier.id === selectedCourierId;
                            return (
                                <button
                                    key={courier.id}
                                    type="button"
                                    onClick={() => setSelectedCourierId(courier.id)}
                                    className={`rounded-2xl border p-4 text-right transition ${active ? "border-violet-600 bg-violet-50 ring-2 ring-violet-100" : "border-slate-200 bg-white hover:border-violet-300"}`}
                                    data-testid="store-courier-option"
                                    aria-pressed={active}
                                >
                                    <div className="flex items-start justify-between gap-3">
                                        <span className={`flex h-11 w-11 items-center justify-center rounded-2xl ${active ? "bg-violet-700 text-white" : "bg-slate-100 text-slate-600"}`}>
                                            <UserCircle size={26} weight="duotone" />
                                        </span>
                                        {active && <CheckCircle size={24} className="text-violet-700" weight="fill" />}
                                    </div>
                                    <div className="mt-3 text-base font-black text-slate-950">{courier.name || "موصل"}</div>
                                    <div className="mt-1 text-xs font-bold text-slate-500" dir="ltr">{courier.phone || courier.email || "—"}</div>
                                    <div className="mt-3 grid grid-cols-3 gap-1 rounded-xl bg-slate-50 p-2 text-center text-[10px] font-black text-slate-600">
                                        <div><div className="text-sm text-slate-950">{courier.assigned_count || 0}</div>مسندة</div>
                                        <div><div className="text-sm text-slate-950">{courier.waiting_pickup_count || 0}</div>بانتظار الاستلام</div>
                                        <div><div className="text-sm text-slate-950">{courier.delivering_count || 0}</div>جاري التوصيل</div>
                                    </div>
                                </button>
                            );
                        })}
                    </div>
                )}
            </section>

            <section className={`rounded-3xl border p-5 shadow-sm ${selectedCourier ? "border-emerald-200 bg-white" : "border-slate-200 bg-slate-50"}`} data-testid="store-courier-scan-step">
                <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                    <div>
                        <div className="text-xs font-black text-emerald-700">الخطوة 2</div>
                        <h3 className="mt-1 text-xl font-black text-slate-950">صوّر الشحنة لإسنادها</h3>
                        <p className="mt-1 text-sm font-bold text-slate-500">
                            {selectedCourier
                                ? `كل شحنة تصوّرها الآن ستُسند إلى ${selectedCourier.name}.`
                                : "اختر الموصل أولًا؛ لن يفتح التصوير من دون تحديد المسؤول."}
                        </p>
                    </div>
                    <button
                        type="button"
                        onClick={openScanner}
                        disabled={!selectedCourier}
                        className="inline-flex min-h-14 items-center justify-center gap-2 rounded-2xl bg-emerald-700 px-6 text-base font-black text-white disabled:cursor-not-allowed disabled:opacity-40"
                        data-testid="open-store-courier-assignment-scanner"
                    >
                        <Barcode size={25} weight="bold" /> تصوير شحنة
                    </button>
                </div>
                {lastAssignedOrder && (
                    <div className="mt-4 flex items-center gap-2 rounded-2xl border border-emerald-200 bg-emerald-50 p-3 text-sm font-black text-emerald-900" data-testid="store-courier-last-assignment">
                        <CheckCircle size={22} weight="fill" /> تم إسناد الطلب #{lastAssignedOrder} إلى {selectedCourier?.name}
                    </div>
                )}
            </section>

            <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm" data-testid="selected-store-courier-assignments">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <h3 className="text-xl font-black text-slate-950">شحنات الموصل المحدد</h3>
                        <p className="mt-1 text-sm font-bold text-slate-500">
                            {selectedCourier ? selectedCourier.name : "اختر موصلًا لعرض شحناته."}
                        </p>
                    </div>
                    {selectedCourier && (
                        <span className="rounded-full bg-violet-100 px-3 py-1 text-xs font-black text-violet-800">{assignments.length} شحنة</span>
                    )}
                </div>

                {assignmentsLoading ? (
                    <div className="flex min-h-32 items-center justify-center"><SpinnerGap size={28} className="animate-spin text-violet-700" /></div>
                ) : !selectedCourier ? (
                    <div className="mt-5 rounded-2xl border-2 border-dashed border-slate-200 p-7 text-center text-sm font-black text-slate-500">اختر الموصل من الأعلى.</div>
                ) : !assignments.length ? (
                    <div className="mt-5 rounded-2xl border-2 border-dashed border-slate-200 p-7 text-center text-sm font-black text-slate-500">
                        <Package size={38} className="mx-auto mb-2 text-slate-400" weight="duotone" />لا توجد شحنات مسندة لهذا الموصل.
                    </div>
                ) : (
                    <div className="mt-5 grid gap-3 lg:grid-cols-2">
                        {assignments.map((shipment) => (
                            <article key={shipment.order_number} className="rounded-2xl border border-slate-200 bg-white p-4" data-testid="store-courier-assigned-shipment">
                                <div className="flex items-start justify-between gap-3">
                                    <div>
                                        <div className="text-xs font-black text-slate-400">طلب #{shipment.order_number}</div>
                                        <div className="mt-1 text-lg font-black text-slate-950">{shipment.customer_name || "—"}</div>
                                        <div className="mt-1 text-xs font-bold text-slate-500">{shipment.city || "—"} · {shipment.customer_mobile || "—"}</div>
                                    </div>
                                    <span className="rounded-full bg-amber-100 px-3 py-1 text-[11px] font-black text-amber-900">
                                        {shipment.stage === "delivering" ? "جاري التوصيل" : shipment.stage === "delivered" ? "تم التوصيل" : "بانتظار استلام الموصل"}
                                    </span>
                                </div>
                                <div className="mt-3 rounded-xl bg-slate-50 p-3 text-xs font-bold text-slate-600">
                                    أُسندت: <span className="font-black text-slate-900">{dateLabel(shipment.assigned_at)}</span>
                                    {shipment.assigned_by_name ? ` · بواسطة ${shipment.assigned_by_name}` : ""}
                                </div>
                            </article>
                        ))}
                    </div>
                )}
            </section>

            {scannerOpen && selectedCourier && (
                <ShippingBarcodeScanner
                    title={`إسناد شحنة إلى ${selectedCourier.name}`}
                    description="صوّر QR الموجود على بوليصة مندوب المتجر. يجب أن يحتوي رقم الطلب فقط."
                    busy={scannerBusy}
                    error={scannerError}
                    onDetected={handleBarcode}
                    onClose={() => !scannerBusy && setScannerOpen(false)}
                />
            )}
        </section>
    );
}
