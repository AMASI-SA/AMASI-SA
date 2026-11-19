import { useEffect, useState } from "react";
import api from "../lib/api";

import { webPageSections } from "../lib/webPageAccess";

function normalizePageName(text) {
    return String(text || "").normalize("NFKC").replace(/[ً-ٰٟـ]/g, "").replace(/[أإآٱ]/g, "ا").replace(/ى/g, "ي").trim().toLowerCase();
}

export default function WebPagePermissions({ initialMemberId = "", onMemberChange }) {
    const [catalogue, setCatalogue] = useState(null);
    const [memberId, setMemberId] = useState("");
    const [selected, setSelected] = useState({});
    const [section, setSection] = useState("products");
    const [search, setSearch] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [saved, setSaved] = useState(false);
    const [reload, setReload] = useState(0);

    useEffect(() => {
        const controller = new AbortController();
        setCatalogue(null);
        setError("");
        setSaved(false);
        api.get("/web-page-access-v1", { signal: controller.signal }).then(({ data }) => {
            if (controller.signal.aborted) return;
            const member = data.members.find((row) => row.id === initialMemberId);
            setCatalogue(data);
            setMemberId(member?.id || "");
            onMemberChange?.(member?.id || "");
            setSelected(member?.page_modes || {});
        }).catch(() => {
            if (!controller.signal.aborted) setError("تعذّر تحميل صفحات ميزان والموظفين.");
        });
        return () => controller.abort();
    }, [initialMemberId, reload, onMemberChange]);

    if (!catalogue) return error
        ? <div role="alert" className="rounded-xl border bg-white p-5">
            {error} <button type="button" onClick={() => setReload((value) => value + 1)} className="font-bold underline">إعادة المحاولة</button>
        </div>
        : <p role="status" className="p-5">جارٍ تحميل صلاحيات الصفحات…</p>;

    const member = catalogue.members.find((row) => row.id === memberId);
    function selectMember(id) {
        setMemberId(id);
        onMemberChange?.(id);
        setSelected(catalogue.members.find((row) => row.id === id)?.page_modes || {});
        setError("");
        setSaved(false);
    }

    const sections = webPageSections(catalogue.pages);
    const activeSection = sections.find((item) => item.id === section) || sections[0];
    const needle = normalizePageName(search);
    const sectionPages = needle ? catalogue.pages.filter(page => normalizePageName(page.label).includes(needle)) : activeSection?.pages || [];
    const editablePages = sectionPages.filter((page) => !page.owner_only);
    const selectedCount = Object.keys(selected).length;

    function setModes(pages, mode) {
        setSaved(false);
        setSelected((current) => {
            const next = { ...current };
            for (const page of pages) {
                if (mode) next[page.id] = mode === "write" && !page.write_label ? "read" : mode;
                else delete next[page.id];
            }
            return next;
        });
    }

    async function save(event) {
        event.preventDefault();
        if (!member || busy) return;
        setBusy(true);
        setError("");
        setSaved(false);
        try {
            const { data } = await api.put("/web-page-access-v1/" + encodeURIComponent(member.id), {
                expected_revision: member.revision,
                page_modes: selected,
            });
            setCatalogue((current) => ({
                ...current,
                members: current.members.map((row) => row.id === data.member.id ? data.member : row),
            }));
            setSelected(data.member.page_modes);
            setSaved(true);
        } catch (reason) {
            setError(reason?.response?.status === 409
                ? "تغيّر الاختيار في جلسة أخرى. أعد تحميل الصلاحيات قبل الحفظ."
                : "تعذّر حفظ صلاحيات الصفحات.");
        } finally {
            setBusy(false);
        }
    }

    if (!catalogue.members.length) return <p className="rounded-xl border bg-white p-5">اربط حساب دخول بموظف نشط لإسناد صفحات ميزان.</p>;

    return (
        <form onSubmit={save} className="space-y-4 rounded-2xl border bg-white p-4 sm:p-6" data-testid="web-page-permissions">
            <label className="block max-w-xl font-bold">
                الموظف
                <select value={memberId} disabled={busy} onChange={(event) => selectMember(event.target.value)}
                    className="mt-2 block w-full rounded-xl border bg-white p-3">
                    <option value="">اختر الموظف</option>
                    {catalogue.members.map((row) => <option key={row.id} value={row.id}>
                        {[row.name, row.email].filter(Boolean).join(" — ")}
                    </option>)}
                </select>
            </label>
            {initialMemberId && !member && !catalogue.members.some((row) => row.id === initialMemberId) &&
                <p role="status">لا يوجد حساب دخول مطابق لهذا الموظف ضمن فريق المتجر. اختر الحساب من القائمة.</p>}
            {member && <>
                <p className="text-sm text-slate-600">
                    اختر صفحات الموظف في كل قسم. تظهر الصفحات المختارة فقط في الهيدر عند دخوله ميزان.
                </p>
                <label className="block max-w-xl font-bold">
                    البحث في جميع الصفحات
                    <input type="search" value={search} onChange={event=>setSearch(event.target.value)} placeholder="اكتب اسم الصفحة" className="mt-2 block w-full rounded-xl border bg-white p-3"/>
                </label>
                <label className="block max-w-xl font-bold">
                    القسم
                    <select value={activeSection?.id || ""} disabled={busy || Boolean(needle)} onChange={(event) => setSection(event.target.value)}
                        className="mt-2 block w-full rounded-xl border bg-white p-3">
                        {sections.map(({ id, label, pages }) => (
                            <option key={id} value={id}>
                                {label} ({pages.filter((page) => selected[page.id]).length})
                            </option>
                        ))}
                    </select>
                </label>
                {(activeSection?.id === "balances" || needle && sectionPages.some(page => page.section === "balances")) && <p className="text-sm text-slate-600">
                    حدّد الصفحات هنا، واختر الحسابات والعمليات المتاحة للموظف من <a className="font-bold underline" href={"/operational-balances/access?member=" + encodeURIComponent(member.id)}>صلاحيات موظفي الأرصدة</a>.
                </p>}
                <fieldset disabled={busy} className="space-y-3 rounded-xl border p-3 sm:p-4">
                    <legend className="px-2 font-bold">{needle ? "نتائج البحث (" + sectionPages.length + ")" : activeSection?.label}</legend>
                    {editablePages.length > 0 && <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-bold">{needle ? "الصفحات الظاهرة" : "كل صفحات القسم"}</span>
                        <button type="button" onClick={() => setModes(editablePages, "read")} className="rounded-lg border px-3 py-2 text-sm">قراءة للجميع</button>
                        {editablePages.some((page) => page.write_label) && (
                            <button type="button" onClick={() => setModes(editablePages, "write")} className="rounded-lg border px-3 py-2 text-sm">قراءة وكتابة للجميع</button>
                        )}
                        <button type="button" onClick={() => setModes(editablePages, "")} className="rounded-lg border px-3 py-2 text-sm">{needle ? "إزالة صلاحيات النتائج" : "إزالة القسم"}</button>
                    </div>}
                    {editablePages.some((page) => page.write_label) && editablePages.some((page) => !page.write_label) && (
                        <p className="text-xs text-slate-600">تبقى صفحات العرض على وضع القراءة عند تطبيق الكتابة على القسم.</p>
                    )}
                    {!sectionPages.length && <p>لا توجد صفحات مطابقة.</p>}
                    {sectionPages.map((page) => (
                        <label key={page.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-slate-50 p-3 text-sm">
                            <span>
                                <span className="font-bold">{page.label}</span>
                                {needle && <span className="mr-2 text-xs text-slate-500">{page.section_label}</span>}
                                <span className="block text-xs text-slate-500">{page.owner_only ? "خاصة بالمالك" : page.write_label || "عرض البيانات"}</span>
                            </span>
                            <select aria-label={page.label} disabled={Boolean(page.owner_only)} value={selected[page.id] || ""} onChange={(event) => setModes([page], event.target.value)}
                                className="min-w-36 rounded-lg border bg-white p-2">
                                <option value="">{page.owner_only ? "للمالك فقط" : "بلا صلاحية"}</option>
                                <option value="read">قراءة</option>
                                {page.write_label && <option value="write">قراءة وكتابة</option>}
                            </select>
                        </label>
                    ))}
                </fieldset>
                <div className="flex flex-wrap items-center gap-3 border-t pt-4">
                    <button type="submit" disabled={busy} className="rounded-xl bg-emerald-700 px-5 py-2.5 font-bold text-white disabled:opacity-60">
                        {busy ? "جارٍ الحفظ…" : "حفظ صفحات الموظف"}
                    </button>
                    <span className="text-sm text-slate-600">{selectedCount} صفحة مختارة</span>
                    {saved && <span role="status" className="text-sm font-bold text-emerald-800">تم حفظ الصفحات.</span>}
                </div>
            </>}
            {error && <p role="alert" className="text-sm font-bold text-rose-700">{error} <button type="button" disabled={busy} onClick={() => setReload((value) => value + 1)} className="underline">إعادة التحميل</button></p>}
        </form>
    );
}
