import { useEffect, useState } from "react";
import { CaretLeft, CaretRight } from "@phosphor-icons/react";
import "./reportPagination.css";

export default function PaginatedReportTable({ rows = [], label, pageSize = 13, resetKey = "", showAll = false, children }) {
    const [selection, setSelection] = useState({ resetKey, page: 1 });
    const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
    const page = selection.resetKey === resetKey ? Math.min(selection.page, pageCount) : 1;
    const offset = (page - 1) * pageSize;

    useEffect(() => {
        setSelection(previous => previous.resetKey === resetKey && previous.page === page ? previous : { resetKey, page });
    }, [resetKey, page]);

    return <div className="op-paginated-table">
        {children(showAll ? rows : rows.slice(offset, offset + pageSize))}
        <nav className="op-report-pagination" aria-label={`صفحات ${label}`} dir="rtl">
            <button type="button" aria-label={`الصفحة السابقة — ${label}`} disabled={page === 1}
                onClick={() => setSelection({ resetKey, page: page - 1 })}>
                <CaretRight size={18} aria-hidden="true" /><span>السابق</span>
            </button>
            <span className="op-report-page-status" role="status" aria-live="polite">
                <span>الصفحة <bdi>{page}</bdi> من <bdi>{pageCount}</bdi></span>
                <small>عرض <bdi>{rows.length ? offset + 1 : 0}–{Math.min(offset + pageSize, rows.length)}</bdi> من <bdi>{rows.length}</bdi></small>
            </span>
            <button type="button" aria-label={`الصفحة التالية — ${label}`} disabled={page === pageCount}
                onClick={() => setSelection({ resetKey, page: page + 1 })}>
                <span>التالي</span><CaretLeft size={18} aria-hidden="true" />
            </button>
        </nav>
    </div>;
}
