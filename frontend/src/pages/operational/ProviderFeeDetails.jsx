import { formatRiyadhDateTime } from "../../lib/dates";
import { formatPositionCount, formatPositionMoney, positionText, readableMessages, UNAVAILABLE } from "./financialPositionDisplay";
import { isStatementAverage, statementEstimatedAmount, statementRate } from "./providerStatementPresentation";

const STATUS_LABELS = {
    confirmed: "فعلية مثبتة", estimated: "تقديرية", mixed: "فعلية وتقديرية",
    review: "تحتاج مراجعة", unavailable: "غير متاحة",
};
const BASIS_LABELS = {
    actual_statement: "كشف مطابق", actual_settlement: "تسوية مسجلة",
    plan_estimate: "تعرفة محفوظة", average_estimate: "متوسط كشف",
};
const SOURCE_ISSUES = {
    source_profile_ambiguous: "مصادر أو حسابات متعددة؛ يلزم تحديد النطاق.",
    source_profile_invalid: "تعذر التحقق من بيانات مصدر المتوسط.",
    source_average_unavailable: "لا يوجد متوسط محفوظ مطابق لهذا النطاق.",
    source_scope_unproven: "لم يثبت نطاق العملة والحساب لمصدر المتوسط.",
};
const SOURCE_DATES = {
    statement_date: "تاريخ الكشف", sale_event_date: "تاريخ بيع المصدر", accepted_at: "وقت اعتماد المصدر",
};
const ESTIMATE_ISSUES = {
    provider_fee_operation_count_missing: "عدد العمليات اللازمة لاحتساب الرسم الثابت غير مثبت.",
    provider_fee_vat_rate_missing: "نسبة ضريبة العمولة غير موثقة.",
    provider_fee_vat_rate_conflict: "يوجد تعارض في نسبة ضريبة العمولة.",
    provider_balance_recognition_pending: "المدفوع يحتاج مطابقة الرصيد أو التسويات قبل احتساب رسومه.",
    provider_statement_fee_settlement_conflict: "رسوم الكشف تتداخل مع تسوية مسجلة؛ تحتاج مطابقة لمنع التكرار.",
    provider_fee_estimate_unavailable: "بيانات احتساب العمولة التقديرية غير مكتملة.",
    provider_balance_sar_unavailable: "يلزم تحويل موثق لعرض رسوم العملية بعملة التقرير.",
    provider_statement_fee_review_required: "رسوم الكشف أو توزيعها على الدفع والاسترجاع تحتاج مراجعة.",
    provider_statement_fee_currency_conflict: "عملة رسوم الكشف لا تطابق عملة العملية المثبتة.",
    provider_statement_fee_evidence_changed: "تغير دليل العملية منذ قبول الكشف؛ تحتاج المطابقة إلى مراجعة.",
    provider_statement_fee_principal_unconfirmed: "أصل الدفع أو الاسترجاع المرتبط بالرسوم غير مكتمل في اللقطة.",
    provider_statement_refund_alias_review: "مرجع الاسترجاع يحتاج ربطًا بالدليل المعتمد.",
};
const list = value => Array.isArray(value) ? value : [];
const text = value => typeof value === "string" && value.trim() ? value : null;
const hasMoney = value => formatPositionMoney(value) !== UNAVAILABLE;

function statusLabel(row) {
    return STATUS_LABELS[row.fee_status]
        || (Object.values(STATUS_LABELS).includes(row.fee_status_label) ? row.fee_status_label : STATUS_LABELS.unavailable);
}

function basisLabel(row) {
    const bases = [...new Set([...list(row.fee_basis_sources), row.fee_basis])]
        .map(key => BASIS_LABELS[key]).filter(Boolean);
    return bases.join(" + ") || "المصدر غير مكتمل";
}

function validProfiles(value, row) {
    return list(value).filter(profile => profile && /^[A-Z]{3}$/.test(profile.native_currency)
        && isStatementAverage(profile, row.provider || row.id, profile.native_currency));
}

function sourceContext(row, currency) {
    const saved = validProfiles(row.source_average_profiles, row);
    const issues = list(row.source_average_issues).filter(issue => issue && typeof issue === "object");
    const sameIssues = issues.filter(issue => !issue.native_currency || issue.native_currency === currency);
    const invalidSame = list(row.source_average_profiles).some(profile => profile
        && profile.native_currency === currency && !saved.includes(profile));
    return {
        saved, issues, sameIssues, invalidSame,
        same: saved.filter(profile => profile.native_currency === currency),
        other: saved.filter(profile => profile.native_currency !== currency),
        structured: Array.isArray(row.source_average_profiles) || Array.isArray(row.source_average_issues),
    };
}

function instant(value) {
    return text(value) && /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) && Number.isFinite(Date.parse(value))
        ? formatRiyadhDateTime(value) : UNAVAILABLE;
}

function sourceDate(profile) {
    if (profile.date_precision === "day") {
        return text(profile.source_as_of) && /^\d{4}-\d{2}-\d{2}$/.test(profile.source_as_of)
            ? profile.source_as_of : UNAVAILABLE;
    }
    return profile.date_precision === "instant" ? instant(profile.source_as_of) : UNAVAILABLE;
}

function Amount({ value, currency }) {
    const amount = formatPositionMoney(value);
    return <><bdi dir="ltr">{amount}</bdi>{amount !== UNAVAILABLE && currency && <> <bdi dir="ltr">{currency}</bdi></>}</>;
}

function Tax({ profile }) {
    const rate = statementRate(profile.vat_rate_percent);
    if (profile.vat_rate_status === "explicit_uniform" && rate !== UNAVAILABLE) return <bdi dir="ltr">{rate}%</bdi>;
    if (profile.vat_rate_status === "source_conflict") return <>تحتاج مراجعة لتعارض المصدر</>;
    if (profile.vat_rate_status === "mixed") return <>نسب متعددة في المصدر</>;
    return <>غير موثقة في المصدر</>;
}

function SourceProfile({ profile, applied, saved }) {
    const reference = positionText(profile.statement_reference);
    return <article className="ofp-provider-source-card">
        <h5>متوسط عمولة <bdi dir="ltr">{profile.native_currency}</bdi></h5>
        <p className="ofp-provider-source-use">{applied ? "استُخدم في تقدير رسوم بعض الطلبات في هذه اللقطة."
            : saved ? "مصدر محفوظ؛ لم يُستخدم لتقدير رسوم في هذه اللقطة." : "مصدر للمتوسط التقديري."}</p>
        <dl className="ofp-provider-facts">
            <div><dt>العمولة المتغيرة قبل الضريبة</dt><dd><bdi dir="ltr">{statementRate(profile.rate_percent)}%</bdi> <span>تقديرية من الكشف</span></dd></div>
            <div><dt>متوسط الرسم الثابت لكل عملية</dt><dd><bdi dir="ltr">{statementEstimatedAmount(profile.fixed_amount_per_sale)} {profile.native_currency}</bdi> <span>قبل الضريبة · تقديري</span></dd></div>
            <div><dt>ضريبة المصدر</dt><dd><Tax profile={profile} /></dd></div>
            {text(profile.merchant_account) && <div><dt>الحساب</dt><dd><bdi dir="ltr">{profile.merchant_account}</bdi></dd></div>}
            <div><dt>مرجع الكشف</dt><dd><bdi dir="auto">{reference}</bdi></dd></div>
            <div><dt>{SOURCE_DATES[profile.source_date_basis] || "تاريخ المصدر"}</dt><dd><bdi dir="ltr">{sourceDate(profile)}</bdi>{profile.date_precision === "instant" && sourceDate(profile) !== UNAVAILABLE && " · الرياض"}</dd></div>
            {profile.source_date_basis !== "accepted_at" && <div><dt>وقت اعتماد المصدر</dt><dd><bdi dir="ltr">{instant(profile.accepted_at)}</bdi>{instant(profile.accepted_at) !== UNAVAILABLE && " · الرياض"}</dd></div>}
        </dl>
    </article>;
}

function SourceIssue({ issue }) {
    return <li>{issue.native_currency && <><bdi dir="ltr">{issue.native_currency}</bdi>: </>}
        {SOURCE_ISSUES[issue.reason_code] || "مصدر المتوسط يحتاج مراجعة."}
        {Number.isSafeInteger(issue.scopes) && issue.scopes > 1 && <> <span>عدد النطاقات: <bdi dir="ltr">{formatPositionCount(issue.scopes)}</bdi>.</span></>}
    </li>;
}

// Saved-source evidence is not proof that the displayed estimates used it.
// A headline rate is shown only for one unambiguous source in this currency.
export function ProviderSourceAverage({ row, currency }) {
    const context = sourceContext(row, currency);
    if (context.same.length === 1 && !context.sameIssues.length && !context.invalidSame) {
        return <bdi className="ofp-provider-rate" dir="ltr">{statementRate(context.same[0].rate_percent)}%</bdi>;
    }
    return <span className="ofp-unavailable" aria-label="لا توجد نسبة موحدة لهذه العملة">—</span>;
}

export function ProviderFeeStatus({ row }) {
    return <span className={`ofp-provider-fee-status${row.fee_status === "review" ? " ofp-provider-needs-review" : ""}`}>{statusLabel(row)}</span>;
}

function ProviderRowExplanations({ row, currency }) {
    const name = positionText(row.name);
    const context = sourceContext(row, currency);
    const used = validProfiles(row.average_fee_profiles, row);
    const profiles = new Map();
    for (const profile of context.saved) profiles.set(profile.profile_id || profile.statement_id, { profile, saved: true, applied: false });
    for (const profile of used) {
        const key = profile.profile_id || profile.statement_id;
        profiles.set(key, { ...(profiles.get(key) || {}), profile, applied: true });
    }
    const same = [...profiles.values()].filter(item => item.profile.native_currency === currency);
    const other = [...profiles.values()].filter(item => item.profile.native_currency !== currency);
    const otherIssues = context.issues.filter(issue => issue.native_currency && issue.native_currency !== currency);
    const invalidSources = list(row.source_average_profiles).length !== context.saved.length
        || list(row.average_fee_profiles).length !== used.length;
    const estimatedPartial = row.estimated_fees_complete === false;
    const count = row.estimated_fee_incomplete_orders;
    const estimateIssues = list(row.estimated_fee_incomplete_reasons).filter(issue => issue && typeof issue === "object");
    return <article className="ofp-provider-explanation" aria-label={`توضيحات عمولة ${name}`}>
        <h4>{name}</h4>
        <section><h5>الرسوم في هذه اللقطة · <bdi dir="ltr">{currency}</bdi></h5>
            <dl className="ofp-provider-facts">
                <div><dt>حالة العمولة</dt><dd>{statusLabel(row)}</dd></div>
                <div><dt>أساس الرسوم المحتسبة</dt><dd>{basisLabel(row)}</dd></div>
                <div><dt>{row.fees_complete === false ? "العمولات المعلومة مع ضريبتها" : "العمولات المحتسبة مع ضريبتها"}</dt><dd><Amount value={row.fees} currency={currency} /></dd></div>
                <div><dt>منها عمولات فعلية</dt><dd><Amount value={row.actual_fees} currency={currency} /></dd></div>
                <div><dt>{estimatedPartial ? "العمولات التقديرية المعلومة" : "منها عمولات تقديرية"}</dt><dd><Amount value={row.estimated_fees} currency={currency} />{estimatedPartial && hasMoney(row.estimated_fees) && " · جزئي"}</dd></div>
                <div><dt>آخر قبول لدليل الرسوم</dt><dd><bdi dir="ltr">{text(row.statement_evidence_as_of) ? instant(row.statement_evidence_as_of) : positionText(row.statement_evidence_date_label)}</bdi></dd></div>
            </dl>
            {estimatedPartial && <p className="ofp-provider-detail-note">{hasMoney(row.estimated_fees)
                ? "المبلغ التقديري هو الجزء الذي أمكن احتسابه فقط؛ بقية الرسوم غير مكتملة."
                : "لم تتوفر رسوم تقديرية مكتملة يمكن عرضها لهذه اللقطة."}
                {Number.isSafeInteger(count) && count > 0 && <> طلبات لم تكتمل رسومها التقديرية: <bdi dir="ltr">{formatPositionCount(count)}</bdi>.</>}
            </p>}
            {!!estimateIssues.length && <ul className="ofp-provider-source-issues">{estimateIssues.map((issue, index) => <li key={`${issue.code}:${index}`}>
                {ESTIMATE_ISSUES[issue.code] || "تحتاج مطابقة دليل الرسوم."}
                {Number.isSafeInteger(issue.orders) && issue.orders > 0 && <> عدد الطلبات: <bdi dir="ltr">{formatPositionCount(issue.orders)}</bdi>.</>}
            </li>)}</ul>}
            {row.estimated_fee_scope_incomplete === true && <p className="ofp-provider-detail-note">نطاق مصدر الرسوم التقديرية غير مكتمل في هذه اللقطة.</p>}
            {Number.isSafeInteger(row.estimated_fee_unidentified_orders) && row.estimated_fee_unidentified_orders > 0 && <p className="ofp-provider-detail-note">طلبات لم يكتمل دليل رسومها الفردي: <bdi dir="ltr">{formatPositionCount(row.estimated_fee_unidentified_orders)}</bdi>.</p>}
        </section>
        {Number.isSafeInteger(row.average_estimated_orders) && row.average_estimated_orders > 0 && <section>
            <h5>مكونات تقدير المتوسط</h5>
            <p className="ofp-provider-detail-note">هذه المكونات تخص محاولات التقدير، وقد تشمل طلبات غير مكتملة الرسوم أو لم تُخصم رسومها من الرصيد. لا تمثل تفصيلًا يُجمع للحصول على المجموع التقديري المعلوم.</p>
            <dl className="ofp-provider-facts">
                <div><dt>الطلبات التي استُخدم لها المتوسط</dt><dd><bdi dir="ltr">{formatPositionCount(row.average_estimated_orders)}</bdi></dd></div>
                <div><dt>العمولة المتغيرة التقديرية</dt><dd><Amount value={row.average_variable_fees} currency={currency} /></dd></div>
                <div><dt>الرسوم الثابتة التقديرية</dt><dd><Amount value={row.average_fixed_fees} currency={currency} /></dd></div>
                <div><dt>الضريبة التقديرية</dt><dd><Amount value={row.average_vat_fees} currency={currency} /></dd></div>
            </dl>
            {list(row.average_fixed_bases).includes("configured") && <p className="ofp-provider-detail-note">تستخدم بعض التقديرات الرسم الثابت من التعرفة المحفوظة.</p>}
            {list(row.average_fixed_bases).includes("statement_sale_mean") && <p className="ofp-provider-detail-note">تستخدم بعض التقديرات متوسط الرسم الثابت لكل عملية في الكشف.</p>}
            {list(row.average_operations_bases).includes("one_provider_order_estimate") && <p className="ofp-provider-detail-note">افتُرض رسم ثابت واحد لبعض الطلبات ذات المنصة الواحدة؛ هذا تقدير ولا يثبت عدد العمليات.</p>}
        </section>}
        <section><h5>مصادر متوسط العمولة</h5>
            {context.same.length > 1 && <p className="ofp-provider-detail-note">توجد مصادر متعددة لهذه العملة؛ لم تُعرض نسبة موحدة للمنصة.</p>}
            {(context.sameIssues.length > 0 || context.invalidSame) && context.same.length > 0 && <p className="ofp-provider-detail-note">بعض نطاقات المصدر غير مكتملة؛ لم تُعمّم نسبة الحساب المعروف على بقية العمليات.</p>}
            {same.map(item => <SourceProfile key={item.profile.profile_id || item.profile.statement_id} {...item} />)}
            {!!context.sameIssues.length && <ul className="ofp-provider-source-issues">{context.sameIssues.map((issue, index) => <SourceIssue key={`${issue.reason_code}:${index}`} issue={issue} />)}</ul>}
            {invalidSources && <p className="ofp-provider-detail-note">تعذر التحقق من بعض بيانات المصادر؛ لم تُعرض لها نسبة موحدة.</p>}
            {(other.length > 0 || otherIssues.length > 0) && <div className="ofp-provider-other-currencies">
                <h5>مصادر بعملات أصلية أخرى</h5>
                <p className="ofp-provider-detail-note">تخص هذه المصادر عمليات بعملتها الأصلية. الرسم الثابت الظاهر ليس مبلغًا بعملة التقرير.</p>
                {other.map(item => <SourceProfile key={item.profile.profile_id || item.profile.statement_id} {...item} />)}
                {!!otherIssues.length && <ul className="ofp-provider-source-issues">{otherIssues.map((issue, index) => <SourceIssue key={`${issue.native_currency}:${issue.reason_code}:${index}`} issue={issue} />)}</ul>}
            </div>}
            {!profiles.size && !context.issues.length && !invalidSources && <p>لا يوجد متوسط مصدر محفوظ في هذه اللقطة.</p>}
            {!context.structured && text(row.source_average_description) && <p className="ofp-provider-legacy-description">{row.source_average_description}</p>}
        </section>
        {!row.fee_status && text(row.fee_description) && <section><h5>وصف الرسوم في اللقطة</h5><p className="ofp-provider-legacy-description">{row.fee_description}</p></section>}
        {Number.isSafeInteger(row.pending_orders) && row.pending_orders > 0 && <section>
            <h5>طلبات تحتاج مراجعة</h5>
            <p className="ofp-provider-detail-note">العدد: <bdi dir="ltr">{formatPositionCount(row.pending_orders)}</bdi>. تظهر أرقام الطلبات وأسباب المراجعة بالضغط على العدد في الجدول.</p>
            <ul className="ofp-provider-source-issues">{readableMessages(list(row.pending_reasons)).map(reason => <li key={reason}>{reason}</li>)}</ul>
        </section>}
    </article>;
}

export default function ProviderFeeExplanations({ rows, currency, notes }) {
    return <section className="ofp-provider-explanations" aria-label="توضيحات منصات الدفع">
        <h3>توضيحات</h3>
        <div className="ofp-provider-table-notes">{readableMessages(list(notes)).map(note => <p key={note}>{note}</p>)}</div>
        {list(rows).map((row, index) => <ProviderRowExplanations key={row.id || index} row={row} currency={currency} />)}
    </section>;
}
