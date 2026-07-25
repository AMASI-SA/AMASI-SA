import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const ROOM_TYPES = [
    ["storage", "تخزين"],
    ["installation_engraving", "تركيب ونحت"],
    ["shipping_labeling", "شحن وعنونة"],
    ["packing", "تغليف"],
    ["returns", "مرتجعات"],
    ["raw_materials", "مواد خام"],
    ["ready_to_ship", "جاهز للشحن"],
    ["worker_housing", "سكن عمال"],
    ["office", "مكتب"],
    ["other", "أخرى"],
];

const STORAGE_TYPES = new Set(["storage", "returns", "raw_materials", "ready_to_ship"]);

function Select(props) {
    return <select {...props} className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-brand" />;
}

function Input(props) {
    return <input {...props} className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-brand" />;
}

function Field({ label, children }) {
    return <label className="block"><span className="mb-1 block text-xs font-bold text-slate-600">{label}</span>{children}</label>;
}

export default function WarehouseRoomsPanel() {
    const [warehouses, setWarehouses] = useState([]);
    const [warehouseId, setWarehouseId] = useState("");
    const [rooms, setRooms] = useState([]);
    const [roomId, setRoomId] = useState("");
    const [loading, setLoading] = useState(false);
    const [roomForm, setRoomForm] = useState({ name: "", room_type: "storage", allows_cabinets: true, notes: "" });
    const [cabinetForm, setCabinetForm] = useState({ cabinet_name: "", length: 4, width: 6, purpose: "permanent_storage", max_items_per_location: "" });

    const selectedRoom = useMemo(() => rooms.find((row) => row.id === roomId) || null, [rooms, roomId]);

    const loadWarehouses = async () => {
        const response = await api.get("/warehouse-locations/warehouses");
        const items = response.data?.items || [];
        setWarehouses(items);
        if (!warehouseId && items.length) setWarehouseId(items[0].id);
    };

    const loadRooms = async (id) => {
        if (!id) {
            setRooms([]);
            setRoomId("");
            return;
        }
        const response = await api.get(`/warehouse-locations-v2/warehouses/${id}/rooms`);
        const items = response.data?.items || [];
        setRooms(items);
        if (roomId && !items.some((row) => row.id === roomId)) setRoomId("");
    };

    useEffect(() => {
        loadWarehouses().catch(() => toast.error("تعذر تحميل المباني"));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        loadRooms(warehouseId).catch(() => toast.error("تعذر تحميل الغرف"));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [warehouseId]);

    const changeRoomType = (room_type) => {
        setRoomForm((current) => ({
            ...current,
            room_type,
            allows_cabinets: STORAGE_TYPES.has(room_type),
        }));
    };

    const createRoom = async (event) => {
        event.preventDefault();
        if (!warehouseId) return toast.warning("اختر المبنى أولًا");
        setLoading(true);
        try {
            const response = await api.post("/warehouse-locations-v2/rooms", {
                ...roomForm,
                warehouse_id: warehouseId,
                notes: roomForm.notes || null,
            });
            toast.success(`تم إنشاء الغرفة رقم ${response.data.room_number}`);
            setRoomForm({ name: "", room_type: "storage", allows_cabinets: true, notes: "" });
            await loadRooms(warehouseId);
            setRoomId(response.data.id);
        } catch (error) {
            toast.error(error?.response?.data?.detail?.message || "تعذر إنشاء الغرفة");
        } finally {
            setLoading(false);
        }
    };

    const createCabinet = async (event) => {
        event.preventDefault();
        if (!roomId) return toast.warning("اختر الغرفة أولًا");
        setLoading(true);
        try {
            const response = await api.post(`/warehouse-locations-v2/rooms/${roomId}/cabinets`, {
                ...cabinetForm,
                length: Number(cabinetForm.length),
                width: Number(cabinetForm.width),
                max_items_per_location: cabinetForm.max_items_per_location ? Number(cabinetForm.max_items_per_location) : null,
            });
            toast.success(`تم إنشاء الدولاب رقم ${response.data.cabinet.cabinet_number} داخل الغرفة`);
            setCabinetForm({ cabinet_name: "", length: 4, width: 6, purpose: "permanent_storage", max_items_per_location: "" });
            await loadRooms(warehouseId);
        } catch (error) {
            toast.error(error?.response?.data?.detail?.message || "تعذر إنشاء الدولاب داخل الغرفة");
        } finally {
            setLoading(false);
        }
    };

    return (
        <section className="space-y-5" dir="rtl">
            <div className="rounded-xl border border-violet-100 bg-violet-50 p-4">
                <h2 className="text-lg font-extrabold text-violet-950">الغرف والأقسام الاختيارية</h2>
                <p className="mt-1 text-sm text-violet-800">يمكن أن يحتوي المبنى على دواليب مباشرة، أو غرف، أو الاثنين معًا. الغرفة ليست إلزامية.</p>
            </div>

            <div className="grid gap-5 xl:grid-cols-2">
                <form onSubmit={createRoom} className="rounded-xl border bg-white p-5">
                    <h3 className="mb-4 text-lg font-extrabold">إضافة غرفة أو قسم</h3>
                    <div className="grid gap-3 md:grid-cols-2">
                        <Field label="المبنى">
                            <Select value={warehouseId} onChange={(event) => setWarehouseId(event.target.value)} required>
                                <option value="">اختر المبنى</option>
                                {warehouses.map((row) => <option key={row.id} value={row.id}>{row.name} — {row.city} — رقم {row.warehouse_number}</option>)}
                            </Select>
                        </Field>
                        <Field label="اسم الغرفة"><Input required placeholder="مثال: غرفة التركيب والنحت" value={roomForm.name} onChange={(event) => setRoomForm({ ...roomForm, name: event.target.value })} /></Field>
                        <Field label="نوع الاستخدام">
                            <Select value={roomForm.room_type} onChange={(event) => changeRoomType(event.target.value)}>
                                {ROOM_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                            </Select>
                        </Field>
                        <Field label="السماح بإضافة دواليب">
                            <Select value={roomForm.allows_cabinets ? "yes" : "no"} onChange={(event) => setRoomForm({ ...roomForm, allows_cabinets: event.target.value === "yes" })}>
                                <option value="yes">نعم</option>
                                <option value="no">لا — غرفة تشغيلية</option>
                            </Select>
                        </Field>
                        <div className="md:col-span-2"><Field label="ملاحظات (اختياري)"><Input value={roomForm.notes} onChange={(event) => setRoomForm({ ...roomForm, notes: event.target.value })} /></Field></div>
                    </div>
                    <button disabled={loading} className="mt-4 rounded-lg bg-violet-700 px-4 py-2 text-sm font-bold text-white disabled:opacity-50">إنشاء الغرفة</button>
                </form>

                <form onSubmit={createCabinet} className="rounded-xl border bg-white p-5">
                    <h3 className="mb-4 text-lg font-extrabold">إضافة دولاب داخل غرفة</h3>
                    <Field label="الغرفة">
                        <Select value={roomId} onChange={(event) => setRoomId(event.target.value)} required>
                            <option value="">اختر الغرفة</option>
                            {rooms.map((row) => <option key={row.id} value={row.id}>{row.name} — غرفة رقم {row.room_number}{row.allows_cabinets ? "" : " — بدون دواليب"}</option>)}
                        </Select>
                    </Field>
                    {selectedRoom && !selectedRoom.allows_cabinets && <p className="mt-3 rounded-lg bg-amber-50 p-3 text-sm font-bold text-amber-800">هذه غرفة تشغيلية ولا تسمح بالدواليب. يمكنك تعديل النوع لاحقًا عند إضافة شاشة تحرير الغرف.</p>}
                    <div className="mt-3 grid gap-3 md:grid-cols-2">
                        <Field label="اسم الدولاب (اختياري)"><Input value={cabinetForm.cabinet_name} onChange={(event) => setCabinetForm({ ...cabinetForm, cabinet_name: event.target.value })} /></Field>
                        <Field label="الطول"><Input type="number" min="1" max="100" required value={cabinetForm.length} onChange={(event) => setCabinetForm({ ...cabinetForm, length: event.target.value })} /></Field>
                        <Field label="العرض"><Input type="number" min="1" max="100" required value={cabinetForm.width} onChange={(event) => setCabinetForm({ ...cabinetForm, width: event.target.value })} /></Field>
                        <Field label="سعة الخانة (اختياري)"><Input type="number" min="1" value={cabinetForm.max_items_per_location} onChange={(event) => setCabinetForm({ ...cabinetForm, max_items_per_location: event.target.value })} /></Field>
                    </div>
                    <button disabled={loading || !selectedRoom?.allows_cabinets} className="mt-4 rounded-lg bg-brand px-4 py-2 text-sm font-bold text-white disabled:opacity-50">إنشاء الدولاب والخانات</button>
                </form>
            </div>

            <div className="rounded-xl border bg-white p-5">
                <div className="mb-4 flex items-center justify-between"><h3 className="text-lg font-extrabold">غرف المبنى</h3><span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-bold">{rooms.length} غرفة</span></div>
                {!rooms.length ? <div className="rounded-lg border border-dashed p-8 text-center text-sm text-slate-500">المبنى بدون غرف حاليًا، ويمكن إضافة الدواليب إليه مباشرة من القسم العلوي.</div> : (
                    <div className="grid gap-4 lg:grid-cols-2">
                        {rooms.map((room) => (
                            <article key={room.id} className="rounded-xl border p-4">
                                <div className="flex items-start justify-between gap-3"><div><h4 className="font-extrabold">غرفة {room.room_number} — {room.name}</h4><p className="mt-1 text-xs text-slate-500">{ROOM_TYPES.find(([value]) => value === room.room_type)?.[1] || room.room_type}</p></div><span className={`rounded-full px-2 py-1 text-[11px] font-bold ${room.allows_cabinets ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>{room.allows_cabinets ? `${room.cabinet_count || 0} دواليب` : "تشغيلية"}</span></div>
                                {!!room.cabinets?.length && <div className="mt-3 flex flex-wrap gap-2">{room.cabinets.map((cabinet) => <span key={cabinet.id} className="rounded-lg border bg-slate-50 px-3 py-2 text-xs font-bold">دولاب {cabinet.cabinet_number} — {cabinet.total_locations} خانة</span>)}</div>}
                            </article>
                        ))}
                    </div>
                )}
            </div>
        </section>
    );
}
