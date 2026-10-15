import { useState } from "react";
import AdvertisingPanel from "./AdvertisingPanel";
import DriverPanel from "./DriverPanel";
import ObligationsPanel from "./ObligationsPanel";
import "./h2.css";

const reviews = [
    { id: "advertising", label: "الإعلانات والصرف اليومي", detail: "المحفظة والمستحق وجاهزية الترحيل", Panel: AdvertisingPanel },
    { id: "delivery", label: "الشحن والموصلون ومراجعة البنك وPOS", detail: "المسؤولية والتحصيل وحالات المراجعة", Panel: DriverPanel },
    { id: "obligations", label: "المقدمات والالتزامات والضريبة", detail: "السياسات والرسوم والأشخاص الخارجيون", Panel: ObligationsPanel },
];

export default function DailyReviewDesk() {
    const [active, setActive] = useState("");
    const selected = reviews.find(item => item.id === active);
    return <section className="h2-desk" aria-labelledby="h2-review-title" data-testid="h2-daily-review-desk" dir="rtl">
        <h2 id="h2-review-title">مراجعات اليوم</h2>
        <p>افتح التفاصيل هنا. الحالات والأرصدة من ميزان 2؛ القدرات غير الجاهزة تظهر صراحةً.</p>
        <div className="h2-review-actions">
            {reviews.map(item => <button type="button" key={item.id} aria-expanded={active === item.id} aria-controls="h2-inline-review" onClick={() => setActive(active === item.id ? "" : item.id)}>
                <strong>{item.label}</strong><span>{item.detail}</span>
            </button>)}
        </div>
        <div id="h2-inline-review">
            {selected && <section className="h2-inline-panel" aria-label={selected.label}>
                <header><h3>{selected.label}</h3><button type="button" onClick={() => setActive("")}>إغلاق التفاصيل</button></header>
                <selected.Panel />
            </section>}
        </div>
    </section>;
}
