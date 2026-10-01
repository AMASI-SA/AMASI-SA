"""Public diagnostic metadata. Never changes readiness or financial gates."""

SECTION_STAGES = {"banks_cash": "02", "providers": "03/12", "couriers_cod": "07/08/09",
                  "inventory": "10", "suppliers": "05/06", "payroll_obligations": "04", "equity": "13/14"}
MESSAGES = {
    "onboarding_sections_incomplete": ("15", "الأقسام أو أدلتها غير مكتملة.", "أكمل الأرصدة أو الصفر الصريح أو عدم الانطباق المدعوم بالدليل ثم احفظ الأقسام."),
    "onboarding_cutover_evidence_required": ("01", "دليل توقيت القطع غير مربوط بالجلسة.", "ارفع دليل القطع واحفظه قبل المعاينة."),
    "mz2_writes_paused": ("16", "الكتابات المالية متوقفة؛ مسودة الإعداد متاحة دون ترحيل.", "اترك الإيقاف كما هو؛ استئناف التشغيل يحتاج بوابة وتفويضًا مستقلين."),
    "accounting_v2_not_active": ("16", "كاتب ميزان 2 المالي غير مفعّل.", "أكمل إعداد المصدر؛ التفعيل مسار مستقل خارج المعالج."),
    "opening_balance_not_verified": ("16", "لم يُثبت رصيد افتتاحي مالي معتمد.", "أكمل المعاينة والمراجعة؛ الترحيل يحتاج تفويضًا مستقلًا."),
    "smoke_b_production_proof_required": ("16", "إثبات Smoke B على Production غير متوفر لهذه البوابة.", "أكمل بوابة Smoke B المستقلة دون تعديل الحواجز."),
    "live_owner_authorization_required": ("16", "تفويض المالك للتشغيل المالي غير مثبت.", "اطلب التفويض المستقل بعد اكتمال البوابات."),
    "accounting_transition_contract_invalid": ("16", "حالة انتقال الكاتب المالي غير قابلة للتحقق.", "راجع عقد الانتقال؛ لا تفترض تفعيل الكاتب."),
    "later_phases_must_remain_locked": ("16", "حالة المراحل اللاحقة تخالف القفل المطلوب.", "أوقف مسار الاعتماد وراجع الضوابط؛ لا تغيّرها من المعالج."),
    "onboarding_provider_binding_required": ("03", "مزود الدفع يحتاج بنك تسوية canonical ودليلًا.", "اختر بنكًا نشطًا من الحسابات المالية لميزان 2 واحفظ الربط والدليل."),
    "onboarding_inventory_value_mismatch": ("10", "التقييم المالي لا يطابق حسابات المخزون أو ملف الدليل.", "طابق مجموع قيم الحسابات مع ملف التقييم؛ اعتماد الكميات خطوة مستقلة."),
    "onboarding_snapshot_changed": ("15", "تغيرت البيانات أو الأدلة منذ المعاينة.", "أعد المعاينة ثم المراجعة على النسخة الحالية."),
}


def diagnostic(blocker):
    code = blocker["code"]
    stage, message, action = MESSAGES.get(code, (
        SECTION_STAGES.get(blocker.get("section_id"), "15"),
        "تعذر إثبات شرط التأسيس الموضح برمز الخطأ.",
        "راجع الرمز والجهة والقسم، وصحح البيانات قبل إعادة فحص الجاهزية.",
    ))
    return {**blocker, "stage": blocker.get("stage", stage), "message_ar": message,
            "corrective_action": action}
