import { useEffect, useMemo, useState } from "react";
import { Key, Plus, MapPin, Phone, CurrencyCircleDollar, Truck, PencilSimple, ShieldCheck } from "@phosphor-icons/react";
import { toast } from "sonner";

import {
  createStoreDriver,
  createStoreDriverAccount,
  disableStoreDriverAccount,
  listStoreDrivers,
  resetStoreDriverPassword,
  updateStoreDriver,
} from "../services/storeDelivery";

const EMPTY = { name: "", phone: "", city: "", region: "", district: "", street: "", delivery_fee: "20", status: "active", notes: "" };

function DriverForm({ initial, onClose, onSaved }) {
  const [form, setForm] = useState(initial ? {
    name: initial.name || "", phone: initial.phone || "", city: initial.city || "", region: initial.region || "",
    district: initial.district || "", street: initial.street || "", delivery_fee: String(initial.delivery_fee ?? 20),
    status: initial.status || "active", notes: initial.notes || "",
  } : EMPTY);
  const [busy, setBusy] = useState(false);
  const set = (key, value) => setForm((current) => ({ ...current, [key]: value }));
  async function submit(event) {
    event.preventDefault();
    if (!form.name.trim() || !form.phone.trim() || !form.city.trim()) return toast.error("الاسم والجوال والمدينة مطلوبة");
    setBusy(true);
    try {
      const payload = { ...form, delivery_fee: Number(form.delivery_fee || 0), coverage_mode: "city" };
      if (initial) {
        delete payload.coverage_mode;
        payload.expected_version = initial.version;
        await updateStoreDriver(initial.id, payload);
      } else await createStoreDriver(payload);
      toast.success(initial ? "تم تحديث الموصل" : "تم إضافة الموصل");
      onSaved();
    } catch (error) {
      const code = error?.response?.data?.detail?.code || error?.response?.data?.detail || "store_driver_save_failed";
      toast.error(code === "store_driver_phone_exists" ? "رقم الجوال مستخدم لموصل آخر" : String(code));
    } finally { setBusy(false); }
  }
  const input = "mt-1 h-11 w-full rounded-xl border border-slate-300 px-3 text-sm outline-none focus:border-emerald-500";
  return (
    <div className="fixed inset-0 z-[180] flex items-end justify-center bg-slate-950/60 sm:items-center sm:p-4" dir="rtl">
      <form onSubmit={submit} className="max-h-[92vh] w-full max-w-2xl overflow-y-auto rounded-t-3xl bg-white p-5 shadow-2xl sm:rounded-3xl">
        <div className="mb-4 flex items-center justify-between"><h2 className="text-lg font-black">{initial ? "تعديل الموصل" : "إضافة موصل"}</h2><button type="button" onClick={onClose} className="rounded-xl border px-3 py-2 text-sm font-bold">إغلاق</button></div>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="text-xs font-bold">اسم الموصل *<input value={form.name} onChange={(e) => set("name", e.target.value)} className={input} /></label>
          <label className="text-xs font-bold">رقم الجوال *<input value={form.phone} onChange={(e) => set("phone", e.target.value)} className={input} dir="ltr" /></label>
          <label className="text-xs font-bold">المدينة *<input value={form.city} onChange={(e) => set("city", e.target.value)} className={input} /></label>
          <label className="text-xs font-bold">المنطقة — اختياري<input value={form.region} onChange={(e) => set("region", e.target.value)} className={input} /></label>
          <label className="text-xs font-bold">الحي — اختياري<input value={form.district} onChange={(e) => set("district", e.target.value)} className={input} /></label>
          <label className="text-xs font-bold">الشارع — اختياري<input value={form.street} onChange={(e) => set("street", e.target.value)} className={input} /></label>
          <label className="text-xs font-bold">قيمة التوصيلة (ر.س) *<input type="number" min="0" step="0.01" value={form.delivery_fee} onChange={(e) => set("delivery_fee", e.target.value)} className={input} dir="ltr" /></label>
          <label className="text-xs font-bold">الحالة<select value={form.status} onChange={(e) => set("status", e.target.value)} className={input}><option value="active">نشط</option><option value="inactive">موقوف</option></select></label>
        </div>
        <label className="mt-4 block text-xs font-bold">ملاحظات<textarea rows={3} value={form.notes} onChange={(e) => set("notes", e.target.value)} className="mt-1 w-full rounded-xl border border-slate-300 p-3" /></label>
        <div className="mt-4 rounded-2xl border border-sky-200 bg-sky-50 p-3 text-xs font-bold text-sky-900">التحقق في النسخة الحالية يعتمد على المدينة فقط. المنطقة والحي والشارع محفوظة من الآن للتوسع مستقبلًا.</div>
        <button disabled={busy} className="mt-5 w-full rounded-xl bg-emerald-700 px-5 py-3 font-black text-white disabled:opacity-50">{busy ? "جارٍ الحفظ…" : "حفظ"}</button>
      </form>
    </div>
  );
}

function DriverAccountModal({ driver, onClose, onSaved }) {
  const linked = Boolean(driver.account_user_id);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event) {
    event.preventDefault();
    if (password.length < 12) return toast.error("كلمة المرور يجب أن تكون 12 حرفًا على الأقل");
    if (!linked && !email.trim()) return toast.error("البريد مطلوب");
    setBusy(true);
    try {
      if (linked) {
        await resetStoreDriverPassword(driver.id, password);
        toast.success("تم تغيير كلمة مرور الموصل");
      } else {
        await createStoreDriverAccount(driver.id, { email: email.trim(), password });
        toast.success("تم إنشاء حساب تطبيق توصيل أماسي");
      }
      onSaved();
    } catch (error) {
      const code = error?.response?.data?.detail?.code || error?.response?.data?.detail || "store_driver_account_failed";
      toast.error(String(code));
    } finally { setBusy(false); }
  }
  async function disable() {
    setBusy(true);
    try {
      await disableStoreDriverAccount(driver.id);
      toast.success("تم إيقاف وفصل حساب الموصل");
      onSaved();
    } catch (error) {
      toast.error(error?.response?.data?.detail?.code || "تعذر إيقاف الحساب");
    } finally { setBusy(false); }
  }
  return (
    <div className="fixed inset-0 z-[190] flex items-end justify-center bg-slate-950/60 sm:items-center sm:p-4" dir="rtl">
      <form onSubmit={submit} className="w-full max-w-lg rounded-t-3xl bg-white p-5 shadow-2xl sm:rounded-3xl">
        <div className="flex items-center justify-between"><div><h2 className="text-lg font-black">حساب توصيل أماسي</h2><p className="mt-1 text-xs font-bold text-slate-500">{driver.name}</p></div><button type="button" onClick={onClose} className="rounded-xl border px-3 py-2 text-xs font-black">إغلاق</button></div>
        <div className="mt-4 rounded-2xl border border-emerald-200 bg-emerald-50 p-3 text-xs font-bold leading-6 text-emerald-950"><ShieldCheck className="ml-1 inline" />هذا الحساب دوره ثابت <span dir="ltr">store_driver</span> وبدون أي صلاحيات لوحة ميزان. وصوله محصور بواجهات تطبيق التوصيل.</div>
        {!linked && <label className="mt-4 block text-xs font-black text-slate-600">بريد تسجيل الدخول<input type="email" dir="ltr" value={email} onChange={(e) => setEmail(e.target.value)} className="mt-1 h-11 w-full rounded-xl border px-3" required /></label>}
        <label className="mt-4 block text-xs font-black text-slate-600">{linked ? "كلمة المرور الجديدة" : "كلمة المرور"}<input type="password" dir="ltr" value={password} onChange={(e) => setPassword(e.target.value)} minLength={12} className="mt-1 h-11 w-full rounded-xl border px-3" required /></label>
        <button disabled={busy} className="mt-5 w-full rounded-xl bg-slate-950 px-4 py-3 font-black text-white disabled:opacity-50">{busy ? "جارٍ الحفظ…" : linked ? "تغيير كلمة المرور" : "إنشاء حساب الموصل"}</button>
        {linked && <button type="button" onClick={disable} disabled={busy} className="mt-2 w-full rounded-xl border border-rose-300 bg-rose-50 px-4 py-3 font-black text-rose-800 disabled:opacity-50">إيقاف وفصل الحساب</button>}
      </form>
    </div>
  );
}

export default function StoreDrivers() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(null);
  const [formOpen, setFormOpen] = useState(false);
  const [accountDriver, setAccountDriver] = useState(null);
  async function load() {
    setLoading(true);
    try { const data = await listStoreDrivers(); setItems(data.items || []); }
    catch { toast.error("تعذر تحميل موصلي المتجر"); }
    finally { setLoading(false); }
  }
  useEffect(() => { load(); }, []);
  const activeCount = useMemo(() => items.filter((row) => row.status === "active").length, [items]);
  const linkedCount = useMemo(() => items.filter((row) => row.account_user_id).length, [items]);

  return (
    <main className="p-4 sm:p-6" dir="rtl">
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div><h1 className="text-2xl font-black text-slate-950">موصلو المتجر</h1><p className="mt-1 text-sm font-bold text-slate-500">إدارة موصلي أماسي ونطاق المدينة وسعر التوصيلة وحساب تطبيق التوصيل.</p></div>
        <button onClick={() => { setEditing(null); setFormOpen(true); }} className="inline-flex items-center justify-center gap-2 rounded-xl bg-emerald-700 px-4 py-3 text-sm font-black text-white"><Plus size={20} />إضافة موصل</button>
      </div>
      <div className="mb-5 grid gap-3 sm:grid-cols-4">
        <div className="rounded-2xl border bg-white p-4"><div className="text-xs font-bold text-slate-500">إجمالي الموصلين</div><div className="mt-1 text-3xl font-black" dir="ltr">{items.length}</div></div>
        <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4"><div className="text-xs font-bold text-emerald-700">النشطون</div><div className="mt-1 text-3xl font-black text-emerald-950" dir="ltr">{activeCount}</div></div>
        <div className="rounded-2xl border border-violet-200 bg-violet-50 p-4"><div className="text-xs font-bold text-violet-700">حسابات التطبيق</div><div className="mt-1 text-3xl font-black text-violet-950" dir="ltr">{linkedCount}</div></div>
        <div className="rounded-2xl border border-sky-200 bg-sky-50 p-4"><div className="text-xs font-bold text-sky-700">نطاق V1</div><div className="mt-1 text-lg font-black text-sky-950">مطابقة المدينة</div></div>
      </div>
      {loading ? <div className="rounded-2xl border bg-white p-8 text-center font-bold">جارٍ التحميل…</div> : (
        <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
          {items.map((driver) => (
            <article key={driver.id} className="rounded-2xl border bg-white p-5 shadow-sm">
              <div className="flex items-start justify-between gap-3"><div><div className="flex items-center gap-2 text-lg font-black"><Truck size={22} weight="duotone" />{driver.name}</div><div className="mt-2 flex flex-wrap gap-2"><span className={`inline-flex rounded-full px-3 py-1 text-[11px] font-black ${driver.status === "active" ? "bg-emerald-100 text-emerald-800" : "bg-slate-200 text-slate-700"}`}>{driver.status === "active" ? "نشط" : "موقوف"}</span><span className={`inline-flex rounded-full px-3 py-1 text-[11px] font-black ${driver.account_user_id ? "bg-violet-100 text-violet-800" : "bg-amber-100 text-amber-800"}`}>{driver.account_user_id ? "حساب التطبيق مرتبط" : "بدون حساب تطبيق"}</span></div></div><button onClick={() => { setEditing(driver); setFormOpen(true); }} className="rounded-xl border p-2"><PencilSimple size={18} /></button></div>
              <div className="mt-4 space-y-2 text-sm font-bold text-slate-700"><div className="flex items-center gap-2"><Phone size={18} /> <span dir="ltr">{driver.phone}</span></div><div className="flex items-center gap-2"><MapPin size={18} />{driver.city}{driver.district ? ` — ${driver.district}` : ""}</div><div className="flex items-center gap-2"><CurrencyCircleDollar size={18} />{Number(driver.delivery_fee || 0).toFixed(2)} ر.س / توصيلة</div></div>
              <button onClick={() => setAccountDriver(driver)} className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl border border-violet-200 bg-violet-50 px-3 py-2.5 text-xs font-black text-violet-900"><Key size={18} />{driver.account_user_id ? "إدارة حساب توصيل أماسي" : "إنشاء حساب توصيل أماسي"}</button>
            </article>
          ))}
          {!items.length && <div className="rounded-2xl border border-dashed bg-white p-10 text-center text-sm font-bold text-slate-500 lg:col-span-2 xl:col-span-3">لا يوجد موصلون حتى الآن.</div>}
        </div>
      )}
      {formOpen && <DriverForm initial={editing} onClose={() => setFormOpen(false)} onSaved={() => { setFormOpen(false); load(); }} />}
      {accountDriver && <DriverAccountModal driver={accountDriver} onClose={() => setAccountDriver(null)} onSaved={() => { setAccountDriver(null); load(); }} />}
    </main>
  );
}
