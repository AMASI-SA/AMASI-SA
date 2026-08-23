// Iter-161 Phase 2 — Unified Financial Entry Screen
//
// ONE input gateway for every financial movement in the app.
// Each operation produces a balanced txn_group in general_ledger
// via the /api/accounting/* endpoints. No direct mutation of stored
// balances, no DELETE — corrections only via reverse / settlement /
// writeoff / adjustment on the resulting ledger entry.

import React, { useState, useEffect, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import api from "../lib/api";
import HelpToggle from "../components/HelpToggle";
import Iter245MovementForm from "../components/Iter245MovementForm";
import { toast } from "sonner";
import { todaySA, monthISO_SA } from "../lib/dates";

// Iter-186 — Searchable employee picker.
//   • Type-ahead search over the employee list.
//   • Inline custody-balance badge per row (red when > 0).
//   • When `freezeZeroCustody` is true, employees with custody = 0
//     are rendered disabled — used by «إرجاع عهدة نقداً» and
//     «تسوية عهدة بفواتير» since both operations require an open
//     custody to act on.
function EmployeePicker({
    employees, custodyBalances, selectedId, onSelect,
    freezeZeroCustody = false, label = "الموظف",
    testidSuffix = "",
    excludeIds = [],
}) {
    const [q, setQ] = useState("");
    const [open, setOpen] = useState(false);
    const selected = employees.find((e) => e.id === selectedId);
    const list = employees.filter(
        (e) => !excludeIds.includes(e.id)
                && (e.name || "").toLowerCase().includes(q.trim().toLowerCase()),
    );
    return (
        <div className="relative">
            <label className="block text-sm font-bold text-slate-700 mb-1">
                {label}:
            </label>
            <input
                type="text"
                value={selected ? selected.name : q}
                onChange={(ev) => {
                    if (selected) onSelect("");
                    setQ(ev.target.value);
                    setOpen(true);
                }}
                onFocus={() => setOpen(true)}
                onBlur={() => setTimeout(() => setOpen(false), 200)}
                placeholder="🔍 ابحث باسم الموظف…"
                className="w-full px-3 py-2 border border-slate-300 rounded text-sm"
                data-testid={`unified-employee-search${testidSuffix}`}
            />
            {open && (
                <div className="absolute z-20 mt-1 w-full max-h-72 overflow-y-auto bg-white border border-slate-300 rounded-lg shadow-lg"
                     data-testid={`unified-employee-list${testidSuffix}`}>
                    {list.length === 0 && (
                        <div className="p-3 text-xs text-slate-500">
                            — لا توجد نتائج —
                        </div>
                    )}
                    {list.map((e) => {
                        const cust = Number(custodyBalances?.[e.id] || 0);
                        const hasCustody = cust > 0.005;
                        const frozen = freezeZeroCustody && !hasCustody;
                        return (
                            <button
                                key={e.id}
                                type="button"
                                disabled={frozen}
                                onMouseDown={(ev) => {
                                    if (frozen) return;
                                    ev.preventDefault();
                                    onSelect(e.id);
                                    setQ("");
                                    setOpen(false);
                                }}
                                className={`w-full flex items-center justify-between gap-2 px-3 py-2 text-sm text-right border-b border-slate-100 last:border-b-0 ${
                                    frozen
                                        ? "bg-slate-50 text-slate-400 cursor-not-allowed"
                                        : "hover:bg-emerald-50 text-slate-800 cursor-pointer"
                                }`}
                                data-testid={`unified-employee-opt${testidSuffix}-${e.id}`}
                            >
                                <span className="font-bold">{e.name}</span>
                                {hasCustody && (
                                    <span className="text-[11px] font-extrabold text-rose-700 num">
                                        عهدة: {cust.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س
                                    </span>
                                )}
                                {!hasCustody && freezeZeroCustody && (
                                    <span className="text-[11px] text-slate-400 font-bold">
                                        🔒 لا توجد عهدة
                                    </span>
                                )}
                            </button>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

const OP_TYPES = [
    { value: "advance_grant",        label: "💰 سلفة موظف",         section: "employees" },
    { value: "salary_settle",        label: "📅 صرف راتب",            section: "employees" },
    { value: "salary_accrual",       label: "📝 استحقاق راتب شهري",   section: "employees" },
    { value: "custody_grant",        label: "🎒 تسليم عهدة",           section: "employees" },
    { value: "custody_return",       label: "🔙 إرجاع عهدة نقداً",     section: "employees" },
    { value: "custody_settle",       label: "🧾 تسوية عهدة بفواتير",  section: "employees" },
    { value: "custody_transfer",     label: "🔄 نقل عهدة بين موظفين",  section: "employees" },
    { value: "supplier_invoice",     label: "📄 فاتورة مورد",          section: "suppliers" },
    { value: "supplier_pay",         label: "💸 سداد مورد",            section: "suppliers" },
    { value: "external_grant",       label: "🤝 سلفة لشخص خارجي",    section: "externals" },
    { value: "external_collect",     label: "💵 تحصيل من شخص خارجي",  section: "externals" },
    { value: "expense_record",       label: "🛒 مصروف عام",             section: "general" },
    { value: "fixed_asset_purchase", label: "🏛️ شراء أصل ثابت",       section: "general" },
    { value: "bank_transfer",        label: "🔄 تحويل بين الحسابات",   section: "general" },
    { value: "courier_cod_settle",   label: "🚚 تسوية COD مع شركة شحن", section: "shipping" },
];

// Iter-246 — Op-types that switch the UI to the embedded Iter-245
// movement form (no legacy form, no duplicate route).
const ITER245_OPS = {
    supplier_invoice: "supplier_invoice",
    expense_record: "general_expense",
    fixed_asset_purchase: "fixed_asset",
};

export default function UnifiedEntryScreen() {
    const [searchParams] = useSearchParams();
    const [opType, setOpType] = useState("");
    const [busy, setBusy] = useState(false);
    // Iter-209 — Recent ledger transactions panel + green highlight on
    // the just-submitted txn. Stays on the page (no navigation) so the
    // merchant can record another transaction immediately.
    const [recentTxns, setRecentTxns] = useState([]);
    const [highlightGroupId, setHighlightGroupId] = useState(null);
    const [recentLoading, setRecentLoading] = useState(false);
    const [employees, setEmployees] = useState([]);
    const [suppliers, setSuppliers] = useState([]);

    // Iter-173 — Smart source-account filter based on the operation type.
    // Cash outflows (sending money to an employee/supplier/external/expense)
    // can ONLY be funded from a real cash account: a bank or a manual
    // cash account. Pulling them from Salla/Tamara/Tabby/Imkan/COD is
    // accounting nonsense — those balances represent money STILL HELD
    // by the payment platforms, not money in the merchant's wallet.
    //
    // Returns the allowed account_type list for the «خصم من حساب»
    // dropdown. `null` means «show all groups» (used by bank_transfer
    // since transfers between platforms ARE legitimate).
    const allowedSourceAccountTypes = (op) => {
        const cashOut = [
            "advance_grant", "salary_settle", "custody_grant",
            "supplier_pay", "external_grant", "expense_record",
        ];
        const cashIn = [
            "custody_return", "external_collect",
        ];
        if (cashOut.includes(op) || cashIn.includes(op)) {
            return ["bank", "cash"];
        }
        // bank_transfer + any future settlement op → allow everything
        return null;
    };
    const [externals, setExternals] = useState([]);
    const [banks, setBanks] = useState([]);
    const [categories, setCategories] = useState([]);
    // Iter-191 — couriers list + per-courier open COD balance map.
    // Powers the «تسوية COD مع شركة شحن» operation: the merchant picks
    // a courier and the UI shows its current cod_receivable so they
    // can split the settlement into bank / shipping / cod_fee / other.
    const [couriers, setCouriers] = useState([]);
    const [courierCodBalances, setCourierCodBalances] = useState({});
    // Iter-191 — COD settlement legs (kept separate from the generic
    // `amount` field which doesn't make sense for multi-leg ops).
    const [codBankAmount,    setCodBankAmount]    = useState("");
    const [codShippingCost,  setCodShippingCost]  = useState("");
    const [codFee,           setCodFee]           = useState("");
    const [codFeeVat,        setCodFeeVat]        = useState("");
    const [codOtherFees,     setCodOtherFees]     = useState("");
    const [codOtherCategory, setCodOtherCategory] = useState("");
    const [result, setResult] = useState(null);

    // Form fields (used by various ops)
    const [entityId, setEntityId] = useState("");
    const [entityToId, setEntityToId] = useState("");
    const [amount, setAmount] = useState("");
    const [bankId, setBankId] = useState("");
    const [bankToId, setBankToId] = useState("");
    const [paymentDate, setPaymentDate] = useState(() => todaySA());
    const [notes, setNotes] = useState("");
    const [period, setPeriod] = useState(() => monthISO_SA());
    const [expCategory, setExpCategory] = useState("");
    const [invoiceNo, setInvoiceNo] = useState("");
    const [applyAdvances, setApplyAdvances] = useState(true);
    const [custodyItems, setCustodyItems] = useState([
        { expense_category: "", amount: "", notes: "" },
    ]);

    // Iter-250b · P1.5.q — "Pay from custody" support for expense_record.
    //   • paySource: which funding path the merchant picked for the
    //     current expense — "bank" (default) or "custody".
    //   • custodyEmployeeId: when paySource === "custody", which
    //     employee's open custody to debit.
    //   • custodySources: live list fetched from
    //     /accounting/custody/spendable-sources — respects the per-user
    //     permission filter (owner sees all positive-balance custodies;
    //     a regular linked employee sees only their own).
    const [paySource, setPaySource] = useState("bank");
    const [custodyEmployeeId, setCustodyEmployeeId] = useState("");
    const [custodySources, setCustodySources] = useState({
        loaded: false, can_spend_any: false, sources: [],
    });

    // Iter-182 — hidden transaction types loaded from /settings, plus
    // visibility-management modal state.
    const [hiddenTypes, setHiddenTypes] = useState([]);
    const [showVisibilityModal, setShowVisibilityModal] = useState(false);
    const [savingVisibility, setSavingVisibility] = useState(false);
    // Iter-184 — operation→accounts binding loaded from /settings.
    // Shape: { [op_type]: [account_id, ...] }. Empty / missing key
    // means «السماح للكل» (no UI filtering for that op).
    const [accountBindings, setAccountBindings] = useState({});
    // Iter-185 — Live (insufficient-funds-aware) balance map keyed by
    // account_id. Filled from /accounting/cash-accounts-with-balances
    // and refreshed after every successful submit so the UI keeps the
    // freeze state honest.
    const [accountLiveBalances, setAccountLiveBalances] = useState({});
    // Iter-185 — Selected employee's net balance card.
    //   net > 0  → company owes employee (green)
    //   net < 0  → employee owes company (red)
    const [employeeSummary, setEmployeeSummary] = useState(null);
    const [employeeSummaryLoading, setEmployeeSummaryLoading] = useState(false);
    // Iter-186 — Bulk custody balances map { employee_id: open_balance }.
    // Loaded from /accounting/employees/custody/open-balances. Used to
    // decorate the searchable picker and freeze 0-balance employees in
    // custody_return / custody_settle.
    const [custodyBalances, setCustodyBalances] = useState({});
    // Iter-188 — Golden-Rule override. The merchant must explicitly opt
    // in to record an advance for an employee who already has open
    // salary_payable (the system pushes them toward salary_settle
    // first). Reset whenever opType / entity / amount change so the
    // override is never silently reused.
    const [acknowledgePendingSalary, setAcknowledgePendingSalary] = useState(false);

    const visibleOpTypes = useMemo(
        () => OP_TYPES.filter(o => !hiddenTypes.includes(o.value)),
        [hiddenTypes],
    );

    // Financial-provider cards deep-link to the correct canonical form.
    // Only known operation codes are accepted; arbitrary query values never
    // change the form or reach an accounting endpoint.
    useEffect(() => {
        const requested = searchParams.get("operation") || "";
        if (OP_TYPES.some((row) => row.value === requested)) {
            setOpType(requested);
        }
    }, [searchParams]);

    useEffect(() => {
        let alive = true;
        (async () => {
            try {
                const r = await api.get("/settings");
                if (alive) {
                    setHiddenTypes(r.data?.hidden_transaction_types || []);
                    setAccountBindings(r.data?.operation_account_bindings || {});
                }
            } catch (_) { /* swallow — defaults to all visible */ }
        })();
        return () => { alive = false; };
    }, []);

    // Iter-188 — Reset the Golden-Rule override whenever the merchant
    // changes the operation, the employee, or the amount. We never
    // want the override to silently persist across edits.
    useEffect(() => { setAcknowledgePendingSalary(false); },
              [opType, entityId, amount]);
    const allowedAccountIds = useMemo(() => {
        const list = accountBindings?.[opType];
        if (!Array.isArray(list) || list.length === 0) return null; // no filter
        return new Set(list);
    }, [accountBindings, opType]);

    const filterAccounts = (list) => {
        if (!allowedAccountIds) return list;
        return list.filter((b) => allowedAccountIds.has(b.id));
    };

    const noAccountsAllowed = useMemo(() => {
        if (!opType) return false;
        if (!allowedAccountIds) return false;
        return banks.filter((b) => allowedAccountIds.has(b.id)).length === 0;
    }, [allowedAccountIds, banks, opType]);

    // Iter-250b · P1.5.k — Helper: is this account INELIGIBLE for an
    // internal transfer between accounts?
    //
    // Rejected:
    //   - account_type ∉ {bank, cash, payment_platform}
    //   - payment_platform whose provider is COD (الدفع عند الاستلام)
    //   - payment_platform whose provider is "تحويل بنكي" (bank_transfer)
    //   - Iter-250b · P1.5.L — BNPL wallets (Tamara/Tabby). Direct
    //     transfers onto BNPL accounts corrupt the canonical BNPL
    //     settlement bridge; the official BNPL Settlements screen is
    //     the only valid path. Salla is NOT blocked.
    const isInternalTransferIneligible = (b) => {
        if (!b) return true;
        const atype = b.account_type;
        if (!["bank", "cash", "payment_platform"].includes(atype)) {
            return true;
        }
        const name = (b.name || "").toLowerCase();
        const provider = (b.provider_name || "").toLowerCase();
        const npm = (b.normalized_payment_method || "").toLowerCase();
        const codRe = /الدفع\s*عند\s*الاستلام|cash[\s_-]*on[\s_-]*delivery|\bcod\b/i;
        const btRe = /تحويل\s*بنكي|bank[\s_-]*transfer/i;
        if (codRe.test(name) || codRe.test(provider) || npm === "cod" || npm === "cash_on_delivery") {
            return true;
        }
        if (btRe.test(name) || btRe.test(provider) || npm === "bank_transfer") {
            return true;
        }
        const bnplRe = /tamara|tabby|bnpl|تمارا|تابي/i;
        if (bnplRe.test(name) || bnplRe.test(provider) || bnplRe.test(npm)) {
            return true;
        }
        return false;
    };

    // The filtered bank list used by both source and destination dropdowns.
    const filteredBanks = useMemo(
        () => {
            const base = filterAccounts(banks);
            // For internal transfers (bank_transfer opType) we additionally
            // exclude COD and bank-transfer payment platforms because they
            // are NOT real transferable wallets — they belong to the
            // courier/COD settlement workflow, not the generic transfer.
            if (opType === "bank_transfer") {
                return base.filter((b) => !isInternalTransferIneligible(b));
            }
            return base;
        },
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [banks, allowedAccountIds, opType]
    );

    // Iter-185 — Load live cash balances on mount and after each submit.
    const reloadLiveBalances = async () => {
        try {
            const r = await api.get("/accounting/cash-accounts-with-balances");
            const map = {};
            for (const a of (r.data?.accounts || [])) {
                map[a.id] = Number(a.live_balance || 0);
            }
            setAccountLiveBalances(map);
        } catch (_) { /* swallow — feature degrades gracefully */ }
    };
    // Iter-186 — Load all-employees custody balances.
    const reloadCustodyBalances = async () => {
        try {
            const r = await api.get("/accounting/employees/custody/open-balances");
            const map = {};
            for (const row of (r.data?.rows || [])) {
                map[row.employee_id] = Number(row.open_balance || 0);
            }
            setCustodyBalances(map);
        } catch (_) { /* swallow */ }
    };
    // Iter-191 — Load couriers + their open COD balances.
    const reloadCouriers = async () => {
        try {
            const r = await api.get("/accounting/couriers/list");
            const items = r.data?.couriers || [];
            setCouriers(items);
            const map = {};
            for (const c of items) {
                map[c.id] = Number(c.cod_receivable || 0);
            }
            setCourierCodBalances(map);
        } catch (_) { /* swallow */ }
    };
    useEffect(() => { reloadLiveBalances(); }, []);
    useEffect(() => { reloadCustodyBalances(); }, []);
    useEffect(() => { reloadCouriers(); }, []);
    // Iter-246j — Refresh live bank balances whenever the merchant
    // switches op type so سداد مورد and فاتورة مورد always read the
    // SAME, latest SSOT value.  Without this, a stale `banks` /
    // `accountLiveBalances` cache from page-mount time could lag
    // behind a freshly posted invoice's cash leg.
    useEffect(() => {
        if (!opType) return;
        reloadLiveBalances();
    }, [opType]);

    // Iter-185 — Cash-out ops where the source account must have
    // enough funds. Mirrors backend `_enforce_sufficient_funds` calls.
    const isCashOutOp = (op) => [
        "advance_grant", "salary_settle", "custody_grant",
        "supplier_pay", "external_grant", "expense_record",
        "bank_transfer",
    ].includes(op);

    const amt = Number(amount) || 0;
    const bankPickerLocked = isCashOutOp(opType) && amt <= 0;

    const isInsufficient = (acc) => {
        if (!isCashOutOp(opType)) return false;
        if (amt <= 0) return false;
        const bal = accountLiveBalances[acc.id];
        if (bal === undefined) return false;   // unknown → don't freeze
        return bal < amt - 0.005;
    };

    // Iter-185 — Auto-load the selected employee's net summary card.
    useEffect(() => {
        if (!entityId || !["advance_grant", "salary_settle",
                            "salary_accrual", "custody_grant",
                            "custody_return", "custody_settle",
                            "custody_transfer"].includes(opType)) {
            setEmployeeSummary(null);
            return;
        }
        let alive = true;
        setEmployeeSummaryLoading(true);
        (async () => {
            try {
                const r = await api.get(
                    `/accounting/employees/${entityId}/summary-balance`);
                if (alive) setEmployeeSummary(r.data);
            } catch (_) {
                if (alive) setEmployeeSummary(null);
            } finally {
                if (alive) setEmployeeSummaryLoading(false);
            }
        })();
        return () => { alive = false; };
    }, [entityId, opType]);

    const toggleHidden = (value) => {
        setHiddenTypes((prev) =>
            prev.includes(value)
                ? prev.filter(v => v !== value)
                : [...prev, value]
        );
    };

    const saveVisibility = async () => {
        setSavingVisibility(true);
        try {
            // PUT /settings expects the FULL settings object — read first,
            // patch the field, write back. Avoids accidentally clearing
            // other settings.
            const cur = await api.get("/settings");
            await api.put("/settings", {
                ...cur.data,
                hidden_transaction_types: hiddenTypes,
            });
            toast.success("تم حفظ إعدادات الإظهار");
            setShowVisibilityModal(false);
        } catch (e) {
            toast.error("فشل الحفظ — راجع الكونسول");
            console.error(e);
        } finally {
            setSavingVisibility(false);
        }
    };

    // Iter-246h — Reloadable supplier list so the dropdown reflects
    // the latest outstanding_debt the moment a payment posts (or any
    // other ledger touch that affects supplier balances).
    const reloadSuppliers = async () => {
        try {
            const { data } = await api.get(
                "/accounting/suppliers/list");
            setSuppliers((data?.suppliers || []).map((s) => ({
                id: s.id,
                name: s.name,
                outstanding_debt: s.outstanding_debt || 0,
            })));
        } catch { /* silent — non-blocking refresh */ }
    };

    useEffect(() => { (async () => {
        try {
            const [emp, sup, ext, bnk, cat] = await Promise.all([
                api.get("/operating-expenses/salaries"),
                // Iter-246f — Unified supplier list (counterparties +
                // db.suppliers) ranked by outstanding_debt.  This is
                // the SSOT now used for both supplier_invoice picking
                // and supplier_pay; legacy /counterparties remains
                // available for other consumers.
                api.get("/accounting/suppliers/list"),
                api.get("/counterparties?limit=500"),
                api.get("/accounts?account_type=bank&limit=200"),
                api.get("/accounting/expense-categories"),
            ]);
            setEmployees((emp.data?.items || emp.data || []).filter(
                e => (e.category || "employee") === "employee"));
            // Map the «suppliers/list» response shape into the existing
            // {id,name} contract while preserving `outstanding_debt` so
            // the supplier_pay branch can render «الاسم — مستحق X ر.س».
            setSuppliers(
                (sup.data?.suppliers || []).map((s) => ({
                    id: s.id,
                    name: s.name,
                    outstanding_debt: s.outstanding_debt || 0,
                })),
            );
            setExternals((ext.data?.items || ext.data || []).filter(
                x => !["ad_account", "supplier", "courier"].includes(x.kind)));
            // Iter-170 — Fix duplicate accounts in dropdown:
            // Backend originally ignored ?type= (it expects ?account_type=).
            // Result: both queries below returned ALL accounts → merge
            // produced 2× every account in the "خصم من حساب" picker.
            // We now use account_type AND deduplicate by id as a defense
            // in depth. The accounts are also tagged for grouping.
            const bankItems = bnk.data?.items || bnk.data || [];
            const seen = new Set();
            const dedupe = (list) => list.filter((a) => {
                if (!a?.id || seen.has(a.id)) return false;
                seen.add(a.id);
                return true;
            });
            let allAccounts = dedupe(bankItems);
            try {
                const pp = await api.get("/accounts?account_type=payment_platform&limit=200");
                const ppItems = pp.data?.items || pp.data || [];
                allAccounts = [...allAccounts, ...dedupe(ppItems)];
            } catch (e) { /* optional */ }
            // Iter-250b · P1.5.k — Fetch cash accounts as well so the
            // «تحويل بين الحسابات» dropdowns can show الصناديق النقدية
            // (e.g. صندوق الرياض). Previously only bank + payment_platform
            // were loaded, hiding all cash funds from the picker.
            try {
                const ca = await api.get("/accounts?account_type=cash&limit=200");
                const caItems = ca.data?.items || ca.data || [];
                allAccounts = [...allAccounts, ...dedupe(caItems)];
            } catch (e) { /* optional */ }
            setBanks(allAccounts);
            setCategories(cat.data || []);
            if (cat.data?.length) setExpCategory(cat.data[0].code);
        } catch (e) {
            toast.error("فشل تحميل البيانات الأساسية");
        }
    })(); }, []);

    const resetForm = () => {
        setEntityId(""); setEntityToId(""); setAmount(""); setBankId(""); setBankToId("");
        setNotes(""); setInvoiceNo(""); setResult(null);
        setCustodyItems([{ expense_category: "", amount: "", notes: "" }]);
        // Iter-191 — COD settlement legs.
        setCodBankAmount(""); setCodShippingCost("");
        setCodFee(""); setCodFeeVat("");
        setCodOtherFees(""); setCodOtherCategory("");
        // Iter-250b · P1.5.q — reset pay-source picker so it doesn't
        // bleed across operation switches.
        setPaySource("bank"); setCustodyEmployeeId("");
    };

    useEffect(() => { resetForm(); }, [opType]);

    // Iter-250b · P1.5.q — Lazy-load spendable custody sources the
    // first time the merchant flips the toggle to "عهدة موظف". Cached
    // for the rest of the session — re-fetches whenever paySource
    // flips back from custody to bank and back again, to surface
    // freshly-granted custodies. We DO NOT pre-fetch on mount because
    // most operations don't need this list.
    useEffect(() => {
        // Iter-250b · P1.5.q + P1.5.y — Lazy-load spendable custody
        // sources for both `expense_record` and `supplier_pay` when
        // the merchant flips to «عهدة موظف».
        const eligible = ["expense_record", "supplier_pay"]
            .includes(opType);
        if (!eligible || paySource !== "custody") return;
        let alive = true;
        (async () => {
            try {
                const { data } = await api.get(
                    "/accounting/custody/spendable-sources");
                if (!alive) return;
                setCustodySources({
                    loaded: true,
                    can_spend_any: !!data.can_spend_any,
                    sources: Array.isArray(data.sources) ? data.sources : [],
                });
                if (!data.can_spend_any
                    && Array.isArray(data.sources)
                    && data.sources.length === 1) {
                    setCustodyEmployeeId(data.sources[0].employee_id);
                }
            } catch (e) {
                if (!alive) return;
                setCustodySources({
                    loaded: true, can_spend_any: false, sources: [],
                });
            }
        })();
        return () => { alive = false; };
    }, [opType, paySource]);

    // Iter-209 — Fetch the last 10 ledger txn-groups and surface them
    // in a compact table at the bottom. Optionally highlights the new
    // one we just posted. The highlight fades after 4 seconds.
    const loadRecentTxns = async (highlightId = null) => {
        setRecentLoading(true);
        try {
            // Pull a generous slice and group on the client — backend
            // doesn't currently expose a "group-by" endpoint.
            const { data } = await api.get("/ledger/entries",
                { params: { limit: 200 } });
            const items = Array.isArray(data) ? data
                : (data?.items || data?.entries || []);
            const groups = new Map();
            for (const e of items) {
                const gid = e.txn_group_id;
                if (!gid) continue;
                if (!groups.has(gid)) {
                    groups.set(gid, {
                        txn_group_id: gid,
                        posted_at: e.posted_at,
                        txn_type: (e.metadata && e.metadata.txn_type)
                            || e.entry_type,
                        notes: e.notes || (e.metadata && e.metadata.notes) || "",
                        // Iter-214 — creator + reverser names. Every leg
                        // in a group shares the same posted_by, so we
                        // capture the first non-empty name we see.
                        posted_by_name: e.posted_by_name || "",
                        reversed_by_name: e.reversed_by_name || "",
                        reversed_at: e.reversed_at || null,
                        legs: [],
                    });
                }
                const g = groups.get(gid);
                if (!g.posted_by_name && e.posted_by_name) {
                    g.posted_by_name = e.posted_by_name;
                }
                if (!g.reversed_by_name && e.reversed_by_name) {
                    g.reversed_by_name = e.reversed_by_name;
                    g.reversed_at = e.reversed_at || g.reversed_at;
                }
                g.legs.push({
                    entry_id: e.id,
                    side: e.side,
                    amount: Number(e.amount || 0),
                    entity_type: e.entity_type,
                    entity_id: e.entity_id,
                    sub_account: e.sub_account,
                    // Iter-250b · P1.5.g — surface entry_type + metadata.source
                    // so the UI can detect auto-seeded opening balances and
                    // relabel them as "رصيد افتتاحي مرحّل" instead of "تسوية".
                    entry_type: e.entry_type,
                    metadata_source: (e.metadata && e.metadata.source) || null,
                    status: e.status,
                    reversed_by_entry_id: e.reversed_by_entry_id || null,
                });
                if (e.posted_at && (!g.posted_at
                                     || e.posted_at > g.posted_at)) {
                    g.posted_at = e.posted_at;
                }
            }
            const list = Array.from(groups.values())
                .sort((a, b) => (b.posted_at || "").localeCompare(a.posted_at || ""))
                .slice(0, 10)
                .map((g) => ({
                    ...g,
                    total_debit: g.legs
                        .filter((l) => l.side === "debit")
                        .reduce((s, l) => s + l.amount, 0),
                }));
            setRecentTxns(list);
            if (highlightId) {
                setHighlightGroupId(highlightId);
                setTimeout(() => setHighlightGroupId(null), 4000);
            }
        } catch (_) { /* silent */ }
        finally { setRecentLoading(false); }
    };

    useEffect(() => { loadRecentTxns(); /* eslint-disable-next-line */ }, []);

    // Preview computation — show the entries that will be created
    const previewEntries = useMemo(() => {
        const amt = Number(amount) || 0;
        if (!opType || amt <= 0) return null;
        const empName = employees.find(x => x.id === entityId)?.name;
        const supName = suppliers.find(x => x.id === entityId)?.name;
        const extName = externals.find(x => x.id === entityId)?.name;
        const bankName = banks.find(x => x.id === bankId)?.name;
        const bankToName = banks.find(x => x.id === bankToId)?.name;
        const catName = categories.find(x => x.code === expCategory)?.name;

        const e = []; // { debit/credit account label, amount }
        switch (opType) {
            case "advance_grant":
                e.push({ side: "debit",  acc: `سلفة ${empName || ""}`, amt });
                e.push({ side: "credit", acc: bankName,    amt });
                break;
            case "salary_settle":
                e.push({ side: "debit",  acc: `راتب مستحق ${empName || ""}`, amt });
                e.push({ side: "credit", acc: bankName,    amt, note: "(قد تختلف القسمة الفعلية حسب الأرصدة)" });
                break;
            case "salary_accrual":
                e.push({ side: "debit",  acc: "مصروف رواتب",  amt });
                e.push({ side: "credit", acc: `راتب مستحق ${empName || ""}`, amt });
                break;
            case "custody_grant":
                e.push({ side: "debit",  acc: `عهدة ${empName || ""}`, amt });
                e.push({ side: "credit", acc: bankName,    amt });
                break;
            case "custody_return":
                e.push({ side: "debit",  acc: bankName,    amt });
                e.push({ side: "credit", acc: `عهدة ${empName || ""}`, amt });
                break;
            case "custody_settle": {
                const total = custodyItems.reduce((s, it) => s + (Number(it.amount) || 0), 0);
                for (const it of custodyItems) {
                    const cAmt = Number(it.amount) || 0;
                    if (cAmt > 0) {
                        const cName = categories.find(c => c.code === it.expense_category)?.name;
                        e.push({ side: "debit", acc: `مصروف: ${cName || it.expense_category}`, amt: cAmt });
                    }
                }
                if (total > 0) {
                    e.push({ side: "credit", acc: `عهدة ${empName || ""}`, amt: total });
                }
                break;
            }
            case "custody_transfer": {
                const toName = employees.find(x => x.id === entityToId)?.name;
                e.push({ side: "debit",  acc: `عهدة ${toName || ""}`,  amt });
                e.push({ side: "credit", acc: `عهدة ${empName || ""}`, amt });
                break;
            }
            case "supplier_invoice":
                e.push({ side: "debit",  acc: `مصروف: ${catName || ""}`, amt });
                e.push({ side: "credit", acc: `مورد ${supName || ""}`,    amt });
                break;
            case "supplier_pay":
                e.push({ side: "debit",  acc: `مورد ${supName || ""}`,    amt });
                e.push({ side: "credit", acc: bankName,    amt });
                break;
            case "external_grant":
                e.push({ side: "debit",  acc: `قرض/مستحق على ${extName || ""}`, amt });
                e.push({ side: "credit", acc: bankName,    amt });
                break;
            case "external_collect":
                e.push({ side: "debit",  acc: bankName,    amt });
                e.push({ side: "credit", acc: `قرض/مستحق على ${extName || ""}`, amt });
                break;
            case "expense_record": {
                e.push({ side: "debit",  acc: `مصروف: ${catName || ""}`, amt });
                // Iter-250b · P1.5.q — Credit leg follows the chosen
                // pay source.
                if (paySource === "custody") {
                    const sel = (custodySources.sources || []).find(
                        s => s.employee_id === custodyEmployeeId);
                    e.push({ side: "credit",
                             acc: `عهدة ${sel?.name || "(اختر موظفاً)"}`,
                             amt });
                } else {
                    e.push({ side: "credit", acc: bankName, amt });
                }
                break;
            }
            case "bank_transfer":
                e.push({ side: "debit",  acc: bankToName,  amt });
                e.push({ side: "credit", acc: bankName,    amt });
                break;
        }
        const debit = e.filter(x => x.side === "debit").reduce((s, x) => s + x.amt, 0);
        const credit = e.filter(x => x.side === "credit").reduce((s, x) => s + x.amt, 0);
        return { entries: e, debit, credit, balanced: Math.abs(debit - credit) < 0.01 };
    }, [opType, amount, entityId, entityToId, bankId, bankToId, expCategory, custodyItems,
        employees, suppliers, externals, banks, categories,
        paySource, custodyEmployeeId, custodySources]);

    const submit = async () => {
        const amt = Number(amount) || 0;
        // Iter-191 — `courier_cod_settle` doesn't use the generic
        // amount field; it has its own multi-leg validation below.
        if (opType !== "custody_settle"
            && opType !== "courier_cod_settle"
            && amt <= 0) {
            toast.error("أدخل مبلغاً صحيحاً"); return;
        }
        setBusy(true); setResult(null);
        try {
            let url = ""; let body = {};
            const common = { payment_date: paymentDate, notes };
            switch (opType) {
                case "advance_grant":
                    url = `/accounting/employees/${entityId}/advances`;
                    body = {
                        amount: amt, paid_from_account_id: bankId,
                        acknowledge_pending_salary: acknowledgePendingSalary,
                        ...common,
                    };
                    break;
                case "salary_settle":
                    url = `/accounting/employees/${entityId}/settle`;
                    body = { amount: amt, paid_from_account_id: bankId,
                             apply_open_advances: applyAdvances, ...common };
                    break;
                case "salary_accrual":
                    url = `/accounting/employees/${entityId}/salary-accrual`;
                    body = { amount: amt, period, notes };
                    break;
                case "custody_grant":
                    url = `/accounting/employees/${entityId}/custody`;
                    body = { amount: amt, paid_from_account_id: bankId, ...common };
                    break;
                case "custody_return":
                    url = `/accounting/employees/${entityId}/custody/return`;
                    body = { amount: amt, deposited_to_account_id: bankId, ...common };
                    break;
                case "custody_settle":
                    url = `/accounting/employees/${entityId}/custody/settle-with-receipts`;
                    body = {
                        items: custodyItems.filter(it => Number(it.amount) > 0)
                            .map(it => ({
                                expense_category: it.expense_category,
                                amount: Number(it.amount),
                                notes: it.notes || "",
                            })),
                        ...common,
                    };
                    break;
                case "custody_transfer":
                    if (!entityId || !entityToId) {
                        toast.error("اختر الموظف المحوِّل والمستلم");
                        setBusy(false); return;
                    }
                    if (entityId === entityToId) {
                        toast.error("لا يمكن النقل لنفس الموظف");
                        setBusy(false); return;
                    }
                    url = `/accounting/employees/custody/transfer`;
                    body = {
                        from_employee_id: entityId,
                        to_employee_id: entityToId,
                        amount: amt,
                        ...common,
                    };
                    break;
                case "supplier_invoice":
                    url = `/accounting/suppliers/${entityId}/invoice`;
                    body = { amount: amt, expense_category: expCategory,
                             invoice_no: invoiceNo, ...common };
                    break;
                case "supplier_pay":
                    url = `/accounting/suppliers/${entityId}/pay`;
                    // Iter-250b · P1.5.y — Two new dimensions:
                    //   • Custody as a pay source (mirrors expense_record)
                    //   • Over-payment routes the excess to
                    //     `supplier.advance` (دفعة مقدمة). The backend
                    //     splits & guards; the frontend only needs to
                    //     send `allow_advance: true`.
                    if (paySource === "custody") {
                        if (!custodyEmployeeId) {
                            toast.error("اختر موظفاً لديه رصيد عهدة");
                            setBusy(false); return;
                        }
                        const src = (custodySources.sources || []).find(
                            s => s.employee_id === custodyEmployeeId);
                        const available = src ? Number(src.available_balance) : 0;
                        if (amt > available + 0.001) {
                            toast.error(
                                `رصيد العهدة غير كافٍ. المتاح: ` +
                                `${available.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س`);
                            setBusy(false); return;
                        }
                        body = { amount: amt,
                                 custody_employee_id: custodyEmployeeId,
                                 allow_advance: true, ...common };
                    } else {
                        body = { amount: amt,
                                 paid_from_account_id: bankId,
                                 allow_advance: true, ...common };
                    }
                    break;
                case "external_grant":
                    url = `/accounting/external-persons/${entityId}/grant`;
                    body = { amount: amt, paid_from_account_id: bankId, ...common };
                    break;
                case "external_collect":
                    url = `/accounting/external-persons/${entityId}/collect`;
                    body = { amount: amt, deposited_to_account_id: bankId, ...common };
                    break;
                case "expense_record":
                    url = "/accounting/expenses";
                    // Iter-250b · P1.5.q — Branch on pay source: either
                    // a traditional bank/cash account OR an employee
                    // custody wallet. Exactly one is sent.
                    if (paySource === "custody") {
                        if (!custodyEmployeeId) {
                            toast.error("اختر موظفاً لديه رصيد عهدة");
                            setBusy(false); return;
                        }
                        const src = (custodySources.sources || []).find(
                            s => s.employee_id === custodyEmployeeId);
                        const available = src ? Number(src.available_balance) : 0;
                        if (amt > available + 0.001) {
                            toast.error(
                                `رصيد العهدة غير كافٍ. المتاح: ` +
                                `${available.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س`);
                            setBusy(false); return;
                        }
                        body = { amount: amt, expense_category: expCategory,
                                 custody_employee_id: custodyEmployeeId,
                                 ...common };
                    } else {
                        body = { amount: amt, expense_category: expCategory,
                                 paid_from_account_id: bankId, ...common };
                    }
                    break;
                case "bank_transfer":
                    url = "/accounting/bank-transfer";
                    body = { amount: amt, from_account_id: bankId,
                             to_account_id: bankToId, ...common };
                    break;
                case "courier_cod_settle": {
                    // Iter-191 — multi-leg COD settlement.
                    const bankAmtN  = Number(codBankAmount   || 0);
                    const shipAmtN  = Number(codShippingCost || 0);
                    const feeAmtN   = Number(codFee          || 0);
                    const feeVatAmtN = Number(codFeeVat      || 0);
                    const otherAmtN = Number(codOtherFees    || 0);
                    const total = bankAmtN + shipAmtN + feeAmtN + feeVatAmtN + otherAmtN;
                    if (!entityId) {
                        toast.error("اختر شركة الشحن"); setBusy(false); return;
                    }
                    if (total <= 0) {
                        toast.error("أدخل قيمة واحدة على الأقل (تحويل / شحن / رسوم)");
                        setBusy(false); return;
                    }
                    if (bankAmtN > 0 && !bankId) {
                        toast.error("اختر حساب الإيداع للمبلغ المحول");
                        setBusy(false); return;
                    }
                    if (otherAmtN > 0 && !codOtherCategory) {
                        toast.error("اختر فئة المصروف للرسوم الأخرى");
                        setBusy(false); return;
                    }
                    if (feeVatAmtN > 0 && feeAmtN <= 0) {
                        toast.error("أدخل عمولة COD قبل إدخال ضريبة العمولة");
                        setBusy(false); return;
                    }
                    const codBalNow = Number(courierCodBalances[entityId] || 0);
                    if (total > codBalNow + 0.005) {
                        toast.error("إجمالي التسوية يتجاوز الرصيد المتاح");
                        setBusy(false); return;
                    }
                    url = `/accounting/couriers/${entityId}/cod-settle`;
                    body = {
                        bank_amount:        bankAmtN > 0 ? bankAmtN : 0,
                        bank_account_id:    bankAmtN > 0 ? bankId   : null,
                        shipping_cost:      shipAmtN,
                        cod_fee:            feeAmtN,
                        cod_fee_vat:        feeVatAmtN,
                        other_fees:         otherAmtN,
                        other_fees_category: otherAmtN > 0 ? codOtherCategory : null,
                        ...common,
                    };
                    break;
                }
                default:
                    toast.error("اختر نوع العملية"); setBusy(false); return;
            }
            const { data } = await api.post(url, body);
            toast.success("تم اعتماد القيد بنجاح");
            setResult(data);
            // Iter-185 — refresh live balances so the UI freeze stays honest.
            reloadLiveBalances();
            // Iter-186 — refresh custody balances after every successful
            // ledger post (covers grant / return / settle / transfer).
            reloadCustodyBalances();
            // Iter-191 — refresh couriers' open COD after every post
            // that touches them (settlement, deposit, payment, charge).
            reloadCouriers();
            // Iter-246h — Refresh supplier list with fresh outstanding_debt
            // so the merchant can't accidentally re-pay a settled debt
            // by clicking the same supplier twice.
            if (opType === "supplier_pay" || opType === "supplier_invoice") {
                reloadSuppliers();
                if (opType === "supplier_pay") {
                    setEntityId("");   // reset the picker
                }
            }
            // Iter-209 — Clear the form fields so the merchant can post
            // a follow-up transaction without manually resetting, then
            // pull the last 10 transactions and highlight the new one
            // we just posted (green flash row in the bottom table).
            const newGroupId = data?.txn_group_id
                || data?.ledger_txn_group_id
                || (Array.isArray(data?.entries) && data.entries[0]?.txn_group_id)
                || null;
            // Reset form WITHOUT touching opType — user might want to
            // record another op of the same type immediately.
            setEntityId(""); setEntityToId(""); setAmount("");
            setBankId(""); setBankToId("");
            setNotes(""); setInvoiceNo("");
            setCustodyItems([{ expense_category: "", amount: "", notes: "" }]);
            setCodBankAmount(""); setCodShippingCost("");
            setCodFee(""); setCodFeeVat("");
            setCodOtherFees(""); setCodOtherCategory("");
            await loadRecentTxns(newGroupId);
        } catch (e) {
            const det = e.response?.data?.detail;
            // Iter-188 — Server-side Golden Rule (defense in depth): if
            // the merchant bypassed the in-page banner somehow (browser
            // restored state, etc.), we still get a friendly 409.
            if (e.response?.status === 409
                && det && typeof det === "object"
                && det.code === "PENDING_SALARY_BLOCK") {
                toast.error(det.message || "يفضّل تسجيلها كصرف راتب");
                return;
            }
            toast.error(typeof det === "string" ? det
                : (det?.message || det?.[0]?.msg || "فشل الاعتماد"));
        } finally { setBusy(false); }
    };

    const op = OP_TYPES.find(x => x.value === opType);
    // Iter-191 — `courier_cod_settle` is a brand-new shape: it owns
    // its own picker (courier), its own 4 amount fields, and its own
    // preview/submit path. The generic "amount + employee + bank"
    // scaffolding is disabled for it.
    const isCodSettle = opType === "courier_cod_settle";
    const needsEmployee = !isCodSettle && ["advance_grant","salary_settle","salary_accrual",
        "custody_grant","custody_return","custody_settle","custody_transfer"].includes(opType);
    const needsEmployeeTo = opType === "custody_transfer";
    const needsSupplier = ["supplier_invoice","supplier_pay"].includes(opType);
    const needsExternal = ["external_grant","external_collect"].includes(opType);
    // Iter-250b · P1.5.q + P1.5.y — `expense_record` AND `supplier_pay`
    // both skip the bank picker when paid from employee custody.
    const needsBank = !isCodSettle
        && !["salary_accrual","custody_settle","supplier_invoice","custody_transfer"].includes(opType)
        && !(["expense_record","supplier_pay"].includes(opType) && paySource === "custody");
    const needsBankTo = opType === "bank_transfer";
    const needsCategory = ["supplier_invoice","expense_record"].includes(opType);
    const needsCourier = isCodSettle;

    return (
        <div className="p-6 max-w-4xl mx-auto" data-testid="unified-entry-screen">
            <div className="bg-white rounded-2xl shadow-lg p-6">
                <div className="flex items-start justify-between gap-3 mb-1">
                    <h1 className="text-2xl font-extrabold text-slate-900">
                        ➕ حركة مالية جديدة
                    </h1>
                    <button
                        type="button"
                        onClick={() => setShowVisibilityModal(true)}
                        className="text-xs font-bold text-slate-600 hover:text-emerald-700 border border-slate-300 hover:border-emerald-400 rounded-lg px-3 py-1.5 transition-colors"
                        data-testid="unified-manage-visibility-btn"
                        title="إظهار/إخفاء أنواع العمليات"
                    >
                        ⚙️ إدارة الأنواع
                    </button>
                </div>
                <p className="text-sm text-slate-500 mb-6">
                    شاشة موحدة لكل العمليات المحاسبية. ينشئ النظام القيود المزدوجة تلقائياً.
                </p>

                <div className="mb-6 rounded-xl border border-sky-200 bg-sky-50 p-4 text-xs font-semibold leading-6 text-sky-900" data-testid="unified-provider-movement-policy">
                    <div className="font-extrabold">قاعدة التحويل والتسوية</div>
                    <div>
                        التحويل بين بنك أو صندوق هو نقل رصيد ولا يُعد إيرادًا أو مصروفًا.
                        تحويل سلة/تمارا/تابي/إمكان إلى البنك يُسجّل من كشف تسوية المزود،
                        وتحصيل شركة الشحن يُسجّل كتسوية COD؛ لذلك لا تُعامل هذه الحالات كتحويل عام.
                    </div>
                </div>

                {/* Op type selector */}
                <div className="mb-6">
                    <label className="block text-sm font-bold text-slate-700 mb-2">
                        نوع العملية:
                    </label>
                    <select value={opType} onChange={e => setOpType(e.target.value)}
                        className="w-full px-3 py-2.5 border-2 border-slate-300 rounded-lg text-sm font-bold focus:border-emerald-500 focus:outline-none"
                        data-testid="unified-op-type">
                        <option value="">— اختر نوع العملية —</option>
                        <optgroup label="موظفون">
                            {visibleOpTypes.filter(x => x.section === "employees").map(o =>
                                <option key={o.value} value={o.value}>{o.label}</option>)}
                        </optgroup>
                        <optgroup label="موردون">
                            {visibleOpTypes.filter(x => x.section === "suppliers").map(o =>
                                <option key={o.value} value={o.value}>{o.label}</option>)}
                        </optgroup>
                        <optgroup label="أشخاص خارجيون">
                            {visibleOpTypes.filter(x => x.section === "externals").map(o =>
                                <option key={o.value} value={o.value}>{o.label}</option>)}
                        </optgroup>
                        <optgroup label="عمليات عامة">
                            {visibleOpTypes.filter(x => x.section === "general").map(o =>
                                <option key={o.value} value={o.value}>{o.label}</option>)}
                        </optgroup>
                        <optgroup label="شركات الشحن">
                            {visibleOpTypes.filter(x => x.section === "shipping").map(o =>
                                <option key={o.value} value={o.value}>{o.label}</option>)}
                        </optgroup>
                    </select>
                </div>

                {opType && ITER245_OPS[opType] && (
                    <div className="border-t border-slate-200 pt-4">
                        <Iter245MovementForm
                            movementType={ITER245_OPS[opType]}
                            title={OP_TYPES.find(o => o.value === opType)?.label || ""}
                            onSaved={() => loadRecentTxns()}
                        />
                    </div>
                )}

                {opType && !ITER245_OPS[opType] && (
                    <div className="space-y-4 border-t border-slate-200 pt-4">
                        {/* Iter-186 — Searchable employee picker with
                            custody-aware decorations. Used for every
                            employee-bound operation. Freezes 0-balance
                            employees on return / settle since both
                            operations require an open custody to act on. */}
                        {needsEmployee && (
                            <EmployeePicker
                                employees={employees}
                                custodyBalances={custodyBalances}
                                selectedId={entityId}
                                onSelect={setEntityId}
                                freezeZeroCustody={["custody_return",
                                                    "custody_settle"].includes(opType)}
                                label={opType === "custody_transfer"
                                    ? "من الموظف (المحوِّل)" : "الموظف"}
                                testidSuffix=""
                            />
                        )}
                        {needsEmployeeTo && (
                            <>
                                <EmployeePicker
                                    employees={employees}
                                    custodyBalances={custodyBalances}
                                    selectedId={entityToId}
                                    onSelect={setEntityToId}
                                    label="إلى الموظف (المستلم)"
                                    testidSuffix="-to"
                                    excludeIds={entityId ? [entityId] : []}
                                />
                                <p className="text-[11px] text-slate-500 mt-1 leading-tight">
                                    💡 العملية تنقل رصيد العهدة من المحوِّل إلى المستلم بدون أي تأثير على البنك أو الصندوق.
                                </p>
                            </>
                        )}

                        {/* Iter-250b · P1.5.o — PREVIEW-only debug overlay.
                            Renders ONLY when the page is served from a
                            preview hostname (contains "preview" or
                            "localhost"). Production users never see it.
                            Shows the exact (id, name, monthly_amount)
                            of the currently selected employee so we
                            can verify the picker binding visually
                            during forensic regression. */}
                        {needsEmployee && entityId
                          && typeof window !== "undefined"
                          && (window.location.hostname.includes("preview")
                              || window.location.hostname === "localhost"
                              || window.location.hostname === "127.0.0.1") && (() => {
                            const selEmp = employees.find(e => e.id === entityId);
                            const selEmpTo = entityToId
                              ? employees.find(e => e.id === entityToId) : null;
                            return (
                                <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-[11px] font-mono leading-relaxed"
                                     data-testid="iter250b-employee-binding-debug">
                                    <div className="font-bold text-amber-900 mb-1">
                                        🔬 DEBUG (Preview only · P1.5.o)
                                    </div>
                                    <div className="text-amber-900">
                                        selected_employee_id: <span className="font-bold">{selEmp?.id || "—"}</span>
                                    </div>
                                    <div className="text-amber-900">
                                        selected_employee_name: <span className="font-bold">{selEmp?.name || "—"}</span>
                                    </div>
                                    <div className="text-amber-900">
                                        source_endpoint: /operating-expenses/salaries
                                    </div>
                                    <div className="text-amber-900">
                                        monthly_amount: {Number(selEmp?.monthly_amount || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                                    </div>
                                    {selEmpTo && (
                                        <>
                                            <div className="border-t border-amber-200 my-1"></div>
                                            <div className="text-amber-900">
                                                to_employee_id: <span className="font-bold">{selEmpTo.id}</span>
                                            </div>
                                            <div className="text-amber-900">
                                                to_employee_name: <span className="font-bold">{selEmpTo.name}</span>
                                            </div>
                                        </>
                                    )}
                                </div>
                            );
                        })()}

                        {/* Iter-185/186 — Employee context card.
                            For CUSTODY operations we show ONLY the open
                            custody balance (red when employee holds
                            money, green when the slate is clean). For
                            other employee ops we show the net amount
                            owed/owed-to. */}
                        {needsEmployee && entityId && (() => {
                            const isCustodyOp = ["custody_grant",
                                                  "custody_return",
                                                  "custody_settle",
                                                  "custody_transfer"].includes(opType);
                            if (isCustodyOp) {
                                const cust = Number(
                                    employeeSummary?.custody_open
                                    ?? custodyBalances[entityId]
                                    ?? 0);
                                const hasCustody = cust > 0.005;
                                return (
                                    <div
                                        className={`rounded-xl border-2 px-4 py-3 ${
                                            employeeSummaryLoading
                                                ? "border-slate-200 bg-slate-50"
                                                : hasCustody
                                                    ? "border-rose-300 bg-rose-50"
                                                    : "border-emerald-300 bg-emerald-50"
                                        }`}
                                        data-testid="unified-employee-summary-card"
                                    >
                                        <div className="text-[11px] text-slate-600 font-bold mb-0.5">
                                            {employeeSummaryLoading
                                                ? "جاري حساب رصيد العهدة…"
                                                : hasCustody
                                                    ? "❤️ إجمالي العهدة الحالية"
                                                    : "💚 لا توجد عهدة مفتوحة"}
                                        </div>
                                        <div className={`text-xl font-extrabold num ${
                                            hasCustody ? "text-rose-800" : "text-emerald-800"
                                        }`} data-testid="unified-employee-summary-amount">
                                            {cust.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س
                                        </div>
                                    </div>
                                );
                            }
                            const net = employeeSummary?.net_due_to_employee ?? 0;
                            return (
                                <div
                                    className={`rounded-xl border-2 px-4 py-3 ${
                                        employeeSummaryLoading
                                            ? "border-slate-200 bg-slate-50"
                                            : net > 0.005
                                                ? "border-emerald-300 bg-emerald-50"
                                                : net < -0.005
                                                    ? "border-rose-300 bg-rose-50"
                                                    : "border-slate-200 bg-slate-50"
                                    }`}
                                    data-testid="unified-employee-summary-card"
                                >
                                    <div className="text-[11px] text-slate-600 font-bold mb-0.5">
                                        {employeeSummaryLoading
                                            ? "جاري حساب رصيد الموظف…"
                                            : net > 0.005
                                                ? "💚 رصيد مستحق للموظف"
                                                : net < -0.005
                                                    ? "❤️ مبلغ على الموظف للشركة"
                                                    : "💼 رصيد الموظف"}
                                    </div>
                                    <div className={`text-xl font-extrabold num ${
                                        net > 0.005 ? "text-emerald-800"
                                            : net < -0.005 ? "text-rose-800"
                                                : "text-slate-700"
                                    }`} data-testid="unified-employee-summary-amount">
                                        {employeeSummary
                                            ? `${Math.abs(net).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س`
                                            : "—"}
                                    </div>
                                    {/* Iter-203 — show un-posted daily accrual breakdown */}
                                    {employeeSummary?.pending_accrual > 0.005 && (
                                        <div className="mt-1 text-[10px] text-amber-700 font-bold"
                                            data-testid="unified-employee-pending-accrual">
                                            +{Number(employeeSummary.pending_accrual).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س استحقاق يومي تراكمي حتى اليوم
                                        </div>
                                    )}
                                </div>
                            );
                        })()}

                        {/* Iter-188 — Golden-Rule suggestion banner.
                            When the merchant picks "سلفة موظف" on an
                            employee who already has open salary_payable,
                            we strongly nudge toward "صرف راتب" instead.
                            Two-click escape hatch is provided for the
                            rare genuine-advance case (personal loan,
                            etc). */}
                        {opType === "advance_grant"
                          && entityId
                          && (employeeSummary?.salary_payable ?? 0) > 0.005
                          && !acknowledgePendingSalary && (
                            <div className="bg-amber-50 border-2 border-amber-300 rounded-xl p-3"
                                 data-testid="unified-golden-rule-banner">
                                <div className="text-sm font-extrabold text-amber-900 mb-1">
                                    🟡 توصية محاسبية مهمة
                                </div>
                                <p className="text-[12px] text-amber-900 leading-relaxed mb-2.5">
                                    يوجد للموظف رصيد راتب مستحق قدره{" "}
                                    <strong className="num">
                                        {Number(employeeSummary.salary_payable).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س
                                    </strong>
                                    . القاعدة الذهبية: المبلغ المدفوع مقابل راتب مستحق
                                    بالفعل يُسجَّل كـ <strong>«صرف راتب»</strong>،
                                    وليس كسلفة. هذا يمنع تضخم حساب السلف بدون داعٍ.
                                </p>
                                <div className="flex flex-wrap gap-2">
                                    <button type="button"
                                        onClick={() => setOpType("salary_settle")}
                                        className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-extrabold rounded-lg"
                                        data-testid="golden-rule-switch-to-salary-btn">
                                        🔄 حوّل إلى «صرف راتب»
                                    </button>
                                    <button type="button"
                                        onClick={() => setAcknowledgePendingSalary(true)}
                                        className="px-3 py-1.5 bg-white border-2 border-amber-400 text-amber-900 text-xs font-extrabold rounded-lg hover:bg-amber-100"
                                        data-testid="golden-rule-acknowledge-btn">
                                        ✓ هي سلفة فعلاً — تابع
                                    </button>
                                </div>
                            </div>
                        )}
                        {opType === "advance_grant"
                          && entityId
                          && (employeeSummary?.salary_payable ?? 0) > 0.005
                          && acknowledgePendingSalary && (
                            <div className="bg-rose-50 border border-rose-200 rounded-lg px-3 py-2 text-[11px] text-rose-800 font-bold"
                                 data-testid="unified-golden-rule-ack">
                                ⚠️ تم تأكيدها كسلفة حقيقية رغم وجود راتب مستحق
                                ({Number(employeeSummary.salary_payable).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س).
                                <button type="button"
                                    onClick={() => setAcknowledgePendingSalary(false)}
                                    className="ms-2 underline text-rose-900">
                                    تراجع
                                </button>
                            </div>
                        )}
                        {needsSupplier && (
                            <div>
                                <label className="block text-sm font-bold text-slate-700 mb-1">المورد:</label>
                                <select value={entityId} onChange={e => setEntityId(e.target.value)}
                                    className="w-full px-3 py-2 border border-slate-300 rounded text-sm"
                                    data-testid="unified-entity-supplier">
                                    <option value="">— اختر —</option>
                                    {/* Iter-250b · P1.5.y — `supplier_pay` no
                                        longer filters out suppliers with
                                        zero debt: the merchant is now
                                        allowed to record a pre-advance
                                        (دفعة مقدمة) against any supplier. */}
                                    {suppliers.map((s) => {
                                        const debt = Number(s.outstanding_debt || 0);
                                        const label = debt > 0
                                          ? `${s.name} — مستحق ${debt.toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2})} ر.س`
                                          : `${s.name} — لا يوجد رصيد مستحق`;
                                        return (
                                          <option key={s.id} value={s.id}>{label}</option>
                                        );
                                    })}
                                </select>
                            </div>
                        )}

                        {/* Iter-250b · P1.5.y — Pay-source toggle for
                            «سداد مورد». Lets the merchant pay from a
                            bank/cash account OR from an employee's open
                            custody wallet. Only one source per txn. */}
                        {opType === "supplier_pay" && (
                            <div className="rounded-lg border border-slate-300 bg-slate-50 p-3"
                                 data-testid="suppay-paysource-block">
                                <label className="block text-sm font-bold text-slate-700 mb-2">
                                    طريقة السداد:
                                </label>
                                <div className="flex gap-2 mb-2">
                                    <button type="button"
                                        onClick={() => setPaySource("bank")}
                                        className={`flex-1 px-3 py-2 rounded text-sm font-bold border transition ${paySource === "bank"
                                            ? "bg-indigo-600 text-white border-indigo-600"
                                            : "bg-white text-slate-700 border-slate-300 hover:bg-slate-100"}`}
                                        data-testid="suppay-paysource-bank-btn">
                                        🏦 بنك / صندوق
                                    </button>
                                    <button type="button"
                                        onClick={() => setPaySource("custody")}
                                        className={`flex-1 px-3 py-2 rounded text-sm font-bold border transition ${paySource === "custody"
                                            ? "bg-amber-600 text-white border-amber-600"
                                            : "bg-white text-slate-700 border-slate-300 hover:bg-slate-100"}`}
                                        data-testid="suppay-paysource-custody-btn">
                                        👤 عهدة موظف
                                    </button>
                                </div>
                                {paySource === "custody" && (
                                    <div>
                                        {!custodySources.loaded ? (
                                            <div className="text-xs text-slate-500 py-2">⏳ يتم تحميل قائمة العهد...</div>
                                        ) : custodySources.sources.length === 0 ? (
                                            <div className="text-xs text-rose-700 bg-rose-50 border border-rose-200 rounded px-3 py-2 font-bold">
                                                {custodySources.can_spend_any
                                                    ? "🚫 لا يوجد موظفون لديهم رصيد عهدة موجب حالياً."
                                                    : "🚫 لا توجد عهدة مرتبطة بحسابك حالياً، أو رصيدك = 0."}
                                            </div>
                                        ) : (
                                            <>
                                                <label className="block text-xs font-bold text-slate-700 mb-1">اختر الموظف:</label>
                                                <select
                                                    value={custodyEmployeeId}
                                                    onChange={(e) => setCustodyEmployeeId(e.target.value)}
                                                    className="w-full px-3 py-2 border border-amber-300 bg-white rounded text-sm"
                                                    data-testid="suppay-custody-employee-select">
                                                    <option value="">— اختر موظفاً —</option>
                                                    {custodySources.sources.map(s => (
                                                        <option key={s.employee_id} value={s.employee_id}>
                                                            {s.name} · رصيد العهدة: {Number(s.available_balance).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س
                                                        </option>
                                                    ))}
                                                </select>
                                            </>
                                        )}
                                    </div>
                                )}
                            </div>
                        )}

                        {/* Iter-250b · P1.5.y — Pre-advance preview when
                            the amount exceeds the supplier's outstanding
                            debt. Shows the split BEFORE the merchant
                            clicks save. */}
                        {opType === "supplier_pay" && entityId && Number(amount) > 0 && (() => {
                            const sup = suppliers.find(s => s.id === entityId);
                            if (!sup) return null;
                            const outstanding = Number(sup.outstanding_debt || 0);
                            const amt = Number(amount);
                            if (amt <= outstanding + 0.001) return null;
                            const toDebt = Math.max(Math.min(amt, outstanding), 0);
                            const toAdvance = amt - toDebt;
                            const f = (n) => n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
                            return (
                                <div className="rounded-lg border border-amber-400 bg-amber-50 p-3 text-[12px]"
                                     data-testid="suppay-advance-warning">
                                    <div className="font-extrabold text-amber-900 mb-1">
                                        ⚠️ تنبيه: السداد أكبر من المستحق
                                    </div>
                                    <div className="text-amber-800 leading-relaxed">
                                        رصيد المورد المستحق ({f(outstanding)} ر.س) أقل من مبلغ السداد ({f(amt)} ر.س).
                                        سيتم تسجيل الفرق كـ <b>دفعة مقدمة للمورد</b> لصالحنا.
                                    </div>
                                    <div className="mt-2 pt-2 border-t border-amber-200 flex flex-wrap gap-3 font-mono">
                                        <span>سداد مستحق: <b className="text-emerald-700">{f(toDebt)}</b></span>
                                        <span>دفعة مقدمة: <b className="text-indigo-700">{f(toAdvance)}</b></span>
                                        <span>الإجمالي: <b>{f(amt)}</b></span>
                                    </div>
                                </div>
                            );
                        })()}
                        {needsExternal && (
                            <div>
                                <label className="block text-sm font-bold text-slate-700 mb-1">الشخص:</label>
                                <select value={entityId} onChange={e => setEntityId(e.target.value)}
                                    className="w-full px-3 py-2 border border-slate-300 rounded text-sm"
                                    data-testid="unified-entity-external">
                                    <option value="">— اختر —</option>
                                    {externals.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}
                                </select>
                            </div>
                        )}
                        {needsCategory && (
                            <div>
                                <label className="block text-sm font-bold text-slate-700 mb-1">فئة المصاريف:</label>
                                <select value={expCategory} onChange={e => setExpCategory(e.target.value)}
                                    className="w-full px-3 py-2 border border-slate-300 rounded text-sm"
                                    data-testid="unified-expense-category">
                                    {categories.map(c => <option key={c.code} value={c.code}>{c.name}</option>)}
                                </select>
                            </div>
                        )}

                        {/* ───────────────────────────────────────────
                            Iter-191 — COD settlement with a courier
                            ─────────────────────────────────────────── */}
                        {needsCourier && (() => {
                            const selectedCourier = couriers.find(c => c.id === entityId);
                            const codBal = Number(courierCodBalances[entityId] || 0);
                            const bankAmt    = Number(codBankAmount    || 0);
                            const shipAmt    = Number(codShippingCost  || 0);
                            const feeAmt     = Number(codFee           || 0);
                            const feeVatAmt  = Number(codFeeVat        || 0);
                            const otherAmt   = Number(codOtherFees     || 0);
                            const totalSettle = Math.round(
                                (bankAmt + shipAmt + feeAmt + feeVatAmt + otherAmt) * 100) / 100;
                            const remaining = Math.round((codBal - totalSettle) * 100) / 100;
                            const overSettled = totalSettle > codBal + 0.005;

                            const fmt = (v) => Number(v || 0).toLocaleString("en-US", {
                                minimumFractionDigits: 2, maximumFractionDigits: 2,
                            });

                            // Bank/cash only — courier settlement never lands
                            // in a payment_platform / courier / ads account.
                            const bankCashOnly = banks.filter(
                                b => b.account_type === "bank" || b.account_type === "cash");

                            return (<>
                                {/* Courier picker (search + COD badge) */}
                                <div>
                                    <label className="block text-sm font-bold text-slate-700 mb-1">
                                        شركة الشحن:
                                    </label>
                                    <select value={entityId}
                                        onChange={e => setEntityId(e.target.value)}
                                        className="w-full px-3 py-2 border border-slate-300 rounded text-sm"
                                        data-testid="unified-entity-courier">
                                        <option value="">— اختر —</option>
                                        {couriers.map(c => {
                                            const bal = Number(courierCodBalances[c.id] || 0);
                                            const hasCod = bal > 0.005;
                                            return (
                                                <option key={c.id} value={c.id}
                                                    disabled={!hasCod}
                                                    data-testid={`unified-courier-opt-${c.id}`}>
                                                    {c.name}
                                                    {hasCod
                                                        ? ` · COD ${fmt(bal)} ر.س`
                                                        : " · 🔒 لا يوجد COD مستحق"}
                                                </option>
                                            );
                                        })}
                                    </select>
                                    {couriers.length === 0 && (
                                        <p className="text-[11px] text-slate-500 mt-1">
                                            💡 لا توجد شركات شحن مسجلة بعد — أنشئها من
                                            «الإعدادات → شركات الشحن».
                                        </p>
                                    )}
                                </div>

                                {/* COD balance card */}
                                {entityId && (
                                    <div className={`rounded-xl border-2 px-4 py-3 ${
                                        codBal > 0.005
                                            ? "border-emerald-300 bg-emerald-50"
                                            : "border-rose-300 bg-rose-50"
                                    }`} data-testid="unified-courier-cod-card">
                                        <div className="text-[11px] text-slate-600 font-bold mb-0.5">
                                            {codBal > 0.005
                                                ? "💚 رصيد COD مستحق على الشركة"
                                                : "❤️ لا يوجد رصيد COD على هذه الشركة"}
                                        </div>
                                        <div className={`text-xl font-extrabold num ${
                                            codBal > 0.005 ? "text-emerald-800" : "text-rose-800"
                                        }`} data-testid="unified-courier-cod-amount">
                                            {fmt(codBal)} ر.س
                                        </div>
                                    </div>
                                )}

                                {/* Multi-leg amount fields */}
                                {entityId && codBal > 0.005 && (
                                    <div className="space-y-3">
                                        {/* Leg 1 — Bank/cash transfer */}
                                        <div className="border border-slate-200 rounded-xl p-3 bg-white">
                                            <div className="flex items-center justify-between mb-2">
                                                <div className="text-xs font-extrabold text-slate-700">
                                                    💰 المبلغ المحول
                                                </div>
                                                <HelpToggle testid="cod-bank-help">
                                                    <p className="font-bold text-slate-900">المبلغ الفعلي الذي وصل لحسابك من شركة الشحن.</p>
                                                    <p>هذا هو صافي COD = إجمالي التحصيل − (رسوم الشحن + رسوم COD + رسوم أخرى).</p>
                                                    <p className="text-amber-800 bg-amber-50 border border-amber-200 rounded p-2"><b>مثال:</b> شركة الشحن جمعت 10,000 ر.س، خصمت 1,200 شحن + 300 رسوم COD، حوّلت لك 8,500. أدخل هنا <b>8,500</b>.</p>
                                                </HelpToggle>
                                            </div>
                                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                                                <input type="number" step="0.01"
                                                    placeholder="مبلغ التحويل"
                                                    value={codBankAmount}
                                                    onChange={e => setCodBankAmount(e.target.value)}
                                                    className="px-3 py-2 border border-slate-300 rounded text-sm"
                                                    data-testid="cod-bank-amount" />
                                                <select value={bankId}
                                                    onChange={e => setBankId(e.target.value)}
                                                    className="px-3 py-2 border border-slate-300 rounded text-sm"
                                                    data-testid="cod-bank-account">
                                                    <option value="">— حساب الإيداع —</option>
                                                    <optgroup label="🏦 بنوك">
                                                        {bankCashOnly.filter(b => b.account_type === "bank").map(b =>
                                                            <option key={b.id} value={b.id}>{b.name}</option>)}
                                                    </optgroup>
                                                    {bankCashOnly.some(b => b.account_type === "cash") && (
                                                        <optgroup label="💵 صناديق نقدية">
                                                            {bankCashOnly.filter(b => b.account_type === "cash").map(b =>
                                                                <option key={b.id} value={b.id}>{b.name}</option>)}
                                                        </optgroup>
                                                    )}
                                                </select>
                                            </div>
                                            <p className="text-[11px] text-slate-500 mt-1">
                                                لا يُسمح بالإيداع في بوابات الدفع أو حسابات الإعلانات.
                                            </p>
                                        </div>

                                        {/* Leg 2 — Withheld shipping cost */}
                                        <div className="border border-slate-200 rounded-xl p-3 bg-white">
                                            <div className="flex items-center justify-between mb-2">
                                                <div className="text-xs font-extrabold text-slate-700">
                                                    📦 تكلفة الشحن المخصومة
                                                </div>
                                                <HelpToggle testid="cod-shipping-help">
                                                    <p className="font-bold text-slate-900">رسوم الشحن التي خصمتها الشركة قبل التحويل.</p>
                                                    <p>تُسجَّل كمصروف شحن (expense.shipping) في القيد الموحد، وتقلّل رصيد العهدة المفتوحة على شركة الشحن.</p>
                                                    <p className="text-emerald-800 bg-emerald-50 border border-emerald-200 rounded p-2"><b>مثال:</b> شركة الشحن قالت "خصمت 1,200 ر.س مصاريف شحن"، أدخل <b>1,200</b>. هذا يدخل تقرير "تكاليف الشحن" في الملخص التنفيذي.</p>
                                                </HelpToggle>
                                            </div>
                                            <input type="number" step="0.01"
                                                placeholder="0.00"
                                                value={codShippingCost}
                                                onChange={e => setCodShippingCost(e.target.value)}
                                                className="w-full px-3 py-2 border border-slate-300 rounded text-sm"
                                                data-testid="cod-shipping-cost" />
                                        </div>

                                        {/* Leg 3 — Withheld COD fee */}
                                        <div className="border border-slate-200 rounded-xl p-3 bg-white">
                                            <div className="flex items-center justify-between mb-2">
                                                <div className="text-xs font-extrabold text-slate-700">
                                                    💳 رسوم COD المخصومة
                                                </div>
                                                <HelpToggle testid="cod-fee-help">
                                                    <p className="font-bold text-slate-900">رسوم خدمة التحصيل (Cash-On-Delivery fees).</p>
                                                    <p>عمولة شركة الشحن على عملية التحصيل النقدي من العميل (غير رسوم الشحن العادية).</p>
                                                    <p className="text-sky-800 bg-sky-50 border border-sky-200 rounded p-2"><b>الفرق عن "تكلفة الشحن":</b> الشحن = نقل الطرد. رسوم COD = تحصيل النقد من العميل وتسليمه لك. شركات كثيرة تفصلهما في كشف الحساب.</p>
                                                </HelpToggle>
                                            </div>
                                            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                                                <label className="text-[11px] font-bold text-slate-600">
                                                    العمولة قبل الضريبة
                                                    <input type="number" step="0.01"
                                                        placeholder="0.00"
                                                        value={codFee}
                                                        onChange={e => setCodFee(e.target.value)}
                                                        className="mt-1 w-full px-3 py-2 border border-slate-300 rounded text-sm"
                                                        data-testid="cod-fee" />
                                                </label>
                                                <label className="text-[11px] font-bold text-slate-600">
                                                    ضريبة العمولة
                                                    <input type="number" step="0.01"
                                                        placeholder="0.00"
                                                        value={codFeeVat}
                                                        onChange={e => setCodFeeVat(e.target.value)}
                                                        className="mt-1 w-full px-3 py-2 border border-slate-300 rounded text-sm"
                                                        data-testid="cod-fee-vat" />
                                                </label>
                                            </div>
                                        </div>

                                        {/* Leg 4 — Other fees + category */}
                                        <div className="border border-slate-200 rounded-xl p-3 bg-white">
                                            <div className="flex items-center justify-between mb-2">
                                                <div className="text-xs font-extrabold text-slate-700">
                                                    🧾 رسوم أخرى (اختياري)
                                                </div>
                                                <HelpToggle testid="cod-other-help">
                                                    <p className="font-bold text-slate-900">أي خصومات إضافية لا تنتمي للشحن أو رسوم COD.</p>
                                                    <p>اختر فئة المصروف من القائمة (مثل: مرتجعات، تأمين، خصومات تجارية) ليُسجَّل في الفئة الصحيحة بالمركز المالي.</p>
                                                    <p className="text-amber-800 bg-amber-50 border border-amber-200 rounded p-2"><b>أمثلة:</b> قيمة طرد مفقود، رسم تأمين، عمولة استرجاع، خصم تجاري متفق عليه.</p>
                                                </HelpToggle>
                                            </div>
                                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                                                <input type="number" step="0.01"
                                                    placeholder="0.00"
                                                    value={codOtherFees}
                                                    onChange={e => setCodOtherFees(e.target.value)}
                                                    className="px-3 py-2 border border-slate-300 rounded text-sm"
                                                    data-testid="cod-other-fees" />
                                                <select value={codOtherCategory}
                                                    onChange={e => setCodOtherCategory(e.target.value)}
                                                    disabled={otherAmt <= 0}
                                                    className={`px-3 py-2 border border-slate-300 rounded text-sm ${
                                                        otherAmt <= 0 ? "bg-slate-100 text-slate-400" : ""
                                                    }`}
                                                    data-testid="cod-other-category">
                                                    <option value="">— فئة المصروف —</option>
                                                    {categories.map(c =>
                                                        <option key={c.code} value={c.code}>{c.name}</option>)}
                                                </select>
                                            </div>
                                        </div>

                                        {/* Live preview block */}
                                        <div className={`rounded-xl border-2 p-3 ${
                                            overSettled
                                                ? "border-rose-300 bg-rose-50"
                                                : "border-emerald-200 bg-emerald-50"
                                        }`} data-testid="cod-preview-block">
                                            <div className="text-xs font-extrabold text-slate-700 mb-2">
                                                📊 ملخص التسوية
                                            </div>
                                            <div className="grid grid-cols-2 gap-1 text-[12px]">
                                                <div className="text-slate-600">الرصيد الحالي:</div>
                                                <div className="text-left font-bold num">{fmt(codBal)} ر.س</div>
                                                <div className="text-slate-600">تحويل للبنك:</div>
                                                <div className="text-left font-bold num">{fmt(bankAmt)} ر.س</div>
                                                <div className="text-slate-600">تكلفة شحن:</div>
                                                <div className="text-left font-bold num">{fmt(shipAmt)} ر.س</div>
                                                <div className="text-slate-600">رسوم COD:</div>
                                                <div className="text-left font-bold num">{fmt(feeAmt)} ر.س</div>
                                                <div className="text-slate-600">ضريبة رسوم COD:</div>
                                                <div className="text-left font-bold num">{fmt(feeVatAmt)} ر.س</div>
                                                <div className="text-slate-600">رسوم أخرى:</div>
                                                <div className="text-left font-bold num">{fmt(otherAmt)} ر.س</div>
                                                <div className="font-extrabold text-slate-800 border-t border-slate-300 pt-1 mt-0.5">
                                                    إجمالي التسوية:
                                                </div>
                                                <div className="text-left font-extrabold num border-t border-slate-300 pt-1 mt-0.5"
                                                     data-testid="cod-total-settle">
                                                    {fmt(totalSettle)} ر.س
                                                </div>
                                                <div className={`font-extrabold ${
                                                    overSettled ? "text-rose-800"
                                                        : remaining < 0.005 ? "text-emerald-800"
                                                            : "text-amber-800"
                                                }`}>
                                                    الرصيد المتبقي بعد العملية:
                                                </div>
                                                <div className={`text-left font-extrabold num ${
                                                    overSettled ? "text-rose-800"
                                                        : remaining < 0.005 ? "text-emerald-800"
                                                            : "text-amber-800"
                                                }`} data-testid="cod-remaining">
                                                    {fmt(remaining)} ر.س
                                                </div>
                                            </div>
                                            {overSettled && (
                                                <div className="mt-2 text-[11px] text-rose-700 font-bold"
                                                     data-testid="cod-overflow-warning">
                                                    ⚠️ إجمالي التسوية يتجاوز رصيد COD المتاح —
                                                    عدّل القيم قبل الحفظ.
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                )}
                            </>);
                        })()}

                        {/* Amount (or items for custody settle / multi-leg for cod settle) */}
                        {opType !== "custody_settle" && !isCodSettle && (
                            <div>
                                <label className="block text-sm font-bold text-slate-700 mb-1">المبلغ (ر.س):</label>
                                <input type="number" step="0.01" value={amount}
                                    onChange={e => setAmount(e.target.value)}
                                    className="w-full px-3 py-2 border border-slate-300 rounded text-sm"
                                    data-testid="unified-amount" />
                            </div>
                        )}

                        {/* Bank */}
                        {needsBank && (
                            <div>
                                <label className="block text-sm font-bold text-slate-700 mb-1">
                                    {opType === "custody_return" || opType === "external_collect"
                                        ? "إيداع في حساب:"
                                        : opType === "bank_transfer" ? "من حساب:" : "خصم من حساب:"}
                                </label>
                                {/* Iter-173 — smart filter; show a hint when restricted */}
                                {allowedSourceAccountTypes(opType) && (
                                    <div className="text-[11px] text-slate-500 mb-1 leading-tight">
                                        💡 هذه العملية صرف نقدي حقيقي — متاحة فقط من البنوك والصندوق
                                        (لا تظهر بوابات الدفع ولا الـ COD لأن أرصدتها مُحتجَزة لدى المنصة).
                                    </div>
                                )}
                                {/* Iter-184 — show inline warning when the
                                    binding configured in Settings would block
                                    the picker entirely. */}
                                {noAccountsAllowed && (
                                    <div className="mb-2 text-xs text-rose-700 bg-rose-50 border border-rose-200 rounded-lg px-3 py-2 font-bold"
                                         data-testid="unified-bank-blocked">
                                        ⚠️ لا يوجد حساب مرتبط بهذه العملية في الإعدادات.
                                        أضِف حساباً واحداً على الأقل من «الإعدادات → ربط العمليات بالحسابات»
                                        ثم أعد المحاولة.
                                    </div>
                                )}
                                {/* Iter-185 — Lock the picker until the
                                    merchant enters an amount. */}
                                {bankPickerLocked && (
                                    <div className="mb-2 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 font-bold"
                                         data-testid="unified-bank-locked">
                                        🔒 أدخل مبلغ العملية أولاً لإظهار الحسابات المتاحة وفلترة غير الكافية منها.
                                    </div>
                                )}
                                <select value={bankId} onChange={e => setBankId(e.target.value)}
                                    disabled={bankPickerLocked}
                                    className={`w-full px-3 py-2 border border-slate-300 rounded text-sm ${bankPickerLocked ? "bg-slate-100 cursor-not-allowed text-slate-400" : ""}`}
                                    data-testid="unified-bank">
                                    <option value="">— اختر —</option>
                                    <optgroup label="🏦 الحسابات البنكية">
                                        {filteredBanks.filter(b => b.account_type === "bank").map(b => {
                                            const insufficient = isInsufficient(b);
                                            const bal = accountLiveBalances[b.id];
                                            return (
                                                <option key={b.id} value={b.id}
                                                    disabled={insufficient}
                                                    data-testid={`unified-bank-opt-${b.id}`}>
                                                    {b.name}
                                                    {bal !== undefined ? ` · ${bal.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س` : ""}
                                                    {insufficient ? " · 🚫 مجمد — الرصيد غير كافٍ" : " · بنك"}
                                                </option>
                                            );
                                        })}
                                    </optgroup>
                                    {filteredBanks.some(b => b.account_type === "cash") && (
                                        <optgroup label="💵 الصندوق النقدي">
                                            {filteredBanks.filter(b => b.account_type === "cash").map(b => {
                                                const insufficient = isInsufficient(b);
                                                const bal = accountLiveBalances[b.id];
                                                return (
                                                    <option key={b.id} value={b.id}
                                                        disabled={insufficient}>
                                                        {b.name}
                                                        {bal !== undefined ? ` · ${bal.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س` : ""}
                                                        {insufficient ? " · 🚫 مجمد — الرصيد غير كافٍ" : " · صندوق"}
                                                    </option>
                                                );
                                            })}
                                        </optgroup>
                                    )}
                                    {(!allowedSourceAccountTypes(opType)) && (
                                        <>
                                            <optgroup label="💳 بوابات الدفع">
                                                {filteredBanks.filter(b => b.account_type === "payment_platform" && !/الدفع\s*عند\s*الاستلام|cash[\s_-]*on[\s_-]*delivery|COD/i.test(b.name || "")).map(b => {
                                                    const insufficient = isInsufficient(b);
                                                    const bal = accountLiveBalances[b.id];
                                                    return (
                                                        <option key={b.id} value={b.id}
                                                            disabled={insufficient}>
                                                            {b.name}
                                                            {bal !== undefined ? ` · ${bal.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س` : ""}
                                                            {insufficient ? " · 🚫 مجمد — الرصيد غير كافٍ" : " · بوابة دفع"}
                                                        </option>
                                                    );
                                                })}
                                            </optgroup>
                                            {filteredBanks.some(b => b.account_type === "payment_platform" && /الدفع\s*عند\s*الاستلام|cash[\s_-]*on[\s_-]*delivery|COD/i.test(b.name || "")) && (
                                                <optgroup label="💵 الدفع عند الاستلام">
                                                    {filteredBanks.filter(b => b.account_type === "payment_platform" && /الدفع\s*عند\s*الاستلام|cash[\s_-]*on[\s_-]*delivery|COD/i.test(b.name || "")).map(b => {
                                                        const insufficient = isInsufficient(b);
                                                        const bal = accountLiveBalances[b.id];
                                                        return (
                                                            <option key={b.id} value={b.id}
                                                                disabled={insufficient}>
                                                                {b.name}
                                                                {bal !== undefined ? ` · ${bal.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س` : ""}
                                                                {insufficient ? " · 🚫 مجمد — الرصيد غير كافٍ" : " · COD"}
                                                            </option>
                                                        );
                                                    })}
                                                </optgroup>
                                            )}
                                            {filteredBanks.some(b => b.account_type === "courier") && (
                                                <optgroup label="📦 شركات الشحن">
                                                    {filteredBanks.filter(b => b.account_type === "courier").map(b => {
                                                        const insufficient = isInsufficient(b);
                                                        const bal = accountLiveBalances[b.id];
                                                        return (
                                                            <option key={b.id} value={b.id}
                                                                disabled={insufficient}>
                                                                {b.name}
                                                                {bal !== undefined ? ` · ${bal.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س` : ""}
                                                                {insufficient ? " · 🚫 مجمد — الرصيد غير كافٍ" : " · شركة شحن"}
                                                            </option>
                                                        );
                                                    })}
                                                </optgroup>
                                            )}
                                            {filteredBanks.some(b => b.account_type && !["bank","cash","payment_platform","courier"].includes(b.account_type)) && (
                                                <optgroup label="📁 أخرى">
                                                    {filteredBanks.filter(b => b.account_type && !["bank","cash","payment_platform","courier"].includes(b.account_type)).map(b => {
                                                        const insufficient = isInsufficient(b);
                                                        const bal = accountLiveBalances[b.id];
                                                        return (
                                                            <option key={b.id} value={b.id}
                                                                disabled={insufficient}>
                                                                {b.name}
                                                                {bal !== undefined ? ` · ${bal.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س` : ""}
                                                                {insufficient ? " · 🚫 مجمد — الرصيد غير كافٍ" : " · " + b.account_type}
                                                            </option>
                                                        );
                                                    })}
                                                </optgroup>
                                            )}
                                        </>
                                    )}
                                </select>
                            </div>
                        )}
                        {needsBankTo && (
                            <div>
                                <label className="block text-sm font-bold text-slate-700 mb-1">إلى حساب:</label>
                                <select value={bankToId} onChange={e => setBankToId(e.target.value)}
                                    className="w-full px-3 py-2 border border-slate-300 rounded text-sm"
                                    data-testid="unified-bank-to">
                                    <option value="">— اختر —</option>
                                    <optgroup label="🏦 الحسابات البنكية">
                                        {filteredBanks.filter(b => b.account_type === "bank" && b.id !== bankId).map(b =>
                                            <option key={b.id} value={b.id}>{b.name} · بنك</option>)}
                                    </optgroup>
                                    {/* Iter-250b · P1.5.f — Include cash accounts as a valid transfer destination */}
                                    {filteredBanks.some(b => b.account_type === "cash" && b.id !== bankId) && (
                                        <optgroup label="💵 الصندوق النقدي">
                                            {filteredBanks.filter(b => b.account_type === "cash" && b.id !== bankId).map(b =>
                                                <option key={b.id} value={b.id} data-testid={`unified-bank-to-opt-${b.id}`}>{b.name} · صندوق</option>)}
                                        </optgroup>
                                    )}
                                    <optgroup label="💳 بوابات الدفع">
                                        {filteredBanks.filter(b => b.account_type === "payment_platform" && b.id !== bankId && !/الدفع\s*عند\s*الاستلام|cash[\s_-]*on[\s_-]*delivery|COD/i.test(b.name || "")).map(b =>
                                            <option key={b.id} value={b.id}>{b.name} · بوابة دفع</option>)}
                                    </optgroup>
                                    {filteredBanks.some(b => b.account_type === "payment_platform" && b.id !== bankId && /الدفع\s*عند\s*الاستلام|cash[\s_-]*on[\s_-]*delivery|COD/i.test(b.name || "")) && (
                                        <optgroup label="💵 الدفع عند الاستلام">
                                            {filteredBanks.filter(b => b.account_type === "payment_platform" && b.id !== bankId && /الدفع\s*عند\s*الاستلام|cash[\s_-]*on[\s_-]*delivery|COD/i.test(b.name || "")).map(b =>
                                                <option key={b.id} value={b.id}>{b.name} · COD</option>)}
                                        </optgroup>
                                    )}
                                </select>
                            </div>
                        )}

                        {/* Salary accrual: period */}
                        {opType === "salary_accrual" && (
                            <div>
                                <label className="block text-sm font-bold text-slate-700 mb-1">الفترة (YYYY-MM):</label>
                                <input type="month" value={period}
                                    onChange={e => setPeriod(e.target.value)}
                                    className="w-full px-3 py-2 border border-slate-300 rounded text-sm"
                                    data-testid="unified-period" />
                            </div>
                        )}

                        {/* Custody receipts */}
                        {opType === "custody_settle" && (
                            <div>
                                <label className="block text-sm font-bold text-slate-700 mb-2">بنود الفواتير:</label>
                                {custodyItems.map((it, idx) => (
                                    <div key={idx} className="flex gap-2 items-start mb-2"
                                         data-testid={`unified-custody-item-${idx}`}>
                                        <select value={it.expense_category}
                                            onChange={e => {
                                                const copy = [...custodyItems];
                                                copy[idx].expense_category = e.target.value;
                                                setCustodyItems(copy);
                                            }}
                                            className="px-2 py-1.5 border border-slate-300 rounded text-xs flex-1">
                                            <option value="">— الفئة —</option>
                                            {categories.map(c => <option key={c.code} value={c.code}>{c.name}</option>)}
                                        </select>
                                        <input type="number" step="0.01" placeholder="المبلغ"
                                            value={it.amount}
                                            onChange={e => {
                                                const copy = [...custodyItems];
                                                copy[idx].amount = e.target.value;
                                                setCustodyItems(copy);
                                            }}
                                            className="w-28 px-2 py-1.5 border border-slate-300 rounded text-xs" />
                                        <input type="text" placeholder="ملاحظات"
                                            value={it.notes}
                                            onChange={e => {
                                                const copy = [...custodyItems];
                                                copy[idx].notes = e.target.value;
                                                setCustodyItems(copy);
                                            }}
                                            className="flex-1 px-2 py-1.5 border border-slate-300 rounded text-xs" />
                                        {custodyItems.length > 1 && (
                                            <button onClick={() => setCustodyItems(
                                                custodyItems.filter((_, i) => i !== idx))}
                                                className="text-rose-600 hover:text-rose-800 text-sm px-2">×</button>
                                        )}
                                    </div>
                                ))}
                                <button onClick={() => setCustodyItems(
                                    [...custodyItems, { expense_category: "", amount: "", notes: "" }])}
                                    className="text-xs text-emerald-700 hover:text-emerald-900 font-bold"
                                    data-testid="unified-add-custody-item">
                                    + إضافة بند
                                </button>
                            </div>
                        )}

                        {opType === "supplier_invoice" && (
                            <div>
                                <label className="block text-sm font-bold text-slate-700 mb-1">رقم الفاتورة (اختياري):</label>
                                <input type="text" value={invoiceNo}
                                    onChange={e => setInvoiceNo(e.target.value)}
                                    className="w-full px-3 py-2 border border-slate-300 rounded text-sm" />
                            </div>
                        )}

                        {opType === "salary_settle" && (
                            <div className="bg-slate-50 border border-slate-200 rounded-lg p-3">
                                <div className="flex items-center justify-between gap-2 flex-wrap">
                                    <label className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer">
                                        <input type="checkbox" checked={applyAdvances}
                                            onChange={e => setApplyAdvances(e.target.checked)}
                                            data-testid="unified-apply-advances" />
                                        خصم السلف المفتوحة تلقائياً
                                    </label>
                                    <HelpToggle testid="unified-apply-advances-help">
                                        <p className="font-bold text-slate-900">
                                            ما الذي يفعله هذا الخيار؟
                                        </p>
                                        <p>
                                            يقفل تلقائياً أي سلفة مفتوحة على الموظف مقابل راتبه المستحق
                                            في نفس العملية — بدل تسجيل عمليتين منفصلتين.
                                        </p>
                                        <div className="bg-white border border-slate-200 rounded p-2">
                                            <p className="font-bold text-slate-900 mb-1">مثال:</p>
                                            <p>راتب مستحق = 3,000 · سلفة مفتوحة = 500 · تدفع كاش = 2,000</p>
                                            <ul className="mt-1 space-y-0.5 pr-3 list-disc marker:text-emerald-500">
                                                <li><b className="text-emerald-700">مع الخيار:</b> البنك −2,000 · الراتب المستحق المتبقي = 500 · السلفة = <b>صفر (أُقفلت)</b></li>
                                                <li><b className="text-rose-700">بدون الخيار:</b> البنك −2,000 · الراتب المستحق المتبقي = 1,000 · السلفة <b>تظل 500 مفتوحة</b></li>
                                            </ul>
                                        </div>
                                        <div className="bg-amber-50 border border-amber-200 rounded p-2">
                                            <p className="font-bold text-amber-900 mb-1">💡 متى تستخدمه؟</p>
                                            <ul className="space-y-0.5 pr-3 list-disc marker:text-amber-500">
                                                <li>عند تصفية حسابات الموظف نهاية الشهر.</li>
                                                <li>عندما تريد استرداد السلفة من راتب هذا الشهر.</li>
                                                <li>أطفئه إذا الموظف لم يوافق على الخصم أو تريد إبقاء السلفة مفتوحة.</li>
                                            </ul>
                                        </div>
                                        <p className="text-[10px] text-slate-500">
                                            ملاحظة: لو دفعت <b>أكثر</b> من المستحق، يُسجَّل الفائض كسلفة جديدة على الموظف تلقائياً.
                                        </p>
                                    </HelpToggle>
                                </div>
                            </div>
                        )}

                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <label className="block text-sm font-bold text-slate-700 mb-1">التاريخ:</label>
                                <input type="date" value={paymentDate}
                                    onChange={e => setPaymentDate(e.target.value)}
                                    className="w-full px-3 py-2 border border-slate-300 rounded text-sm" />
                            </div>
                            <div>
                                <label className="block text-sm font-bold text-slate-700 mb-1">ملاحظات:</label>
                                <input type="text" value={notes}
                                    onChange={e => setNotes(e.target.value)}
                                    placeholder="اختياري"
                                    className="w-full px-3 py-2 border border-slate-300 rounded text-sm" />
                            </div>
                        </div>

                        {/* Preview */}
                        {previewEntries && previewEntries.entries.length > 0 && (
                            <div className="bg-slate-50 border-2 border-slate-300 rounded-lg p-4 mt-4"
                                 data-testid="unified-preview">
                                <div className="text-sm font-bold text-slate-700 mb-2">
                                    📋 معاينة القيود المحاسبية:
                                </div>
                                <table className="w-full text-xs">
                                    <thead>
                                        <tr className="text-slate-500 border-b border-slate-200">
                                            <th className="text-right py-1">الجانب</th>
                                            <th className="text-right py-1">الحساب</th>
                                            <th className="text-left py-1">المبلغ</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {previewEntries.entries.map((e, i) => (
                                            <tr key={i} className="border-b border-slate-100">
                                                <td className={`py-1 font-bold ${e.side === "debit" ? "text-emerald-700" : "text-rose-700"}`}>
                                                    {e.side === "debit" ? "مدين" : "دائن"}
                                                </td>
                                                <td className="py-1">{e.acc}</td>
                                                <td className="text-left py-1 num font-bold">
                                                    {e.amt.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                                                </td>
                                            </tr>
                                        ))}
                                        <tr className="font-bold border-t-2 border-slate-300">
                                            <td colSpan="2" className="py-1 text-left">الإجمالي:</td>
                                            <td className={`py-1 text-left num ${previewEntries.balanced ? "text-emerald-700" : "text-rose-700"}`}>
                                                {previewEntries.balanced ? "متوازن ✓" : "غير متوازن ✗"}
                                            </td>
                                        </tr>
                                    </tbody>
                                </table>
                            </div>
                        )}

                        <div className="flex gap-2 pt-2">
                            <button onClick={submit} disabled={busy}
                                className="flex-1 px-4 py-2.5 bg-emerald-600 hover:bg-emerald-700 text-white font-bold rounded-lg disabled:opacity-50"
                                data-testid="unified-submit">
                                {busy ? "جاري الاعتماد..." : "اعتماد القيد"}
                            </button>
                            <button onClick={resetForm}
                                className="px-4 py-2.5 bg-slate-200 hover:bg-slate-300 text-slate-800 font-bold rounded-lg">
                                إعادة تعيين
                            </button>
                        </div>

                        {result && (
                            <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-3 mt-3 text-xs"
                                 data-testid="unified-result">
                                <div className="font-bold text-emerald-900 mb-1">
                                    ✅ تم تسجيل القيد رقم: {result.txn_group_id?.slice(0, 8)}…
                                </div>
                                <div className="text-slate-700">
                                    عدد القيود: {result.entries?.length} ·
                                    إجمالي مدين: {result.debit_total} ·
                                    إجمالي دائن: {result.credit_total}
                                </div>
                                {/* Iter-191 — Courier COD settlement audit block. */}
                                {opType === "courier_cod_settle"
                                  && result.settlement_total !== undefined && (
                                    <div className="mt-2 pt-2 border-t border-emerald-200 grid grid-cols-2 gap-1 text-[11px]">
                                        <div className="text-slate-600">شركة الشحن:</div>
                                        <div className="text-left font-bold">
                                            {couriers.find(c => c.id === entityId)?.name || "—"}
                                        </div>
                                        <div className="text-slate-600">إجمالي التسوية:</div>
                                        <div className="text-left font-bold num">
                                            {Number(result.settlement_total).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س
                                        </div>
                                        <div className="text-slate-600">الرصيد السابق:</div>
                                        <div className="text-left font-bold num">
                                            {Number(result.previous_cod_balance).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س
                                        </div>
                                        <div className="text-slate-600">الرصيد المتبقي:</div>
                                        <div className={`text-left font-extrabold num ${
                                            Number(result.remaining_cod_balance) < 0.005
                                                ? "text-emerald-700" : "text-amber-700"
                                        }`}>
                                            {Number(result.remaining_cod_balance).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س
                                        </div>
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                )}
            </div>

            {/* Iter-209 — Recent ledger transactions panel ─────────── */}
            <RecentTxnsPanel
                rows={recentTxns}
                highlightGroupId={highlightGroupId}
                loading={recentLoading}
                onRefresh={() => loadRecentTxns()}
            />

            {/* Iter-182 — Visibility management modal. */}
            {showVisibilityModal && (
                <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" data-testid="visibility-modal">
                    <div className="bg-white rounded-2xl shadow-2xl max-w-2xl w-full max-h-[85vh] overflow-y-auto">
                        <div className="sticky top-0 bg-white border-b border-slate-200 p-5 flex items-center justify-between">
                            <div>
                                <h2 className="text-lg font-extrabold text-slate-900">⚙️ إدارة أنواع العمليات</h2>
                                <p className="text-xs text-slate-500 mt-0.5">اختر الأنواع التي تريد إخفاءها من شاشة «حركة مالية جديدة». الإعداد عام لكل الحساب.</p>
                            </div>
                            <button
                                type="button"
                                onClick={() => setShowVisibilityModal(false)}
                                className="text-slate-500 hover:text-slate-800 text-2xl leading-none"
                                data-testid="visibility-modal-close"
                            >×</button>
                        </div>

                        <div className="p-5 space-y-4">
                            {["employees", "suppliers", "externals", "general", "shipping"].map(sec => {
                                const sectionLabel = {
                                    employees: "موظفون",
                                    suppliers: "موردون",
                                    externals: "أشخاص خارجيون",
                                    general: "عمليات عامة",
                                    shipping: "شركات الشحن",
                                }[sec];
                                const items = OP_TYPES.filter(x => x.section === sec);
                                return (
                                    <div key={sec} className="border border-slate-200 rounded-xl p-3">
                                        <div className="text-sm font-extrabold text-slate-700 mb-2">{sectionLabel}</div>
                                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                                            {items.map(o => {
                                                const hidden = hiddenTypes.includes(o.value);
                                                return (
                                                    <label
                                                        key={o.value}
                                                        className={`flex items-center justify-between gap-3 px-3 py-2 rounded-lg cursor-pointer border-2 transition-colors ${
                                                            hidden ? "bg-rose-50 border-rose-200" : "bg-emerald-50 border-emerald-200"
                                                        }`}
                                                        data-testid={`visibility-toggle-${o.value}`}
                                                    >
                                                        <span className="text-sm font-bold">{o.label}</span>
                                                        <input
                                                            type="checkbox"
                                                            checked={!hidden}
                                                            onChange={() => toggleHidden(o.value)}
                                                            className="w-5 h-5 accent-emerald-600 cursor-pointer"
                                                        />
                                                    </label>
                                                );
                                            })}
                                        </div>
                                    </div>
                                );
                            })}
                        </div>

                        <div className="sticky bottom-0 bg-white border-t border-slate-200 p-4 flex items-center justify-end gap-2">
                            <button
                                type="button"
                                onClick={() => setShowVisibilityModal(false)}
                                className="px-4 py-2 text-sm font-bold text-slate-600 hover:text-slate-900 rounded-lg"
                                data-testid="visibility-cancel-btn"
                            >إلغاء</button>
                            <button
                                type="button"
                                onClick={saveVisibility}
                                disabled={savingVisibility}
                                className="px-6 py-2 text-sm font-extrabold bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg disabled:opacity-50"
                                data-testid="visibility-save-btn"
                            >{savingVisibility ? "جاري الحفظ…" : "حفظ"}</button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}



// ── Iter-209 — Recent ledger transactions panel ──────────────────────
// Sits BELOW the entry form. After every successful submission the
// form clears, the table refreshes, and the newly-posted row flashes
// green for 4 seconds. The merchant stays on the same screen and can
// chain transactions without navigation.

const TXN_TYPE_LABELS = {
    salary_payment: "صرف راتب",
    salary_accrual: "استحقاق راتب",
    salary_settle: "تسوية راتب",
    employee_settle: "تسوية موظف",
    advance_grant: "منح سلفة",
    advance_offset: "خصم سلفة",
    custody_grant: "صرف عهدة",
    custody_return: "إرجاع عهدة",
    custody_settle: "تسوية عهدة",
    supplier_pay: "دفع لمورد",
    external_grant: "صرف لخارجي",
    external_collect: "تحصيل من خارجي",
    expense_record: "تسجيل مصروف",
    expense: "مصروف",
    adjustment: "تسوية",
    bank_transfer: "تحويل بين حسابات",
    courier_cod_settle: "تسوية شحن",
    ad_account_topup: "تعبئة حساب إعلاني",
    ad_account_spend: "صرف إعلاني",
    correction: "تصحيح قيد",
    reversal: "عكس قيد",
    opening_balance: "رصيد افتتاحي",
    auto_opening_balance: "رصيد افتتاحي مرحّل",
    topup: "تعبئة",
    spend: "صرف",
    sale: "بيع",
    refund: "مرتجع",
    commission: "عمولة",
    transfer: "تحويل",
};

function txnLabel(t) {
    return TXN_TYPE_LABELS[t] || t || "—";
}

function fmtNum(n) {
    return Number(n || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
}

function timeAgo(iso) {
    if (!iso) return "—";
    try {
        const d = new Date(iso);
        const diff = (Date.now() - d.getTime()) / 1000;
        if (diff < 60) return "قبل لحظات";
        if (diff < 3600) return `قبل ${Math.floor(diff / 60)} د`;
        if (diff < 86400) return `قبل ${Math.floor(diff / 3600)} س`;
        return d.toLocaleDateString("en-CA");
    } catch { return iso.slice(0, 10); }
}

function RecentTxnsPanel({ rows, highlightGroupId, loading, onRefresh }) {
    const [selectedTxn, setSelectedTxn] = useState(null);
    if (!rows || rows.length === 0) {
        return loading ? (
            <div className="max-w-5xl mx-auto px-4 pb-6">
                <div className="bg-white rounded-2xl shadow-sm p-5 text-center text-xs text-slate-400">
                    جاري تحميل آخر الحركات…
                </div>
            </div>
        ) : null;
    }
    return (
        <div className="max-w-5xl mx-auto px-4 pb-6"
            data-testid="unified-recent-txns">
            <div className="bg-white rounded-2xl shadow-sm overflow-hidden">
                <div className="px-5 py-3 border-b border-slate-200 flex items-center justify-between">
                    <h3 className="text-sm font-extrabold text-slate-900">
                        📋 آخر الحركات المالية
                    </h3>
                    <button onClick={onRefresh}
                        className="text-[11px] font-bold text-emerald-700 hover:text-emerald-900"
                        data-testid="recent-txns-refresh">
                        🔄 تحديث
                    </button>
                </div>
                <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                        <thead className="bg-slate-50 text-slate-500 border-b border-slate-200">
                            <tr>
                                <th className="text-right py-2 px-3 font-bold">الوقت</th>
                                <th className="text-right py-2 px-3 font-bold">نوع العملية</th>
                                <th className="text-right py-2 px-3 font-bold">الوصف</th>
                                <th className="text-right py-2 px-3 font-bold">بواسطة</th>
                                <th className="text-right py-2 px-3 font-bold">الحالة</th>
                                <th className="text-left py-2 px-3 font-bold">المبلغ (ر.س)</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((g) => {
                                const isNew = g.txn_group_id === highlightGroupId;
                                const isReversed = !!g.reversed_by_name;
                                return (
                                    <tr key={g.txn_group_id}
                                        onClick={() => setSelectedTxn(g)}
                                        className={`border-b border-slate-100 transition-colors duration-700 cursor-pointer ${
                                            isNew
                                                ? "bg-emerald-50 ring-2 ring-emerald-300 ring-inset"
                                                : isReversed
                                                ? "bg-rose-50/40 hover:bg-rose-50"
                                                : "hover:bg-slate-50"
                                        }`}
                                        data-testid={`recent-row-${g.txn_group_id}`}>
                                        <td className="py-2 px-3 text-slate-600 whitespace-nowrap">
                                            {isNew && (
                                                <span className="inline-block w-2 h-2 bg-emerald-500 rounded-full me-1.5 animate-pulse"></span>
                                            )}
                                            {timeAgo(g.posted_at)}
                                        </td>
                                        <td className="py-2 px-3 font-bold text-slate-800">
                                            {(() => {
                                                // Iter-250b · P1.5.g — relabel auto-seeded
                                                // opening balances in the recent rows table too.
                                                const isOB = (g.legs || []).some(
                                                    (l) =>
                                                        l.entry_type === "opening_balance" ||
                                                        l.metadata_source === "iter192_auto_seed",
                                                );
                                                return (
                                                    <>
                                                        {txnLabel(isOB ? "auto_opening_balance" : g.txn_type)}
                                                        {isOB && (
                                                            <span
                                                                data-testid={`row-badge-ob-${g.txn_group_id}`}
                                                                className="ms-1.5 inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-extrabold bg-indigo-100 text-indigo-800 border border-indigo-200"
                                                                title="رصيد افتتاحي مرحّل تلقائياً"
                                                            >
                                                                🪙 OB
                                                            </span>
                                                        )}
                                                    </>
                                                );
                                            })()}
                                        </td>
                                        <td className="py-2 px-3 text-slate-600 truncate max-w-[220px]"
                                            title={g.notes}>
                                            {g.notes || "—"}
                                        </td>
                                        <td className="py-2 px-3 text-slate-700 whitespace-nowrap"
                                            data-testid={`recent-row-${g.txn_group_id}-creator`}>
                                            {g.posted_by_name || "—"}
                                        </td>
                                        <td className="py-2 px-3 whitespace-nowrap"
                                            data-testid={`recent-row-${g.txn_group_id}-status`}>
                                            {isReversed ? (
                                                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-rose-100 text-rose-800 text-[10px] font-bold"
                                                    title={`عكسها: ${g.reversed_by_name}`}>
                                                    ↩︎ معكوس · {g.reversed_by_name}
                                                </span>
                                            ) : (
                                                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-emerald-100 text-emerald-800 text-[10px] font-bold">
                                                    ✓ معتمد
                                                </span>
                                            )}
                                        </td>
                                        <td className={`text-left py-2 px-3 num font-extrabold ${
                                            isReversed
                                                ? "text-rose-700 line-through decoration-rose-400"
                                                : "text-emerald-700"
                                        }`}>
                                            {fmtNum(g.total_debit)}
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
                <div className="px-5 py-2 bg-slate-50 border-t border-slate-200 text-[10px] text-slate-500">
                    آخر {rows.length} حركة من القيد الموحد. الصف الأخضر = الحركة التي اعتُمدت للتو. اضغط على أي صف لعرض التفاصيل المحاسبية.
                </div>
            </div>
            <TxnDetailModal
                key={selectedTxn?.txn_group_id || "none"}
                txn={selectedTxn}
                onClose={() => setSelectedTxn(null)}
                onReversed={() => {
                    setSelectedTxn(null);
                    onRefresh && onRefresh();
                }}
            />
        </div>
    );
}

// ── Iter-213 — Transaction detail modal ──────────────────────────────
// Renders the double-entry breakdown of a clicked txn-group: every
// debit/credit leg, what entity it touches, and a plain-Arabic
// "كم له / كم عليه" summary so the merchant understands the impact at
// a glance. Iter-214 adds creator/reverser names + a one-click
// reversal button gated by reason-code selection.

// Iter-214 — Reason codes mirror REASON_CODES in backend ledger_core.
const REVERSAL_REASONS = [
    { code: "data_entry_error", label: "خطأ إدخال" },
    { code: "duplicate_entry", label: "قيد مكرر" },
    { code: "platform_correction", label: "تصحيح من المنصة" },
    { code: "accounting_settle", label: "تسوية محاسبية" },
    { code: "approved_writeoff", label: "شطب معتمد" },
    { code: "other", label: "أخرى" },
];

const ENTITY_LABELS = {
    employee: "موظف",
    supplier: "مورّد",
    external: "خارجي",
    bank: "بنك",
    cash: "صندوق نقدي",
    expense: "مصروف",
    ad_account: "حساب إعلاني",
    courier: "شركة شحن",
    customer: "عميل",
    payment_gateway: "بوابة دفع",
    revenue: "إيرادات",
    equity: "حقوق ملكية",
    asset: "أصل",
    liability: "التزام",
};

const SUB_ACCOUNT_LABELS = {
    salary_payable: "راتب مستحق",
    advance: "سلفة",
    custody: "عهدة",
    main: "الرئيسي",
    balance: "الرصيد",
    debt: "المديونية",
    payable: "ذمم دائنة",
    receivable: "ذمم مدينة",
    cod_receivable: "ذمم COD",
    opening_balance: "رصيد افتتاحي",
    sales: "مبيعات",
};

function entityLabel(t) { return ENTITY_LABELS[t] || t || "—"; }
function subLabel(s) { return SUB_ACCOUNT_LABELS[s] || s || "—"; }

function TxnDetailModal({ txn, onClose, onReversed }) {
    const [reason, setReason] = useState("");
    const [reversalNotes, setReversalNotes] = useState("");
    const [confirming, setConfirming] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    if (!txn) return null;
    const debits = txn.legs.filter((l) => l.side === "debit");
    const credits = txn.legs.filter((l) => l.side === "credit");
    const totalDebit = debits.reduce((s, l) => s + l.amount, 0);
    const totalCredit = credits.reduce((s, l) => s + l.amount, 0);
    const balanced = Math.abs(totalDebit - totalCredit) < 0.01;
    const isReversed = !!txn.reversed_by_name;
    const canReverse = !isReversed && balanced;
    // Iter-250b · P1.5.g — Detect auto-seeded opening balance groups so
    // we relabel them as "رصيد افتتاحي مرحّل" instead of generic "تسوية",
    // render an explicit badge, and require a stronger confirmation
    // before allowing reversal (reversing changes the account's starting
    // point in the ledger).
    const isAutoOpeningBalance = (txn.legs || []).some(
        (l) =>
            l.entry_type === "opening_balance" ||
            l.metadata_source === "iter192_auto_seed",
    );
    const effectiveTxnType = isAutoOpeningBalance
        ? "auto_opening_balance"
        : txn.txn_type;

    const handleReverse = async () => {
        if (!reason) {
            toast.error("اختر سبب العكس أولاً");
            return;
        }
        setSubmitting(true);
        try {
            const { data } = await api.post(
                `/ledger/groups/${txn.txn_group_id}/reverse`,
                { reason_code: reason, notes: reversalNotes || "" },
            );
            toast.success(
                `تم عكس الحركة بنجاح (${data.reversed_count} قيد)`,
            );
            onReversed && onReversed();
        } catch (err) {
            const msg = err?.response?.data?.detail
                || "فشل عكس الحركة";
            toast.error(msg);
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
            onClick={onClose}
            data-testid="txn-detail-modal">
            <div className="bg-white rounded-2xl shadow-2xl max-w-3xl w-full max-h-[90vh] overflow-hidden flex flex-col"
                onClick={(e) => e.stopPropagation()}>
                {/* Header */}
                <div className="px-5 py-3 bg-gradient-to-l from-sky-50 to-emerald-50 border-b border-sky-200 flex items-center justify-between">
                    <div>
                        <h3 className="font-extrabold text-slate-900" style={{ fontFamily: "Tajawal" }}>
                            تفاصيل الحركة — {txnLabel(effectiveTxnType)}
                        </h3>
                        <p className="text-[10px] text-slate-500 mt-0.5 font-mono">
                            {txn.txn_group_id}
                        </p>
                        {isAutoOpeningBalance && (
                            <span
                                data-testid="badge-auto-opening-balance"
                                title="هذا قيد افتتاحي تلقائي صدر عند أول قيد محاسبي على الحساب — يربط رصيد الحساب القديم بـ Ledger."
                                className="mt-1.5 inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-800 border border-indigo-200 text-[10px] font-extrabold"
                            >
                                🪙 Opening Balance · رصيد افتتاحي
                            </span>
                        )}
                    </div>
                    <button onClick={onClose}
                        className="w-8 h-8 rounded-lg hover:bg-sky-100 flex items-center justify-center text-slate-600 font-bold"
                        data-testid="txn-detail-modal-close">✕</button>
                </div>

                {/* Iter-214 — Audit trail (creator + reverser) */}
                <div className="px-5 py-2.5 bg-white border-b border-slate-100 flex flex-wrap items-center gap-2 text-[11px]"
                    data-testid="txn-detail-audit">
                    <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg bg-sky-50 text-sky-800 font-bold">
                        <span className="w-1.5 h-1.5 rounded-full bg-sky-500"></span>
                        أضافها: <span className="num">{txn.posted_by_name || "—"}</span>
                    </span>
                    {isReversed && (
                        <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg bg-rose-50 text-rose-800 font-bold"
                            data-testid="txn-detail-reverser">
                            <span className="w-1.5 h-1.5 rounded-full bg-rose-500"></span>
                            عكسها: {txn.reversed_by_name}
                            {txn.reversed_at && (
                                <span className="text-[10px] text-rose-600 font-normal">
                                    · {timeAgo(txn.reversed_at)}
                                </span>
                            )}
                        </span>
                    )}
                </div>

                {/* Plain-Arabic summary */}
                <div className="px-5 py-3 bg-amber-50/40 border-b border-amber-100">
                    <p className="text-xs text-slate-600 mb-2 font-bold">📝 ملخص العملية</p>
                    <p className="text-sm text-slate-800 leading-relaxed">
                        {txn.notes || "—"}
                    </p>
                </div>

                {/* Body — double-entry breakdown */}
                <div className="flex-1 overflow-auto p-4 space-y-3">
                    {/* Debits */}
                    <div>
                        <h4 className="text-xs font-extrabold text-emerald-800 mb-2 flex items-center gap-2">
                            <span className="inline-block w-2 h-2 bg-emerald-500 rounded-full"></span>
                            مدين ({debits.length}) — ما زاد علينا أو ما دفعناه
                        </h4>
                        <div className="space-y-2">
                            {debits.map((l, i) => (
                                <div key={i} className="bg-emerald-50 border border-emerald-200 rounded-lg p-2.5 flex items-center justify-between">
                                    <div>
                                        <div className="text-xs font-bold text-emerald-900">
                                            {entityLabel(l.entity_type)}
                                            {l.sub_account ? ` · ${subLabel(l.sub_account)}` : ""}
                                        </div>
                                        <div className="text-[10px] text-emerald-700 font-mono">
                                            {l.entity_id}
                                        </div>
                                    </div>
                                    <div className="text-base font-extrabold num text-emerald-800">
                                        {fmtNum(l.amount)} <span className="text-[10px]">ر.س</span>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* Credits */}
                    <div>
                        <h4 className="text-xs font-extrabold text-rose-800 mb-2 flex items-center gap-2">
                            <span className="inline-block w-2 h-2 bg-rose-500 rounded-full"></span>
                            دائن ({credits.length}) — ما خصم من رصيدنا أو ما استحق علينا
                        </h4>
                        <div className="space-y-2">
                            {credits.map((l, i) => (
                                <div key={i} className="bg-rose-50 border border-rose-200 rounded-lg p-2.5 flex items-center justify-between">
                                    <div>
                                        <div className="text-xs font-bold text-rose-900">
                                            {entityLabel(l.entity_type)}
                                            {l.sub_account ? ` · ${subLabel(l.sub_account)}` : ""}
                                        </div>
                                        <div className="text-[10px] text-rose-700 font-mono">
                                            {l.entity_id}
                                        </div>
                                    </div>
                                    <div className="text-base font-extrabold num text-rose-800">
                                        {fmtNum(l.amount)} <span className="text-[10px]">ر.س</span>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* Balance check */}
                    <div className={`mt-4 p-3 rounded-lg border-2 ${
                        balanced
                            ? "bg-emerald-50 border-emerald-300"
                            : "bg-rose-50 border-rose-300"
                    }`}>
                        <div className="flex items-center justify-between mb-1">
                            <span className="text-xs font-extrabold text-slate-700">
                                {balanced ? "✅ القيد متوازن" : "⚠️ القيد غير متوازن"}
                            </span>
                            <span className="text-[10px] text-slate-500">
                                {timeAgo(txn.posted_at)}
                            </span>
                        </div>
                        <div className="grid grid-cols-2 gap-2 text-xs">
                            <div>
                                <span className="text-slate-500">إجمالي المدين: </span>
                                <span className="num font-bold text-emerald-700">{fmtNum(totalDebit)}</span>
                            </div>
                            <div>
                                <span className="text-slate-500">إجمالي الدائن: </span>
                                <span className="num font-bold text-rose-700">{fmtNum(totalCredit)}</span>
                            </div>
                        </div>
                    </div>

                    {/* Iter-214 — Reversal panel */}
                    {isReversed && (
                        <div className="mt-3 p-3 rounded-lg bg-rose-50 border border-rose-200 text-xs text-rose-800 font-bold flex items-center gap-2"
                            data-testid="txn-already-reversed">
                            ↩︎ هذه الحركة معكوسة بالفعل بواسطة {txn.reversed_by_name}.
                        </div>
                    )}
                    {!isReversed && !balanced && (
                        <div className="mt-3 p-3 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-800"
                            data-testid="txn-imbalanced-warning">
                            ⚠️ هذا القيد غير متوازن — لا يمكن عكسه آلياً. تواصل مع المسؤول المحاسبي لتعديل البيانات يدوياً.
                        </div>
                    )}
                    {canReverse && !confirming && (
                        <button
                            onClick={() => setConfirming(true)}
                            className="mt-3 w-full py-2.5 rounded-lg bg-rose-600 hover:bg-rose-700 text-white text-sm font-extrabold transition"
                            data-testid="txn-detail-reverse-btn">
                            ↩︎ عكس هذه الحركة
                        </button>
                    )}
                    {canReverse && confirming && (
                        <div className="mt-3 p-3 rounded-lg bg-rose-50 border-2 border-rose-200 space-y-2.5"
                            data-testid="txn-detail-reverse-form">
                            <h5 className="text-xs font-extrabold text-rose-900">
                                تأكيد عكس الحركة
                            </h5>
                            {isAutoOpeningBalance && (
                                <div
                                    data-testid="txn-reverse-opening-warning"
                                    className="p-2 rounded-md bg-amber-100 border-2 border-amber-400 text-amber-900 text-[11px] font-extrabold leading-relaxed">
                                    ⚠️ هذا رصيد افتتاحي مرحّل — عكسه سيُغيّر نقطة بداية الحساب في Ledger وقد يجعل جميع الأرصدة اللاحقة غير متطابقة مع البيانات التاريخية. تابع فقط إذا كنت متأكداً تماماً.
                                </div>
                            )}
                            <p className="text-[11px] text-rose-700 leading-relaxed">
                                سيُنشأ قيد عكسي يلغي أثر هذه الحركة على جميع الحسابات. القيد الأصلي يبقى محفوظاً للمراجعة.
                            </p>
                            <div>
                                <label className="block text-[10px] font-bold text-slate-600 mb-1">
                                    سبب العكس <span className="text-rose-600">*</span>
                                </label>
                                <select
                                    value={reason}
                                    onChange={(e) => setReason(e.target.value)}
                                    className="w-full text-xs border border-slate-300 rounded-md px-2 py-1.5 bg-white"
                                    data-testid="txn-reverse-reason-select">
                                    <option value="">— اختر السبب —</option>
                                    {REVERSAL_REASONS.map((r) => (
                                        <option key={r.code} value={r.code}>
                                            {r.label}
                                        </option>
                                    ))}
                                </select>
                            </div>
                            <div>
                                <label className="block text-[10px] font-bold text-slate-600 mb-1">
                                    ملاحظات (اختياري)
                                </label>
                                <input
                                    type="text"
                                    value={reversalNotes}
                                    onChange={(e) => setReversalNotes(e.target.value)}
                                    placeholder="اشرح سبب العكس بإيجاز…"
                                    className="w-full text-xs border border-slate-300 rounded-md px-2 py-1.5"
                                    data-testid="txn-reverse-notes-input"/>
                            </div>
                            <div className="flex gap-2 pt-1">
                                <button
                                    onClick={handleReverse}
                                    disabled={submitting || !reason}
                                    className="flex-1 py-2 rounded-md bg-rose-600 hover:bg-rose-700 text-white text-xs font-extrabold disabled:opacity-50 disabled:cursor-not-allowed transition"
                                    data-testid="txn-reverse-confirm-btn">
                                    {submitting ? "جاري العكس…" : "تأكيد العكس"}
                                </button>
                                <button
                                    onClick={() => { setConfirming(false); setReason(""); setReversalNotes(""); }}
                                    disabled={submitting}
                                    className="px-4 py-2 rounded-md bg-white border border-slate-300 hover:bg-slate-50 text-xs font-bold text-slate-700"
                                    data-testid="txn-reverse-cancel-btn">
                                    إلغاء
                                </button>
                            </div>
                        </div>
                    )}
                </div>

                {/* Footer */}
                <div className="px-5 py-2 bg-slate-50 border-t border-slate-200 text-[10px] text-slate-500">
                    💡 المدين (Debit) = ما دخل/ازداد عندنا. الدائن (Credit) = ما خرج/استحق علينا. مجموع المدين يجب أن يساوي مجموع الدائن دائماً.
                </div>
            </div>
        </div>
    );
}

// Iter-216 — Named exports so the dedicated transactions page
// (LedgerTransactionsPage) can reuse the modal + label helpers
// without duplicating ~250 lines of code.
export {
    TxnDetailModal,
    txnLabel,
    entityLabel,
    subLabel,
    fmtNum,
    timeAgo,
    TXN_TYPE_LABELS,
    ENTITY_LABELS,
    SUB_ACCOUNT_LABELS,
    REVERSAL_REASONS,
};
