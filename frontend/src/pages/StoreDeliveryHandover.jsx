import { useMemo, useState } from "react";
import { Barcode, CheckCircle, Package, UserCircle, WarningCircle } from "@phosphor-icons/react";
import { toast } from "sonner";

import {
  confirmStoreDeliveryHandover,
  createStoreDeliveryHandover,
  getStoreDrivers,
  scanStoreDeliveryShipment,
} from "../services/storeDelivery";

export default function StoreDeliveryHandover() {
  const [drivers, setDrivers] = useState([]);
  const [driverId, setDriverId] = useState("");
  const [session, setSession] = useState(null);
  const [barcode, setBarcode] = useState("");
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);

  const selectedDriver = useMemo(
    () => drivers.find((item) => item.id === driverId) || null,
    [drivers, driverId],
  );

  async function loadDrivers() {
    try {
      const data = await getStoreDrivers({ status: "active" });
      setDrivers(data.items || []);
    } catch (error) {
      toast.error("تعذر تحميل الموصلين");
    }
  }

  async function startSession() {
    if (!driverId) return toast.error("اختر الموصل أولًا");
    setBusy(true);
    try {
      const data = await createStoreDeliveryHandover({ driver_id: driverId });
      setSession(data);
      setRows([]);
      toast.success("بدأت جلسة تسليم الشحنات");
    } catch (error) {
      toast.error(error?.response?.data?.detail?.code || "تعذر بدء الجلسة");
    } finally {
      setBusy(false);
    }
  }

  async function scan(event) {
    event?.preventDefault();
    const value = barcode.trim();
    if (!session?.id || !value) return;
    setBusy(true);
    try {
      const data = await scanStoreDeliveryShipment(session.id, { barcode: value });
      setRows((current) => [data, ...current.filter((row) => row.order_id !== data.order_id)]);
      setBarcode("");
      toast.success(`تمت إضافة الطلب ${data.order_number || data.order_id}`);
    } catch (error) {
      const code = error?.response?.data?.detail?.code;
      if (code === "driver_city_mismatch") toast.error("مدينة الشحنة لا تطابق مدينة الموصل");
      else toast.error(code || "تعذر إضافة الشحنة");
      setBarcode("");
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!session?.id || rows.length === 0) return;
    setBusy(true);
    try {
      const data = await confirmStoreDeliveryHandover(session.id);
      toast.success(`تم إسناد ${data.assigned_count || rows.length} شحنة للموصل`);
      setSession(null);
      setRows([]);
      setDriverId("");
    } catch (error) {
      toast.error(error?.response?.data?.detail?.code || "تعذر تأكيد التسليم");
    } finally {
      setBusy(false);
    }
  }

  if (drivers.length === 0 && !busy) {
    queueMicrotask(loadDrivers);
  }

  return (
    <main className="mx-auto max-w-5xl space-y-5 p-4" dir="rtl">
      <header>
        <h1 className="text-2xl font-black text-slate-950">تسليم الشحنات للموصلين</h1>
        <p className="mt-1 text-sm font-bold text-slate-500">اختر الموصل ثم امسح الشحنات التي سيستلمها. يتم التحقق من المدينة قبل الإسناد.</p>
      </header>

      {!session ? (
        <section className="rounded-3xl border bg-white p-5 shadow-sm">
          <label className="text-sm font-black text-slate-700">الموصل</label>
          <select value={driverId} onChange={(event) => setDriverId(event.target.value)} className="mt-2 h-12 w-full rounded-2xl border px-3 font-bold">
            <option value="">اختر الموصل</option>
            {drivers.map((driver) => (
              <option key={driver.id} value={driver.id}>{driver.name} — {driver.city} — {driver.delivery_fee} ريال</option>
            ))}
          </select>
          {selectedDriver && (
            <div className="mt-4 rounded-2xl bg-slate-50 p-4 text-sm font-bold">
              <div className="flex items-center gap-2"><UserCircle size={22} />{selectedDriver.name}</div>
              <div className="mt-2">المدينة: {selectedDriver.city}</div>
              <div>سعر التوصيلة الحالي: {selectedDriver.delivery_fee} ريال</div>
            </div>
          )}
          <button type="button" disabled={busy || !driverId} onClick={startSession} className="mt-4 w-full rounded-2xl bg-emerald-700 px-5 py-3 font-black text-white disabled:opacity-40">بدء جلسة التسليم</button>
        </section>
      ) : (
        <>
          <section className="rounded-3xl border border-emerald-200 bg-emerald-50 p-5">
            <div className="flex items-center gap-2 font-black text-emerald-950"><UserCircle size={22} />{selectedDriver?.name || session.driver_name_snapshot}</div>
            <div className="mt-1 text-sm font-bold text-emerald-900">المدينة: {selectedDriver?.city || session.driver_city_snapshot} · السعر المحفوظ للجلسة: {session.delivery_fee_snapshot ?? selectedDriver?.delivery_fee} ريال</div>
          </section>

          <form onSubmit={scan} className="rounded-3xl border bg-white p-5 shadow-sm">
            <label className="flex items-center gap-2 text-sm font-black"><Barcode size={24} />مسح باركود الشحنة</label>
            <input autoFocus value={barcode} onChange={(event) => setBarcode(event.target.value)} placeholder="امسح الباركود أو أدخل رقم الطلب" className="mt-3 h-14 w-full rounded-2xl border px-4 text-lg font-black" />
            <button disabled={busy || !barcode.trim()} className="mt-3 w-full rounded-2xl bg-slate-950 px-5 py-3 font-black text-white disabled:opacity-40">إضافة الشحنة</button>
          </form>

          <section className="rounded-3xl border bg-white p-5 shadow-sm">
            <div className="flex items-center justify-between">
              <h2 className="font-black">الشحنات المقبولة</h2>
              <span className="rounded-full bg-emerald-100 px-3 py-1 text-sm font-black text-emerald-800">{rows.length}</span>
            </div>
            <div className="mt-4 space-y-2">
              {rows.length === 0 && <div className="rounded-2xl bg-slate-50 p-5 text-center text-sm font-bold text-slate-500"><Package className="mx-auto mb-2" size={28} />ابدأ بمسح الشحنات</div>}
              {rows.map((row) => (
                <div key={row.order_id} className="flex items-center justify-between rounded-2xl border p-3">
                  <div><div className="font-black">#{row.order_number || row.order_id}</div><div className="text-xs font-bold text-slate-500">{row.shipping_city_snapshot || row.shipping_city}</div></div>
                  {row.accepted === false ? <WarningCircle size={22} className="text-rose-600" /> : <CheckCircle size={22} weight="fill" className="text-emerald-600" />}
                </div>
              ))}
            </div>
          </section>

          <button type="button" onClick={confirm} disabled={busy || rows.length === 0} className="w-full rounded-2xl bg-emerald-700 px-5 py-4 text-base font-black text-white disabled:opacity-40">تأكيد تسليم {rows.length} شحنة للموصل</button>
        </>
      )}
    </main>
  );
}
