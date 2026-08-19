import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCircle, Package, Truck, WarningCircle } from "@phosphor-icons/react";
import { toast } from "sonner";

import BarcodeCameraScanner from "../components/BarcodeCameraScanner";
import {
  confirmDriverHandoverSession,
  createDriverHandoverSession,
  listStoreDrivers,
  scanDriverHandoverShipment,
} from "../services/storeDelivery";

function money(value) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "SAR", maximumFractionDigits: 2 }).format(Number(value || 0));
}

export default function StoreDriverHandover() {
  const [drivers, setDrivers] = useState([]);
  const [driverId, setDriverId] = useState("");
  const [session, setSession] = useState(null);
  const [barcode, setBarcode] = useState("");
  const [accepted, setAccepted] = useState([]);
  const [rejected, setRejected] = useState([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    listStoreDrivers({ status: "active" })
      .then((data) => setDrivers(data.items || []))
      .catch(() => toast.error("تعذر تحميل الموصلين"));
  }, []);

  const driver = useMemo(() => drivers.find((row) => row.id === driverId), [drivers, driverId]);

  async function begin() {
    if (!driverId) return toast.error("اختر الموصل أولًا");
    setBusy(true);
    try {
      const data = await createDriverHandoverSession(driverId);
      setSession(data);
      setAccepted([]);
      setRejected([]);
      toast.success("بدأت جلسة تسليم الشحنات");
    } catch (error) {
      toast.error(error?.response?.data?.detail?.code || "تعذر بدء الجلسة");
    } finally {
      setBusy(false);
    }
  }

  const scan = useCallback(async (value) => {
    const clean = String(value || "").trim();
    if (!clean || !session?.id || busy) return;
    setBusy(true);
    try {
      const result = await scanDriverHandoverShipment(session.id, clean);
      if (result.accepted) {
        setAccepted((current) => current.some((row) => row.order_id === result.shipment.order_id) ? current : [...current, result.shipment]);
        toast.success(`تم قبول #${result.shipment.order_number || result.shipment.order_id}`);
      } else {
        setRejected((current) => [...current, result]);
        toast.error(result.code === "driver_city_mismatch" ? "مدينة الشحنة لا تطابق مدينة الموصل" : result.code);
      }
    } catch (error) {
      toast.error(error?.response?.data?.detail?.code || "تعذر قراءة الشحنة");
    } finally {
      setBusy(false);
      setBarcode("");
    }
  }, [session?.id, busy]);

  async function manualScan(event) {
    event.preventDefault();
    await scan(barcode);
  }

  async function confirm() {
    if (!session?.id || !accepted.length) return;
    setBusy(true);
    try {
      const result = await confirmDriverHandoverSession(session.id);
      toast.success(`تم تسليم ${result.assigned_count} شحنة للموصل`);
      setSession(null);
      setDriverId("");
      setAccepted([]);
      setRejected([]);
    } catch (error) {
      toast.error(error?.response?.data?.detail?.code || "تعذر تأكيد التسليم");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="space-y-5 p-4 sm:p-6" dir="rtl">
      <header>
        <h1 className="text-2xl font-black text-slate-950">تسليم الشحنات للموصلين</h1>
        <p className="mt-1 text-sm font-bold text-slate-500">اختر موصلًا واحدًا ثم امسح جميع الشحنات التي سيستلمها. النظام يرفض أي شحنة خارج مدينته.</p>
      </header>

      {!session && (
        <section className="max-w-2xl rounded-3xl border bg-white p-5 shadow-sm">
          <label className="text-sm font-black text-slate-700">الموصل<select value={driverId} onChange={(e) => setDriverId(e.target.value)} className="mt-2 h-12 w-full rounded-xl border px-3 font-bold"><option value="">اختر الموصل</option>{drivers.map((row) => <option key={row.id} value={row.id}>{row.name} — {row.city} — {money(row.delivery_fee)}</option>)}</select></label>
          {driver && <div className="mt-4 rounded-2xl bg-slate-50 p-4 text-sm font-bold"><Truck className="ml-1 inline" />{driver.name} · المدينة {driver.city} · سعر التوصيلة الحالي {money(driver.delivery_fee)}</div>}
          <button disabled={busy || !driverId} onClick={begin} className="mt-4 w-full rounded-2xl bg-emerald-700 px-4 py-4 font-black text-white disabled:opacity-40">بدء جلسة التسليم</button>
        </section>
      )}

      {session && (
        <>
          <section className="rounded-3xl border border-emerald-200 bg-emerald-50 p-5">
            <div className="flex flex-wrap items-center justify-between gap-3"><div><div className="text-xs font-black text-emerald-700">الجلسة الحالية</div><div className="text-xl font-black text-emerald-950">{session.driver_name_snapshot}</div><div className="mt-1 text-sm font-bold text-emerald-800">المدينة: {session.driver_city_snapshot} · السعر المثبت لهذه الجلسة: {money(session.delivery_fee_snapshot)}</div></div><Truck size={38} className="text-emerald-700" /></div>
          </section>

          <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
            <section className="space-y-4">
              <BarcodeCameraScanner active={!busy} onDetected={scan} label="تشغيل كاميرا الباركود" />
              <form onSubmit={manualScan} className="rounded-2xl border bg-white p-4"><label className="text-xs font-black text-slate-600">إدخال الباركود يدويًا<input value={barcode} onChange={(e) => setBarcode(e.target.value)} className="mt-2 h-12 w-full rounded-xl border px-3 text-lg font-black" autoFocus /></label><button disabled={busy || !barcode.trim()} className="mt-3 w-full rounded-xl bg-slate-950 px-4 py-3 font-black text-white disabled:opacity-40">إضافة الشحنة</button></form>
            </section>

            <section className="space-y-4">
              <div className="grid grid-cols-2 gap-3"><div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4"><div className="text-xs font-black text-emerald-700">مقبولة</div><div className="mt-1 text-3xl font-black text-emerald-950" dir="ltr">{accepted.length}</div></div><div className="rounded-2xl border border-rose-200 bg-rose-50 p-4"><div className="text-xs font-black text-rose-700">مرفوضة</div><div className="mt-1 text-3xl font-black text-rose-950" dir="ltr">{rejected.length}</div></div></div>
              <div className="max-h-[420px] space-y-2 overflow-y-auto">
                {accepted.map((row) => <div key={row.order_id} className="rounded-2xl border border-emerald-200 bg-white p-3"><div className="flex items-center justify-between"><div className="font-black">#{row.order_number || row.order_id}</div><CheckCircle className="text-emerald-600" size={22} weight="fill" /></div><div className="mt-1 text-xs font-bold text-slate-500">{row.shipping_city} · {row.barcode}</div></div>)}
                {rejected.slice().reverse().map((row, index) => <div key={`${row.barcode}-${index}`} className="rounded-2xl border border-rose-200 bg-rose-50 p-3"><div className="flex items-center justify-between"><div className="font-black text-rose-950">{row.order_number ? `#${row.order_number}` : row.barcode}</div><WarningCircle className="text-rose-600" size={22} weight="fill" /></div><div className="mt-1 text-xs font-bold text-rose-800">{row.code}{row.shipping_city ? ` · ${row.shipping_city}` : ""}</div></div>)}
              </div>
            </section>
          </div>

          <section className="rounded-3xl border bg-white p-5 shadow-sm">
            <div className="grid gap-3 sm:grid-cols-3"><div><div className="text-xs font-bold text-slate-500">الموصل</div><div className="font-black">{session.driver_name_snapshot}</div></div><div><div className="text-xs font-bold text-slate-500">الشحنات المقبولة</div><div className="font-black" dir="ltr">{accepted.length}</div></div><div><div className="text-xs font-bold text-slate-500">إجمالي الأجور المتوقع</div><div className="font-black" dir="ltr">{money(accepted.length * Number(session.delivery_fee_snapshot || 0))}</div></div></div>
            <button disabled={busy || !accepted.length} onClick={confirm} className="mt-5 flex w-full items-center justify-center gap-2 rounded-2xl bg-emerald-700 px-4 py-4 font-black text-white disabled:opacity-40"><Package size={22} />تأكيد وتسليم جميع الشحنات</button>
          </section>
        </>
      )}
    </main>
  );
}
