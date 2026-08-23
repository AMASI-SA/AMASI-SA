import { useEffect, useMemo, useState } from "react";
import { FileText, ShieldCheck, X } from "@phosphor-icons/react";
import { toast } from "sonner";
import {
    getAccountingPermissionsCatalogue,
    getAccountingPermissionUsers,
    updateAccountingPermissionUser,
} from "../../services/accountingModule";
import { LoadingBlock } from "./AccountingShared";

function PermissionGroup({ title, rows, selected, onToggle, Icon }) {
    return (
        <section className="rounded-2xl border border-slate-200 p-4">
            <div className="flex items-center gap-2"><Icon size={20} weight="duotone" className="text-emerald-800" /><h3 className="text-sm font-black text-slate-950">{title}</h3></div>
            <div className="mt-3 grid gap-2 md:grid-cols-2">
                {rows.map((row) => (
                    <label key={row.permission} className={`flex cursor-pointer items-center gap-3 rounded-xl border p-3 ${selected.has(row.permission) ? "border-emerald-300 bg-emerald-50" : "border-slate-200 bg-slate-50"}`}>
                        <input type="checkbox" checked={selected.has(row.permission)} onChange={() => onToggle(row.permission)} className="h-4 w-4 accent-emerald-700" />
                        <div><div className="text-xs font-extrabold text-slate-900">{row.label}</div><div className="mt-1 font-mono text-[10px] text-slate-400" dir="ltr">{row.permission}</div></div>
                    </label>
                ))}
            </div>
        </section>
    );
}

export default function AccountingPermissionsDialog({ open, onClose }) {
    const [catalogue, setCatalogue] = useState(null);
    const [users, setUsers] = useState([]);
    const [selectedUserId, setSelectedUserId] = useState("");
    const [selected, setSelected] = useState(new Set());
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const selectedUser = useMemo(
        () => users.find((row) => row.id === selectedUserId) || null,
        [users, selectedUserId],
    );

    useEffect(() => {
        if (!open) return;
        setLoading(true);
        Promise.all([getAccountingPermissionsCatalogue(), getAccountingPermissionUsers()])
            .then(([catalogueResult, usersResult]) => {
                setCatalogue(catalogueResult);
                const rows = (usersResult.users || []).filter((row) => !row.is_owner);
                setUsers(rows);
                setSelectedUserId(rows[0]?.id || "");
                setSelected(new Set(rows[0]?.accounting_permissions || []));
            })
            .catch(() => toast.error("تعذر تحميل صلاحيات المحاسبة"))
            .finally(() => setLoading(false));
    }, [open]);

    useEffect(() => {
        if (selectedUser) setSelected(new Set(selectedUser.accounting_permissions || []));
    }, [selectedUser]);

    if (!open) return null;

    const toggle = (permission) => setSelected((current) => {
        const next = new Set(current);
        if (next.has(permission)) next.delete(permission);
        else next.add(permission);
        return next;
    });

    const save = async () => {
        if (!selectedUserId) return;
        setSaving(true);
        try {
            const result = await updateAccountingPermissionUser(selectedUserId, Array.from(selected).sort());
            setUsers((current) => current.map((row) => row.id === result.user.id ? result.user : row));
            toast.success("تم حفظ صلاحيات المحاسبة.");
        } catch (error) {
            const detail = error?.response?.data?.detail;
            toast.error(typeof detail === "string" ? detail : detail?.message || "تعذر حفظ الصلاحيات");
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-950/60 p-4" role="dialog" aria-modal="true" aria-label="صلاحيات المحاسبة">
            <div className="max-h-[92vh] w-full max-w-5xl overflow-y-auto rounded-2xl bg-white shadow-2xl" dir="rtl">
                <header className="sticky top-0 z-10 flex items-start justify-between gap-3 border-b border-slate-200 bg-white p-5">
                    <div><h2 className="text-xl font-black text-slate-950">صلاحيات المحاسبة</h2><p className="mt-1 text-xs font-semibold text-slate-500">كل صفحة وكل إجراء حساس مستقل، ولا يرث الموظف صلاحية من دوره.</p></div>
                    <button type="button" onClick={onClose} className="rounded-xl border border-slate-200 p-2 text-slate-600"><X size={20} /></button>
                </header>
                <div className="p-5">
                    {loading ? <LoadingBlock label="جاري تحميل المستخدمين والصلاحيات…" /> : users.length === 0 ? (
                        <div className="rounded-xl border border-slate-200 bg-slate-50 p-6 text-center text-sm font-bold text-slate-500">لا يوجد مستخدمون غير المالك.</div>
                    ) : (
                        <div className="space-y-5">
                            <label className="block text-xs font-extrabold text-slate-700">المستخدم
                                <select value={selectedUserId} onChange={(event) => setSelectedUserId(event.target.value)} className="mt-2 h-11 w-full rounded-xl border border-slate-300 bg-white px-3 text-sm font-bold">
                                    {users.map((row) => <option key={row.id} value={row.id}>{row.name} — {row.role}</option>)}
                                </select>
                            </label>
                            <PermissionGroup title="صلاحيات الصفحات" rows={catalogue?.pages || []} selected={selected} onToggle={toggle} Icon={FileText} />
                            <PermissionGroup title="الإجراءات الحساسة" rows={catalogue?.actions || []} selected={selected} onToggle={toggle} Icon={ShieldCheck} />
                        </div>
                    )}
                </div>
                <footer className="sticky bottom-0 flex justify-end gap-2 border-t border-slate-200 bg-white p-4">
                    <button type="button" onClick={onClose} className="rounded-xl border border-slate-300 px-4 py-2 text-sm font-extrabold text-slate-700">إلغاء</button>
                    <button type="button" onClick={save} disabled={saving || !selectedUserId} className="rounded-xl bg-emerald-800 px-5 py-2 text-sm font-extrabold text-white disabled:opacity-50">{saving ? "جاري الحفظ…" : "حفظ الصلاحيات"}</button>
                </footer>
            </div>
        </div>
    );
}
