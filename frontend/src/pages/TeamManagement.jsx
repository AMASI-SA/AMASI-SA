import { useCallback, useEffect, useState } from "react";
import { Plus, PencilSimple, Trash, UsersThree, ShieldCheck, X, Warning } from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";
import { useAuth } from "../context/AuthContext";

const ROLE_LABELS = {
    owner: "Owner — مالك",
    admin: "Admin — مدير",
    accountant: "محاسب",
    operations: "عمليات",
    viewer: "مشاهد فقط",
};

const ROLE_COLORS = {
    owner: "bg-amber-100 text-amber-800 border-amber-200",
    admin: "bg-violet-100 text-violet-800 border-violet-200",
    accountant: "bg-emerald-100 text-emerald-800 border-emerald-200",
    operations: "bg-sky-100 text-sky-800 border-sky-200",
    viewer: "bg-slate-100 text-slate-700 border-slate-200",
};

const inputCls =
    "w-full px-3 py-2 text-sm border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand focus:border-brand transition";

function RoleBadge({ role }) {
    const cls = ROLE_COLORS[role] || ROLE_COLORS.viewer;
    return (
        <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold border ${cls}`} data-testid={`team-role-badge-${role}`}>
            {ROLE_LABELS[role] || role}
        </span>
    );
}

function UserFormModal({ mode, initial, catalogue, onClose, onSaved }) {
    const isEdit = mode === "edit";
    const [name, setName] = useState(initial?.name || "");
    const [email, setEmail] = useState(initial?.email || "");
    const [password, setPassword] = useState("");
    const [role, setRole] = useState(initial?.role || "viewer");
    const [extra, setExtra] = useState(new Set(initial?.extra_permissions || []));
    const [denied, setDenied] = useState(new Set(initial?.denied_permissions || []));
    const [busy, setBusy] = useState(false);

    const togglePerm = (key, list, setter) => {
        const next = new Set(list);
        next.has(key) ? next.delete(key) : next.add(key);
        setter(next);
    };

    const submit = async (e) => {
        e.preventDefault();
        if (!name.trim()) return toast.error("الاسم مطلوب");
        if (!isEdit && !email.trim()) return toast.error("البريد الإلكتروني مطلوب");
        if (!isEdit && password.length < 6) return toast.error("كلمة المرور يجب أن تكون 6 أحرف على الأقل");
        setBusy(true);
        try {
            if (isEdit) {
                const payload = {
                    name: name.trim(),
                    role,
                    extra_permissions: Array.from(extra),
                    denied_permissions: Array.from(denied),
                };
                if (password) payload.new_password = password;
                const { data } = await api.put(`/team/users/${initial.id}`, payload);
                toast.success("تم تحديث المستخدم");
                onSaved(data);
            } else {
                const { data } = await api.post("/team/users", {
                    name: name.trim(),
                    email: email.trim(),
                    password,
                    role,
                    extra_permissions: Array.from(extra),
                    denied_permissions: Array.from(denied),
                });
                toast.success("تم إنشاء المستخدم");
                onSaved(data);
            }
            onClose();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally {
            setBusy(false);
        }
    };

    const roleDefaults = catalogue?.role_defaults?.[role] || [];
    const permList = catalogue?.permissions || [];

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" data-testid="team-user-modal">
            <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
                <header className="flex items-center justify-between border-b border-border px-5 py-3">
                    <h2 className="font-bold text-lg" data-testid="team-modal-title">
                        {isEdit ? "تعديل مستخدم" : "إضافة مستخدم جديد"}
                    </h2>
                    <button onClick={onClose} className="p-1.5 rounded hover:bg-accent" data-testid="team-modal-close-btn">
                        <X size={20} />
                    </button>
                </header>
                <form onSubmit={submit} className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">الاسم</label>
                            <input value={name} onChange={(e) => setName(e.target.value)} className={inputCls} maxLength={80} data-testid="team-user-name-input" />
                        </div>
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">البريد الإلكتروني</label>
                            <input
                                type="email"
                                value={email}
                                onChange={(e) => setEmail(e.target.value)}
                                disabled={isEdit}
                                className={`${inputCls} ${isEdit ? "bg-slate-50 text-muted-foreground" : ""}`}
                                dir="ltr"
                                style={{ textAlign: "right" }}
                                data-testid="team-user-email-input"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">
                                {isEdit ? "كلمة مرور جديدة (اختياري)" : "كلمة المرور"}
                            </label>
                            <input
                                type="text"
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                className={inputCls}
                                placeholder={isEdit ? "اترك فارغاً للاحتفاظ بالحالية" : "6 أحرف على الأقل"}
                                data-testid="team-user-password-input"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">الدور</label>
                            <select value={role} onChange={(e) => setRole(e.target.value)} className={inputCls} data-testid="team-user-role-select">
                                <option value="admin">{ROLE_LABELS.admin}</option>
                                <option value="accountant">{ROLE_LABELS.accountant}</option>
                                <option value="operations">{ROLE_LABELS.operations}</option>
                                <option value="viewer">{ROLE_LABELS.viewer}</option>
                            </select>
                        </div>
                    </div>

                    <div className="border border-border rounded-lg p-3 bg-slate-50">
                        <div className="text-sm font-bold mb-2 flex items-center gap-1.5">
                            <ShieldCheck size={16} className="text-brand" />
                            الصلاحيات
                            <span className="font-normal text-muted-foreground text-xs">(الافتراضية للدور مفعّلة تلقائياً)</span>
                        </div>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 max-h-64 overflow-y-auto pr-1">
                            {permList.map((p) => {
                                const isDefault = roleDefaults.includes(p.key);
                                const isExtra = extra.has(p.key);
                                const isDenied = denied.has(p.key);
                                const effective = (isDefault && !isDenied) || isExtra;
                                return (
                                    <div key={p.key} className="flex items-center justify-between text-xs bg-white border border-border rounded px-2 py-1.5" data-testid={`team-perm-row-${p.key}`}>
                                        <div className="flex-1">
                                            <div className="font-semibold">{p.label}</div>
                                            <div className="text-[10px] text-muted-foreground" dir="ltr" style={{ textAlign: "right" }}>{p.key}</div>
                                        </div>
                                        <div className="flex items-center gap-1">
                                            {isDefault ? (
                                                <button
                                                    type="button"
                                                    onClick={() => togglePerm(p.key, denied, setDenied)}
                                                    className={`px-2 py-0.5 rounded text-[11px] font-bold ${isDenied ? "bg-rose-600 text-white" : "bg-slate-200 text-slate-700 hover:bg-slate-300"}`}
                                                    data-testid={`team-perm-deny-${p.key}`}
                                                    title="منع هذه الصلاحية من الدور الافتراضي"
                                                >
                                                    {isDenied ? "ممنوع" : "افتراضي"}
                                                </button>
                                            ) : (
                                                <button
                                                    type="button"
                                                    onClick={() => togglePerm(p.key, extra, setExtra)}
                                                    className={`px-2 py-0.5 rounded text-[11px] font-bold ${isExtra ? "bg-emerald-600 text-white" : "bg-slate-200 text-slate-700 hover:bg-slate-300"}`}
                                                    data-testid={`team-perm-add-${p.key}`}
                                                    title="إضافة صلاحية إضافية"
                                                >
                                                    {isExtra ? "مضافة" : "أضف"}
                                                </button>
                                            )}
                                            {effective ? (
                                                <span className="w-2 h-2 rounded-full bg-emerald-500" title="فعّالة" />
                                            ) : (
                                                <span className="w-2 h-2 rounded-full bg-slate-300" title="غير فعّالة" />
                                            )}
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                </form>
                <footer className="border-t border-border px-5 py-3 flex justify-end gap-2">
                    <button onClick={onClose} className="px-4 py-2 text-sm border border-border rounded-lg hover:bg-accent" data-testid="team-modal-cancel-btn">إلغاء</button>
                    <button onClick={submit} disabled={busy} className="px-4 py-2 text-sm bg-brand text-white font-semibold rounded-lg bg-brand-hover disabled:opacity-60" data-testid="team-modal-save-btn">
                        {busy ? "جاري الحفظ…" : (isEdit ? "حفظ التعديلات" : "إنشاء")}
                    </button>
                </footer>
            </div>
        </div>
    );
}

function DeleteConfirmModal({ target, onClose, onConfirm }) {
    const [busy, setBusy] = useState(false);
    const handle = async () => {
        setBusy(true);
        try {
            await onConfirm();
        } finally {
            setBusy(false);
        }
    };
    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" data-testid="team-delete-modal">
            <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6">
                <div className="flex items-center gap-3 mb-3">
                    <div className="w-10 h-10 rounded-full bg-rose-100 text-rose-600 flex items-center justify-center">
                        <Warning size={22} weight="bold" />
                    </div>
                    <h3 className="text-lg font-bold">حذف المستخدم</h3>
                </div>
                <p className="text-sm text-muted-foreground mb-4">
                    سيتم حذف <span className="font-bold text-foreground">{target.name}</span> ({target.email}) نهائياً. لا يمكن التراجع.
                </p>
                <div className="flex justify-end gap-2">
                    <button onClick={onClose} className="px-4 py-2 text-sm border border-border rounded-lg hover:bg-accent" data-testid="team-delete-cancel-btn">إلغاء</button>
                    <button onClick={handle} disabled={busy} className="px-4 py-2 text-sm bg-rose-600 text-white font-semibold rounded-lg hover:bg-rose-700 disabled:opacity-60" data-testid="team-delete-confirm-btn">
                        {busy ? "جاري الحذف…" : "حذف نهائي"}
                    </button>
                </div>
            </div>
        </div>
    );
}

export default function TeamManagement() {
    const { user } = useAuth();
    const [users, setUsers] = useState([]);
    const [catalogue, setCatalogue] = useState(null);
    const [loading, setLoading] = useState(true);
    const [modal, setModal] = useState(null); // {mode:'add'|'edit', initial}
    const [deleteTarget, setDeleteTarget] = useState(null);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const [usersRes, catRes] = await Promise.all([
                api.get("/team/users"),
                api.get("/auth/permissions/catalogue"),
            ]);
            setUsers(usersRes.data);
            setCatalogue(catRes.data);
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذر تحميل قائمة المستخدمين");
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        load();
    }, [load]);

    if (!user) return null;

    if (!user.is_owner) {
        return (
            <div className="max-w-2xl mx-auto bg-amber-50 border border-amber-200 rounded-xl p-6 text-center" data-testid="team-no-permission">
                <Warning size={42} weight="duotone" className="mx-auto text-amber-600 mb-3" />
                <h2 className="text-xl font-bold text-amber-900 mb-1">صفحة Owner فقط</h2>
                <p className="text-sm text-amber-800">إدارة المستخدمين متاحة لمالك الحساب فقط.</p>
            </div>
        );
    }

    const handleDelete = async () => {
        try {
            await api.delete(`/team/users/${deleteTarget.id}`);
            toast.success(`تم حذف ${deleteTarget.name}`);
            setUsers((arr) => arr.filter((u) => u.id !== deleteTarget.id));
            setDeleteTarget(null);
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        }
    };

    const handleSaved = (saved) => {
        setUsers((arr) => {
            const idx = arr.findIndex((u) => u.id === saved.id);
            if (idx === -1) return [saved, ...arr];
            const copy = [...arr];
            copy[idx] = saved;
            return copy;
        });
    };

    return (
        <div className="space-y-6" data-testid="team-page">
            <header className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h1 className="text-3xl sm:text-4xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>
                        إدارة الفريق
                    </h1>
                    <p className="text-muted-foreground">إضافة موظفين، تحديد الأدوار، والتحكم بالصلاحيات.</p>
                </div>
                <button
                    onClick={() => setModal({ mode: "add" })}
                    className="inline-flex items-center gap-2 px-4 py-2.5 bg-brand text-white text-sm font-semibold rounded-lg bg-brand-hover"
                    data-testid="team-add-btn"
                >
                    <Plus size={18} weight="bold" />
                    إضافة مستخدم
                </button>
            </header>

            <div className="bg-white rounded-xl border border-border shadow-sm overflow-hidden">
                <div className="px-5 py-3 border-b border-border flex items-center gap-2 bg-slate-50">
                    <UsersThree size={20} className="text-brand" />
                    <span className="font-bold text-sm" data-testid="team-count">
                        {loading ? "..." : `${users.length} مستخدم`}
                    </span>
                </div>
                <div className="overflow-x-auto">
                    <table className="mezan-table w-full text-sm">
                        <thead className="bg-slate-50 text-xs text-muted-foreground uppercase">
                            <tr>
                                <th className="text-right px-5 py-2.5 font-bold">الاسم</th>
                                <th className="text-right px-5 py-2.5 font-bold">البريد الإلكتروني</th>
                                <th className="text-right px-5 py-2.5 font-bold">الدور</th>
                                <th className="text-right px-5 py-2.5 font-bold">الصلاحيات الفعالة</th>
                                <th className="text-right px-5 py-2.5 font-bold">إجراءات</th>
                            </tr>
                        </thead>
                        <tbody>
                            {loading && (
                                <tr><td colSpan={5} className="text-center py-8 text-muted-foreground">جاري التحميل…</td></tr>
                            )}
                            {!loading && users.length === 0 && (
                                <tr><td colSpan={5} className="text-center py-8 text-muted-foreground">لا يوجد مستخدمون.</td></tr>
                            )}
                            {!loading && users.map((u) => (
                                <tr key={u.id} className="border-t border-border hover:bg-slate-50/50" data-testid={`team-user-row-${u.id}`}>
                                    <td className="px-5 py-3 font-semibold" data-testid={`team-user-name-${u.id}`}>{u.name}</td>
                                    <td className="px-5 py-3 text-muted-foreground" dir="ltr" style={{ textAlign: "right" }}>{u.email}</td>
                                    <td className="px-5 py-3"><RoleBadge role={u.role} /></td>
                                    <td className="px-5 py-3">
                                        <span className="inline-flex items-center px-2 py-0.5 rounded bg-slate-100 text-slate-700 text-xs font-bold" data-testid={`team-user-perms-count-${u.id}`}>
                                            {u.effective_permissions?.length || 0} صلاحية
                                        </span>
                                    </td>
                                    <td className="px-5 py-3">
                                        <div className="flex items-center gap-1.5">
                                            <button
                                                onClick={() => setModal({ mode: "edit", initial: u })}
                                                disabled={u.is_owner}
                                                className="p-1.5 rounded text-brand hover:bg-brand/10 disabled:opacity-30 disabled:cursor-not-allowed"
                                                data-testid={`team-edit-btn-${u.id}`}
                                                title={u.is_owner ? "لا يمكن تعديل Owner" : "تعديل"}
                                            >
                                                <PencilSimple size={18} />
                                            </button>
                                            <button
                                                onClick={() => setDeleteTarget(u)}
                                                disabled={u.is_owner || u.id === user.id}
                                                className="p-1.5 rounded text-rose-600 hover:bg-rose-50 disabled:opacity-30 disabled:cursor-not-allowed"
                                                data-testid={`team-delete-btn-${u.id}`}
                                                title={u.is_owner ? "لا يمكن حذف Owner" : (u.id === user.id ? "لا يمكنك حذف نفسك" : "حذف")}
                                            >
                                                <Trash size={18} />
                                            </button>
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>

            {modal && catalogue && (
                <UserFormModal
                    mode={modal.mode}
                    initial={modal.initial}
                    catalogue={catalogue}
                    onClose={() => setModal(null)}
                    onSaved={handleSaved}
                />
            )}
            {deleteTarget && (
                <DeleteConfirmModal target={deleteTarget} onClose={() => setDeleteTarget(null)} onConfirm={handleDelete} />
            )}
        </div>
    );
}
