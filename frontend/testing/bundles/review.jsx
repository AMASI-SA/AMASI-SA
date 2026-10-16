import React from "react";
import { createRoot } from "react-dom/client";
import BundleEditor from "../../src/components/products/BundleEditor";
import BundleReportPanel from "../../src/components/products/BundleReportPanel";
import BundleOperationalPanel from "../../src/components/orders/BundleOperationalPanel";
import "./style.css";

const params = new URLSearchParams(window.location.search);
createRoot(document.getElementById("root")).render(<main className="mx-auto max-w-6xl space-y-5 p-5" dir="rtl">
    <header className="rounded-2xl bg-slate-900 p-5 text-white"><h1 className="text-xl font-bold">ميزان 2 — مراجعة البكجات محليًا</h1><p className="mt-2 text-sm">واجهة الاختبار تستخدم المكونات الفعلية وواجهة API محلية. البيانات التجريبية منفصلة عن الإنتاج.</p></header>
    <BundleEditor productId={params.get("product") || "bundle-demo"} salePrice={200} />
    <BundleOperationalPanel orderNumber={params.get("order") || "100"} canReceive canChangeSource />
    <BundleReportPanel />
</main>);
