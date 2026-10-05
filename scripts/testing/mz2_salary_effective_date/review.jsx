import React from "react";
import {createRoot} from "react-dom/client";
import {Toaster} from "sonner";
import EmployeesV2Management from "../../../frontend/src/pages/EmployeesV2Management";
import "./styles.css";

// Reproducible fixture clock only; never imported by the application.
const NativeDate = Date;
window.Date = class extends NativeDate {
    constructor(...args) { super(...(args.length ? args : ["2026-10-05T12:00:00+03:00"])); }
    static now() { return new NativeDate("2026-10-05T12:00:00+03:00").getTime(); }
};
createRoot(document.getElementById("root")).render(<>
    <p className="bg-amber-100 p-4 font-bold text-center">اختبار محلي ببيانات اصطناعية — 2026-10-05 — ليس الإنتاج. واجهة وخدمة الموظفين وAPI الفعلية مع MongoDB معزول. لا كُتّاب محاسبة.</p>
    <EmployeesV2Management/><Toaster/>
</>);
