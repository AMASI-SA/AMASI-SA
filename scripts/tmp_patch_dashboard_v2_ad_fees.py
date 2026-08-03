from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def patch_backend() -> None:
    path = ROOT / "backend" / "dashboard_v2_routes.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from auth import ensure_user_settings\n",
        "from auth import ensure_user_settings\n"
        "from dashboard_v2_ad_costs import (\n"
        "    apply_mezan_v2_ad_account_costs,\n"
        "    merge_ad_bank_fees_into_dashboard,\n"
        ")\n",
        "dashboard imports",
    )
    text = replace_once(
        text,
        '    spend = sum(_float(row.get("spend_sar")) for row in selected)\n',
        '    spend = sum(_float(\n'
        '        row.get("effective_spend_sar")\n'
        '        if row.get("effective_spend_sar") is not None\n'
        '        else row.get("spend_sar")\n'
        '    ) for row in selected)\n',
        "effective aggregate spend",
    )
    text = replace_once(
        text,
        '    total_spend = sum(_float(row.get("spend_sar")) for row in rows)\n',
        '    total_spend = sum(_float(\n'
        '        row.get("effective_spend_sar")\n'
        '        if row.get("effective_spend_sar") is not None\n'
        '        else row.get("spend_sar")\n'
        '    ) for row in rows)\n',
        "snap account total spend",
    )

    build_start = text.index("async def build_mezan_v2_ads(\n")
    provider_start = text.index("\n\nasync def build_provider_summary", build_start)
    new_build = '''async def build_mezan_v2_ads(
    db: Any,
    user_id: str,
    *,
    from_date: str | None,
    to_date: str | None,
) -> dict[str, Any]:
    today = _today_riyadh().isoformat()
    start = from_date or today
    end = to_date or start
    raw_platform_rows = {
        provider: await _provider_rows(db, user_id, provider, start, end)
        for provider in ("snapchat", "meta", "tiktok")
    }
    account_costs = await apply_mezan_v2_ad_account_costs(
        db,
        user_id,
        raw_platform_rows,
    )
    platform_rows = account_costs["platform_rows"]
    breakdown = {
        provider: round(sum(_float(
            row.get("effective_spend_sar")
            if row.get("effective_spend_sar") is not None
            else row.get("spend_sar")
        ) for row in rows), 2)
        for provider, rows in platform_rows.items()
    }
    google_rows = await _to_list(
        db.daily_costs.find(
            {"user_id": user_id, "date": {"$gte": start, "$lte": end}},
            {"_id": 0, "google_ads": 1},
        ),
        100000,
    )
    breakdown["google_transitional"] = round(
        sum(_float(row.get("google_ads")) for row in google_rows),
        2,
    )
    bank_commissions = {
        key: value for key, value in account_costs.items()
        if key != "platform_rows"
    }
    bank_commissions["google_transitional_spend_sar"] = breakdown["google_transitional"]
    bank_commissions["google_account_allocation"] = (
        "not_available" if breakdown["google_transitional"] > 0 else "not_required"
    )
    return {
        "total": round(sum(breakdown.values()), 2),
        "breakdown": breakdown,
        "providers": {
            provider: _aggregate_provider_rows(rows, start, end)
            for provider, rows in platform_rows.items()
        },
        "bank_commissions": bank_commissions,
        "source_contract": {
            "snapchat": f"{SNAP_FACTS}:selected_accounts:ad_account_rows:spend_native",
            "meta": f"{META_FACTS}:selected_accounts:spend_native",
            "tiktok": f"{TIKTOK_FACTS}:connected_accounts:spend_native",
            "exchange_rates": "mezan_ad_account_cost_settings_v2:per_account",
            "bank_commissions": "mezan_ad_account_cost_settings_v2:per_account",
            "google": "daily_costs.google_ads:transitional_read_only:no_account_allocation",
            "excluded": ["legacy_ad_ledger", "legacy_ads_currency_settings", "daily_costs.snapchat_ads", "daily_costs.instagram_ads"],
        },
    }
'''
    text = text[:build_start] + new_build + text[provider_start:]

    old_provider = '''    rows = await _provider_rows(db, user_id, provider, d30_start, today_s)
    by_date = defaultdict(float)
    for row in rows:
        by_date[str(row.get("date") or "")] += _float(row.get("spend_sar"))
'''
    new_provider = '''    raw_rows = await _provider_rows(db, user_id, provider, d30_start, today_s)
    costed = await apply_mezan_v2_ad_account_costs(
        db,
        user_id,
        {slug: raw_rows if slug == provider else [] for slug in ("snapchat", "meta", "tiktok")},
    )
    rows = costed["platform_rows"].get(provider, [])
    by_date = defaultdict(float)
    for row in rows:
        by_date[str(row.get("date") or "")] += _float(
            row.get("effective_spend_sar")
            if row.get("effective_spend_sar") is not None
            else row.get("spend_sar")
        )
'''
    text = replace_once(text, old_provider, new_provider, "provider summary cost settings")
    text = replace_once(
        text,
        '        "source": f"mezan_v2_{provider}_native",\n',
        '        "source": f"mezan_v2_{provider}_native_with_account_fx",\n'
        '        "cost_settings_coverage": costed.get("coverage") or {},\n',
        "provider summary source",
    )
    text = replace_once(
        text,
        '        response.update({\n',
        '        merge_ad_bank_fees_into_dashboard(response, ads)\n'
        '        response.update({\n',
        "merge bank fees into dashboard",
    )
    text = replace_once(
        text,
        '                "payment_gateway_fees": "legacy_payment_method_settings",\n',
        '                "payment_gateway_fees": "legacy_payment_method_settings + mezan_ad_account_cost_settings_v2",\n',
        "payment fee source contract",
    )
    old_snap = '        rows = await _provider_rows(db, user_id, "snapchat", start, end)\n        accounts_meta = await _to_list(\n'
    new_snap = '''        raw_rows = await _provider_rows(db, user_id, "snapchat", start, end)
        costed = await apply_mezan_v2_ad_account_costs(
            db,
            user_id,
            {"snapchat": raw_rows, "meta": [], "tiktok": []},
        )
        rows = costed["platform_rows"].get("snapchat", [])
        accounts_meta = await _to_list(
'''
    text = replace_once(text, old_snap, new_snap, "snap account summary cost settings")
    text = replace_once(
        text,
        '                "metrics": "provider ad-account Riyadh-day facts only",\n',
        '                "metrics": "provider native spend multiplied by Mezan 2 account exchange rate",\n'
        '                "bank_commissions": "reported separately under payment fees",\n',
        "snap account source contract",
    )
    path.write_text(text, encoding="utf-8")


def patch_profit_summary() -> None:
    path = ROOT / "frontend" / "src" / "components" / "ProfitSummaryCard.jsx"
    text = path.read_text(encoding="utf-8")
    helper_anchor = '''const fmtInt = (v) => {
    if (v == null || Number.isNaN(Number(v))) return "—";
    return Number(v).toLocaleString("en-US");
};
'''
    helper = helper_anchor + '''
const optionalNumber = (value) => {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
};

export function buildPaymentFeeRows(paymentBreakdown = []) {
    const output = [];
    (Array.isArray(paymentBreakdown) ? paymentBreakdown : []).forEach((group, groupIndex) => {
        const children = Array.isArray(group?.sub_methods) && group.sub_methods.length
            ? group.sub_methods
            : [group];
        children.forEach((child, childIndex) => {
            const kind = child?.kind || (group?.key === "ad_bank_commissions" ? "ad_bank_commission" : "payment_method");
            output.push({
                key: child?.key || `${group?.key || groupIndex}-${childIndex}`,
                name: child?.display || child?.name || group?.name || "طريقة دفع غير معروفة",
                parentName: child !== group ? (child?.parent_name || group?.name || null) : null,
                kind,
                ordersCount: optionalNumber(child?.orders_count ?? group?.orders_count),
                baseAmount: optionalNumber(child?.total_sales ?? group?.total_sales) || 0,
                commissionPercent: optionalNumber(child?.commission_percent ?? group?.commission_percent),
                fixedFee: optionalNumber(child?.fixed_fee ?? group?.fixed_fee),
                vatPercent: optionalNumber(child?.vat_percent ?? group?.vat_percent),
                vatAmount: optionalNumber(child?.vat_amount),
                feeAmount: optionalNumber(child?.fee_amount ?? group?.fee_amount) || 0,
                nativeCurrency: child?.native_currency || null,
                exchangeRateToSar: optionalNumber(child?.exchange_rate_to_sar),
                spendNative: optionalNumber(child?.spend_native),
                applyBankCommission: child?.apply_bank_commission !== false,
                configured: child?.configured === true,
            });
        });
    });
    return output;
}
'''
    text = replace_once(text, helper_anchor, helper, "payment helper")
    text = replace_once(
        text,
        '    shippingBreakdown = [],\n    fromDate,\n',
        '    shippingBreakdown = [],\n    paymentBreakdown = [],\n    fromDate,\n',
        "payment breakdown prop",
    )
    text = replace_once(
        text,
        '    const [shippingExpanded, setShippingExpanded] = useState(false);\n    const [opExpanded, setOpExpanded] = useState(false);\n',
        '    const [shippingExpanded, setShippingExpanded] = useState(false);\n'
        '    const [paymentFeesExpanded, setPaymentFeesExpanded] = useState(false);\n'
        '    const [opExpanded, setOpExpanded] = useState(false);\n',
        "payment state",
    )
    old_fees = '''    const allPaymentFees   = (Number(t.other_payment_fees || 0)
                            + Number(t.tamara_fees        || 0)
                            + Number(t.tabby_fees         || 0)
                            + Number(t.emkan_fees         || 0)
                            + Number(t.bank_fees          || 0));
'''
    new_fees = '''    const calculatedPaymentFees = (Number(t.other_payment_fees || 0)
                            + Number(t.tamara_fees        || 0)
                            + Number(t.tabby_fees         || 0)
                            + Number(t.emkan_fees         || 0)
                            + Number(t.bank_fees          || 0)
                            + Number(t.ad_bank_commission_fees || 0));
    const allPaymentFees = t.total_payment_fees != null
        ? Number(t.total_payment_fees)
        : calculatedPaymentFees;
'''
    text = replace_once(text, old_fees, new_fees, "authoritative payment fees")

    payment_block = '''
    const paymentFeeRows = buildPaymentFeeRows(paymentBreakdown)
        .filter((row) => row.ordersCount > 0 || row.baseAmount > 0 || row.feeAmount > 0);
    const paymentFeesTooltip = (
        <div data-testid="payment-fees-tooltip-content" dir="rtl">
            <div className="flex items-center justify-between gap-3 mb-2 pb-2 border-b border-slate-200">
                <span className="text-xs font-extrabold text-violet-900">
                    💳 تفاصيل رسوم طرق الدفع والعمولات البنكية
                </span>
                <span className="text-[10px] text-slate-500">
                    {paymentFeeRows.length} طريقة / حساب
                </span>
            </div>
            {paymentFeeRows.length === 0 ? (
                <div className="text-xs text-slate-500 py-2 text-center">
                    لا توجد رسوم طرق دفع في هذه الفترة
                </div>
            ) : (
                <div className="overflow-x-auto">
                    <table className="w-full min-w-[900px] text-[11px]" data-testid="payment-fees-breakdown-table">
                        <thead className="bg-slate-50 text-slate-700">
                            <tr>
                                <th className="text-right p-1.5 font-extrabold">طريقة الدفع / الحساب</th>
                                <th className="text-center p-1.5 font-extrabold">الطلبات</th>
                                <th className="text-left p-1.5 font-extrabold">المبلغ الخاضع</th>
                                <th className="text-left p-1.5 font-extrabold">نسبة العمولة</th>
                                <th className="text-left p-1.5 font-extrabold">رسوم ثابتة</th>
                                <th className="text-left p-1.5 font-extrabold">VAT</th>
                                <th className="text-left p-1.5 font-extrabold">إجمالي الرسوم</th>
                            </tr>
                        </thead>
                        <tbody>
                            {paymentFeeRows.map((row) => (
                                <tr key={row.key} className="border-t border-slate-100">
                                    <td className="p-1.5 font-bold text-slate-700">
                                        <div>{row.name}</div>
                                        {row.parentName && row.parentName !== row.name && (
                                            <div className="mt-0.5 text-[9px] text-slate-400">{row.parentName}</div>
                                        )}
                                        {row.kind === "ad_bank_commission" && (
                                            <div className="mt-1 flex flex-wrap items-center gap-1 text-[9px]">
                                                <span className="rounded bg-violet-50 px-1 py-0.5 text-violet-700">عمولة سحب إعلاني</span>
                                                {row.nativeCurrency && row.exchangeRateToSar != null && (
                                                    <span className="rounded bg-slate-100 px-1 py-0.5 text-slate-600">
                                                        {row.nativeCurrency} × {row.exchangeRateToSar.toFixed(4)}
                                                    </span>
                                                )}
                                                {!row.applyBankCommission && (
                                                    <span className="rounded bg-slate-100 px-1 py-0.5 text-slate-500">غير مفعلة</span>
                                                )}
                                            </div>
                                        )}
                                    </td>
                                    <td className="p-1.5 text-center font-mono font-bold text-slate-700">
                                        {row.kind === "ad_bank_commission" ? "—" : fmtInt(row.ordersCount)}
                                    </td>
                                    <td className="p-1.5 text-left font-mono text-slate-700">{fmtSar(row.baseAmount)}</td>
                                    <td className="p-1.5 text-left font-mono text-violet-700">
                                        {row.commissionPercent == null ? "—" : `${row.commissionPercent.toFixed(2)}%`}
                                    </td>
                                    <td className="p-1.5 text-left font-mono text-slate-600">
                                        {row.fixedFee == null ? "—" : fmtSar(row.fixedFee)}
                                    </td>
                                    <td className="p-1.5 text-left font-mono text-slate-600">
                                        {row.vatAmount != null && row.vatAmount > 0
                                            ? fmtSar(row.vatAmount)
                                            : row.vatPercent != null && row.vatPercent > 0
                                                ? `${row.vatPercent.toFixed(0)}%`
                                                : "—"}
                                    </td>
                                    <td className="p-1.5 text-left font-mono font-extrabold text-violet-800">
                                        {fmtSar(row.feeAmount)}
                                    </td>
                                </tr>
                            ))}
                            <tr className="border-t-2 border-violet-200 bg-violet-50/60">
                                <td colSpan={6} className="p-1.5 font-extrabold text-slate-800">الإجمالي</td>
                                <td className="p-1.5 text-left font-mono font-extrabold text-violet-900">
                                    {fmtSar(allPaymentFees)}
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            )}
            <p className="mt-2 text-[10px] leading-relaxed text-slate-500">
                عمولة الحساب الإعلاني تُحسب من الصرف الأصلي بعد تحويله بسعر الصرف المحفوظ للحساب،
                وتُخصم مرة واحدة ضمن إجمالي رسوم طرق الدفع.
            </p>
        </div>
    );

'''
    marker = '    // 2) Operating expenses breakdown — sourced from `totals.operating_*`.\n'
    text = replace_once(text, marker, payment_block + marker, "payment tooltip")
    old_line = '                <Line icon={Receipt}    label="− إجمالي رسوم جميع طرق الدفع"    value={fmtSar(allPaymentFees)} share={sharePct(allPaymentFees, sales)}  color="violet" />\n'
    new_line = '                <Line icon={Receipt}    label="− إجمالي رسوم جميع طرق الدفع"    value={fmtSar(allPaymentFees)} share={sharePct(allPaymentFees, sales)}  color="violet" tooltip={paymentFeesTooltip} testid="profit-line-payment-fees" expandable expanded={paymentFeesExpanded} onClick={() => setPaymentFeesExpanded((value) => !value)} />\n'
    text = replace_once(text, old_line, new_line, "expandable payment line")
    path.write_text(text, encoding="utf-8")


def patch_dashboard_page() -> None:
    path = ROOT / "frontend" / "src" / "pages" / "Dashboard.jsx"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '                        shippingBreakdown={data?.shipping_breakdown || []}\n',
        '                        shippingBreakdown={data?.shipping_breakdown || []}\n'
        '                        paymentBreakdown={data?.payment_breakdown || []}\n',
        "dashboard payment breakdown prop",
    )
    path.write_text(text, encoding="utf-8")


def write_tests() -> None:
    backend_test = ROOT / "backend" / "tests" / "test_dashboard_v2_ad_account_costs.py"
    backend_test.write_text('''from dashboard_v2_ad_costs import (\n    apply_cost_settings_to_fact_rows,\n    bank_commission_payment_breakdown,\n    merge_ad_bank_fees_into_dashboard,\n)\n\n\ndef test_native_spend_uses_per_account_rate_and_bank_fee_once():\n    rows = {\n        "snapchat": [{\n            "ad_account_id": "snap-usd",\n            "spend_native": 1000,\n            "spend_sar": 3750,\n            "currency": "USD",\n        }],\n        "meta": [{\n            "ad_account_id": "meta-sar",\n            "spend_native": 500,\n            "spend_sar": 500,\n            "currency_native": "SAR",\n        }],\n        "tiktok": [],\n    }\n    accounts = [\n        {\n            "provider": "snapchat_ads",\n            "external_account_id": "snap-usd",\n            "mezan_integration_account_id": "m-snap",\n            "display_name": "أماسي سناب",\n            "currency": "USD",\n        },\n        {\n            "provider": "meta_ads",\n            "external_account_id": "meta-sar",\n            "mezan_integration_account_id": "m-meta",\n            "display_name": "أماسي ميتا",\n            "currency": "SAR",\n        },\n    ]\n    settings = [{\n        "provider": "snapchat_ads",\n        "external_account_id": "snap-usd",\n        "mezan_integration_account_id": "m-snap",\n        "native_currency": "USD",\n        "exchange_rate_to_sar": 3.7544,\n        "bank_commission_pct": 2.3,\n        "apply_bank_commission": True,\n    }]\n\n    result = apply_cost_settings_to_fact_rows(rows, accounts, settings)\n\n    snap = result["platform_rows"]["snapchat"][0]\n    meta = result["platform_rows"]["meta"][0]\n    assert snap["effective_spend_sar"] == 3754.4\n    assert meta["effective_spend_sar"] == 500\n    assert result["total_effective_spend_sar"] == 4254.4\n    assert result["total_fee_sar"] == 86.35\n    assert result["coverage"]["legacy_ads_currency_settings_read"] is False\n\n    entry = bank_commission_payment_breakdown(result)\n    assert entry["key"] == "ad_bank_commissions"\n    assert entry["fee_amount"] == 86.35\n    assert len(entry["sub_methods"]) == 2\n\n    response = {\n        "totals": {\n            "total_payment_fees": 110,\n            "net_profit": 1000,\n            "net_sales": 1200,\n        },\n        "net_sales_config": {"deduct_payment_fees": True},\n        "payment_breakdown": [{"key": "tamara", "name": "تمارا", "fee_amount": 110}],\n    }\n    merge_ad_bank_fees_into_dashboard(response, {"bank_commissions": result})\n    assert response["totals"]["ad_bank_commission_fees"] == 86.35\n    assert response["totals"]["total_payment_fees"] == 196.35\n    assert response["totals"]["net_profit"] == 913.65\n    assert response["totals"]["net_sales"] == 1113.65\n    assert [row["key"] for row in response["payment_breakdown"]] == [\n        "tamara",\n        "ad_bank_commissions",\n    ]\n\n\ndef test_snapchat_defaults_apply_before_account_is_explicitly_saved():\n    result = apply_cost_settings_to_fact_rows(\n        {\n            "snapchat": [{\n                "ad_account_id": "snap-default",\n                "spend_native": 100,\n                "currency": "USD",\n            }],\n            "meta": [],\n            "tiktok": [],\n        },\n        [{\n            "provider": "snapchat_ads",\n            "external_account_id": "snap-default",\n            "mezan_integration_account_id": "m-default",\n            "display_name": "سناب افتراضي",\n            "currency": "USD",\n        }],\n        [],\n    )\n    account = result["accounts"][0]\n    assert account["configured"] is False\n    assert account["exchange_rate_to_sar"] == 3.7544\n    assert account["bank_commission_pct"] == 2.3\n    assert account["spend_sar"] == 375.44\n    assert account["bank_commission_fee_sar"] == 8.64\n''', encoding="utf-8")

    frontend_test = ROOT / "frontend" / "src" / "components" / "ProfitSummaryCard.paymentFees.test.jsx"
    frontend_test.write_text('''import { fireEvent, render, screen } from "@testing-library/react";\n\njest.mock("react-router-dom", () => ({\n    Link: ({ children }) => children,\n}));\n\nimport ProfitSummaryCard, { buildPaymentFeeRows } from "./ProfitSummaryCard";\n\nconst breakdown = [\n    {\n        key: "tamara",\n        name: "تمارا",\n        total_sales: 1500,\n        fee_amount: 110,\n        orders_count: 5,\n        commission_percent: 6.99,\n        fixed_fee: 1.5,\n        vat_percent: 15,\n    },\n    {\n        key: "ad_bank_commissions",\n        name: "عمولات الحسابات الإعلانية",\n        total_sales: 3754.4,\n        fee_amount: 86.35,\n        sub_methods: [{\n            key: "ad_bank:snap",\n            display: "Snapchat — أماسي الرياض",\n            parent_name: "عمولات الحسابات الإعلانية",\n            kind: "ad_bank_commission",\n            native_currency: "USD",\n            exchange_rate_to_sar: 3.7544,\n            spend_native: 1000,\n            total_sales: 3754.4,\n            commission_percent: 2.3,\n            fee_amount: 86.35,\n            apply_bank_commission: true,\n        }],\n    },\n];\n\ntest("normalizes payment methods and ad-account bank commissions", () => {\n    const rows = buildPaymentFeeRows(breakdown);\n    expect(rows).toHaveLength(2);\n    expect(rows[0]).toMatchObject({ name: "تمارا", feeAmount: 110 });\n    expect(rows[1]).toMatchObject({\n        name: "Snapchat — أماسي الرياض",\n        kind: "ad_bank_commission",\n        baseAmount: 3754.4,\n        commissionPercent: 2.3,\n        feeAmount: 86.35,\n    });\n});\n\ntest("opens the fee table inside the executive profit summary", () => {\n    render(\n        <ProfitSummaryCard\n            totals={{\n                total_sales: 10000,\n                total_product_cost: 1000,\n                total_ads_cost: 3754.4,\n                total_shipping_cost: 300,\n                total_payment_fees: 196.35,\n                tamara_fees: 110,\n                ad_bank_commission_fees: 86.35,\n                net_profit: 4749.25,\n                total_orders: 10,\n            }}\n            paymentBreakdown={breakdown}\n        />,\n    );\n\n    fireEvent.click(screen.getByTestId("profit-line-payment-fees"));\n    expect(screen.getByTestId("payment-fees-tooltip-content")).toBeInTheDocument();\n    expect(screen.getByTestId("payment-fees-breakdown-table")).toBeInTheDocument();\n    expect(screen.getByText("تمارا")).toBeInTheDocument();\n    expect(screen.getByText("Snapchat — أماسي الرياض")).toBeInTheDocument();\n    expect(screen.getAllByText("86.35").length).toBeGreaterThan(0);\n});\n''', encoding="utf-8")


if __name__ == "__main__":
    patch_backend()
    patch_profit_summary()
    patch_dashboard_page()
    write_tests()
