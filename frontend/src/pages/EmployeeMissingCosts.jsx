import { useEffect, useState } from "react";
import api from "../lib/api";

const PAGE_SIZE = 8;

export default function EmployeeMissingCosts() {
    const [result, setResult] = useState(null);
    const [page, setPage] = useState(0);
    const [attempt, setAttempt] = useState(0);
    const [error, setError] = useState(false);

    useEffect(() => {
        const controller = new AbortController();
        setError(false);
        api.get("/product-costs/missing", { signal: controller.signal }).then(({ data }) => {
            if (!controller.signal.aborted) setResult(data);
        }).catch(() => {
            if (!controller.signal.aborted) setError(true);
        });
        return () => controller.abort();
    }, [attempt]);

    const rows = result?.items || [];
    return <section dir="rtl" className="space-y-4 rounded-2xl border bg-white p-5">
        <h1 className="text-xl font-bold">المنتجات بدون تكلفة</h1>
        {error ? <p role="alert">تعذّر تحميل المنتجات. <button onClick={() => setAttempt(n => n + 1)}>إعادة المحاولة</button></p>
            : !result ? <p role="status">جارٍ تحميل المنتجات…</p>
            : <>
                <p>عدد المنتجات التي تحتاج إلى تكلفة في آخر {result.window_days} يومًا: {result.count}</p>
                {!!result.excel_no_products_count && <p>طلبات مستوردة بلا تفاصيل منتجات: {result.excel_no_products_count}</p>}
                {rows.length > 0 && <div className="overflow-x-auto"><table className="w-full text-right text-sm">
                    <thead><tr>{["المنتج", "رمز المنتج", "الكمية", "آخر طلب"].map(label => <th key={label} className="p-3">{label}</th>)}</tr></thead>
                    <tbody>{rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map((row, index) =>
                        <tr className="border-t" key={row.sku || row.product_id || index}>
                            <td className="p-3">{row.name || "—"}</td><td>{row.sku || "—"}</td>
                            <td>{row.total_quantity ?? "—"}</td><td>{row.last_order_number || "—"}</td>
                        </tr>)}</tbody>
                </table></div>}
                {rows.length > PAGE_SIZE && <nav aria-label="صفحات المنتجات" className="flex gap-3">
                    <button disabled={page === 0} onClick={() => setPage(n => n - 1)}>السابق</button>
                    <span>{page + 1} من {Math.ceil(rows.length / PAGE_SIZE)}</span>
                    <button disabled={(page + 1) * PAGE_SIZE >= rows.length} onClick={() => setPage(n => n + 1)}>التالي</button>
                </nav>}
            </>}
    </section>;
}
