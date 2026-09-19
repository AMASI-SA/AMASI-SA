import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "../../components/ui/dialog";

export default function SettlementJournalDialog({ ledger, currency = "SAR" }) {
    const entries = ledger?.entries || [];
    if (!ledger?.txn_group_id || !entries.length) return null;
    const amount = (value) => Number(value || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
    return (
        <Dialog>
            <DialogTrigger asChild>
                <button type="button" className="rounded-lg border border-emerald-200 bg-emerald-50 px-2 py-1 text-xs font-bold text-emerald-800"
                    data-testid="settlement-register-journal-link">فتح القيد</button>
            </DialogTrigger>
            <DialogContent dir="rtl" className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
                <DialogHeader>
                    <DialogTitle>مجموعة قيد التسوية</DialogTitle>
                    <DialogDescription>القيد المرحّل المرتبط بهذه التسوية — عرض فقط.</DialogDescription>
                </DialogHeader>
                <p className="font-mono text-xs" dir="ltr">{ledger.txn_group_id}</p>
                <table className="w-full text-sm">
                    <thead><tr><th>الحساب</th><th>الجانب</th><th>المبلغ</th></tr></thead>
                    <tbody>{entries.map((entry, index) => (
                        <tr key={entry.id || index} className="border-t">
                            <td>{entry.entity_name || entry.account_name || entry.sub_account || entry.entity_type || "—"}</td>
                            <td>{entry.side === "debit" ? "مدين" : "دائن"}</td>
                            <td dir="ltr" className="font-mono">{amount(entry.amount)} {currency}</td>
                        </tr>
                    ))}</tbody>
                </table>
            </DialogContent>
        </Dialog>
    );
}
