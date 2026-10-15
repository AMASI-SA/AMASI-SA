# P01 closure audit — 2026-09-18

P01 remains IN_PROGRESS. This review separates authorized synthetic Preview acceptance from the written release gate. No existing settlement was reposted, reversed, or recognized again. No Production deployment or paid Emergent conversation was used.

## Written exit gate

References are frozen to the reviewed production-branch commit; checkmarks in the older file are historical, not fresh proof for new patches.

| نص الشرط | المرجع | الدليل | المتبقي |
|---|---|---|---|
| صفحة التسويات الموحدة منفذة. | [P01 L205](https://github.com/AMASI-SA/AMASI-SA/blob/1f2923f19ba503017a04677636d75caad8e4df91/docs/operations/MZ2-FIN-CUTOVER-001/PHASE-01-SETTLEMENTS.md#L205) | PASS — التنفيذ المنشور سابقًا وعرض Preview الحالي. | لا شيء في هذا البند. |
| ربط البنك الحالي لكل مزود منفذ ومحمي. | [P01 L206](https://github.com/AMASI-SA/AMASI-SA/blob/1f2923f19ba503017a04677636d75caad8e4df91/docs/operations/MZ2-FIN-CUTOVER-001/PHASE-01-SETTLEMENTS.md#L206) | PASS — الأدلة التاريخية واختبارات P01 المستهدفة؛ ظهرت روابط المزودين والشحن. | لم نغيّر الربط القائم. |
| رفع ومطابقة كشوف سلة وتمارا وتابي وإمكان منفذ ومختبر آليًا. | [P01 L207](https://github.com/AMASI-SA/AMASI-SA/blob/1f2923f19ba503017a04677636d75caad8e4df91/docs/operations/MZ2-FIN-CUTOVER-001/PHASE-01-SETTLEMENTS.md#L207) | PASS جزئي/تراكمي — أدلة سابقة، واختبار استيراد Preview جديد لإصلاح تابي؛ رفض بقاء الطلبات غير المطابقة. | لا يثبت ذلك مطابقة الكشوف الأصلية أو إنتاج ذمم من الطلبات. |
| المسودة والمطابقة والمراجعة والترحيل منفذة ومختبرة آليًا. | [P01 L208](https://github.com/AMASI-SA/AMASI-SA/blob/1f2923f19ba503017a04677636d75caad8e4df91/docs/operations/MZ2-FIN-CUTOVER-001/PHASE-01-SETTLEMENTS.md#L208) | PASS لمسار التسوية التجريبي السابق؛ اختبارات حزمة P01 الحالية ناجحة. | لم تُعَد مراجعة أو ترحيل التسويات الثلاث. |
| قيد واحد idempotent ومتوازن لكل كشف. | [P01 L209](https://github.com/AMASI-SA/AMASI-SA/blob/1f2923f19ba503017a04677636d75caad8e4df91/docs/operations/MZ2-FIN-CUTOVER-001/PHASE-01-SETTLEMENTS.md#L209) | PASS — الأدلة التجريبية السابقة، واختبارات الهوية الحالية؛ القيود القائمة لم تتغير. | إثبات الذمم من المصدر التشغيلي مسألة منفصلة لم تحل. |
| الإجمالي والعمولة والضريبة والاسترداد والتعديل والصافي ظاهرة. | [P01 L210](https://github.com/AMASI-SA/AMASI-SA/blob/1f2923f19ba503017a04677636d75caad8e4df91/docs/operations/MZ2-FIN-CUTOVER-001/PHASE-01-SETTLEMENTS.md#L210) | PASS — عرض Preview، واختبار حفظ/قراءة/عرض رسوم تابي والاسترداد الكامل. | لا شيء في عرض هذه المكونات. |
| الصلاحيات مستقلة ومختبرة. | [P01 L211](https://github.com/AMASI-SA/AMASI-SA/blob/1f2923f19ba503017a04677636d75caad8e4df91/docs/operations/MZ2-FIN-CUTOVER-001/PHASE-01-SETTLEMENTS.md#L211) | PASS تجريبي — حساب عرض مخصص؛ إخفاء الترحيل ورفض الخادم بلا كتابة مثبتان في نقطة الاستئناف السابقة. | حساب تركي نفسه لم يختبر؛ ليس شرطًا مسمى في الوثيقة. |
| اختبارات Backend ناجحة. | [P01 L212](https://github.com/AMASI-SA/AMASI-SA/blob/1f2923f19ba503017a04677636d75caad8e4df91/docs/operations/MZ2-FIN-CUTOVER-001/PHASE-01-SETTLEMENTS.md#L212) | PASS — 55 اختبارًا مستهدفًا على المرشح المجمع؛ 17 اختبارًا منفصلًا لسكربت Preview. | ليست هذه نتيجة كامل pytest أو كامل النظام. |
| اختبارات Frontend والبناء ناجحة. | [P01 L213](https://github.com/AMASI-SA/AMASI-SA/blob/1f2923f19ba503017a04677636d75caad8e4df91/docs/operations/MZ2-FIN-CUTOVER-001/PHASE-01-SETTLEMENTS.md#L213) | PASS — 20 اختبارًا وVite build كامل مهيأ لـPreview، ثم بناء التركيب الخاص. | بناء/تحقق إصدار Production النهائي مستقل. |
| PR مدمج في Production branch. | [P01 L214](https://github.com/AMASI-SA/AMASI-SA/blob/1f2923f19ba503017a04677636d75caad8e4df91/docs/operations/MZ2-FIN-CUTOVER-001/PHASE-01-SETTLEMENTS.md#L214) | PENDING_RELEASE — الإصلاحات موضوع المراجعة مفتوحة؛ #1091 مرشح Draft مجمع. | المراجعة والدمج لاحقًا؛ لا يمنع قبول اختبار Preview. |
| Runtime منشور ومتحقق عبر release guard. | [P01 L215](https://github.com/AMASI-SA/AMASI-SA/blob/1f2923f19ba503017a04677636d75caad8e4df91/docs/operations/MZ2-FIN-CUTOVER-001/PHASE-01-SETTLEMENTS.md#L215) | PENDING_RELEASE — تطبيق محدود ومتحقق على Preview فقط. | نشر Production والتحقق منه غير مأذونين في هذه الجولة. |
| سيناريوهات المتصفح ناجحة وموثقة. | [P01 L216](https://github.com/AMASI-SA/AMASI-SA/blob/1f2923f19ba503017a04677636d75caad8e4df91/docs/operations/MZ2-FIN-CUTOVER-001/PHASE-01-SETTLEMENTS.md#L216) | PARTIAL — 1/2/3/4/7/8 أدلة تاريخية، 5 حساب بديل ناجح، و6 ترحيل سابق وفتح مجموعة ناجح الآن. | فتح ملف الكشف الأصلي نفسه غير مثبت؛ المعروض اسمه وبياناته المستخرجة. |
| تم تحديث STATUS.json بحالة التنفيذ الحالية. | [P01 L217](https://github.com/AMASI-SA/AMASI-SA/blob/1f2923f19ba503017a04677636d75caad8e4df91/docs/operations/MZ2-FIN-CUTOVER-001/PHASE-01-SETTLEMENTS.md#L217) | PROPOSED — تحديث STATUS وPHASE وCURRENT وجدول التدقيق في #1091. | لا تصبح حالة الفرع المرجعي معتمدة إلا بدمج PR. |
| تم فتح P02؛ يبقى هذا البند مقفلًا حتى نجاح جميع البنود السابقة. | [P01 L218](https://github.com/AMASI-SA/AMASI-SA/blob/1f2923f19ba503017a04677636d75caad8e4df91/docs/operations/MZ2-FIN-CUTOVER-001/PHASE-01-SETTLEMENTS.md#L218) | LOCKED — لم تُفتح P02. | نتيجة لاحقة للبوابة وليست مبررًا لفتحها الآن. |

## Preview versus release

P01 L30 and L108 make bank matching optional/conditional on availability. L188 requires a user without posting permission; it does not name a specific employee. L189 explicitly allows an authorized test record. Synthetic bank evidence and a dedicated viewer therefore qualify for synthetic acceptance. They do not establish agreement with original third-party statements.

README L23–31 separately requires implementation, relevant backend/frontend tests, a production frontend build, merge to the production branch, runtime deployment, production verification, browser scenarios, recorded evidence, and no open acceptance item. The merge/deploy/verification items are release conditions, not reasons to reject authorized Preview fixtures. Historical release evidence remains historical. No new production verification is claimed.

## Operational receivable gap

The Excel settlement import path in backend/settlements_import/service.py saves source rows and updates matched order actual fields; it does not create BNPL sale events or call the sale ledger bridge. Existing provider sync/webhook/catch-up paths can post from payment_transactions using provider/owner/canonical provider ID/status/cutoff checks and idempotency. They are not proof that an imported and matched order automatically becomes a ledger receivable.

PR #1086 is a Preview-only single-sale pilot, supports only Tamara/Emkan, and pins an older base. It is not a general repeatable operator workflow for all three providers. Prior synthetic event preparation demonstrated the bridge and settlement consumption, not repair of the ordinary order-import producer. This root problem remains open.

## Integration order and evidence

- #1082 targets old main; wholesale file replacement loses newer Type-based refund behavior and fee-entry metadata. Reconcile the semantic change on the current production-branch source.
- #1083 depends on #1082 and fixes the test fixture loop; combine it before running the regression.
- #1087 is an independent source-hash conflict guard. #1091 includes its identical guard/test changes so the candidate can be tested together. Merge it first, then recheck #1091's resulting diff, or merge one reviewed consolidated candidate; do not apply the same change twice.
- #1091 integrates the compatible payout detection, preserves the newer refund contract, opens the owner-scoped journal group in a read-only dialog, and excludes provider fee evidence from order-matching requests. Saved snapshots are projected without rewriting financial documents.
- #1085 is Preview restoration tooling based on another Preview branch. Its old pins omit later fixes; do not rerun or merge it blindly. Refresh its pins only after selecting the final accepted composition.
- #1086 remains separate Preview pilot tooling; do not treat merging it as an operational recognition solution.

Combined candidate base: 1f2923f19ba503017a04677636d75caad8e4df91. Code commits: 83f8d8c2bcad2a72faa466659f9a64c8fa108f82, c72a0ee0f3c8413496e85cde19c6724e1cc72f66, 485f2fb17b55dd1a233628cf5a316ac2a80937fd.

Validation: 55 targeted backend cases, 20 frontend cases, full optimized Vite build with Preview configuration; #1086 pilot separately 17 cases. Frontend Jest needs module mappings for the existing @ alias and installed Radix conditional subpath. This is not a claim of full pytest.

The live Preview remains an explicit composition: shared backend base ccc444b684a3f63038048c187e4c20f3de7b1f80, private frontend/source overlay 65f648c5ef83f62bdfd62bd231015f72b4cad266, source-conflict patch #1087, and #1091's scoped parser/journal/matching patches. The existing ledger-state adapter was preserved. It is not described as an exact whole-tree deployment of the combined candidate.

## Browser and runtime evidence

- Reproduced the legacy journal-link failure with both viewer and operator; the in-page scoped dialog now opens the existing posted group with all legs.
- A separate clearly synthetic, unposted draft exercises full-refund classification, commission/VAT rebates and payout fee/VAT through actual Preview upload, saved readback, and UI display.
- The fee row no longer asks for a merchant order match. Actual unmatched sale/refund rows stay blocked.
- The original-source document stores metadata/hash/parsed rows; the observed UI has no original-binary attachment-opening action. Do not count metadata preview as proof of that part of scenario 6.
- Fresh lease checks, local database boundary, Preview identity and rejection of the production Origin are recorded privately. Private before/after snapshots support the no-change assertion for existing financial documents.

## Remaining closure work

1. Provide and verify original-attachment opening for the posted settlement evidence; group viewing is fixed.
2. Complete or explicitly separate the operational order-to-payment-event/receivable producer; the synthetic pilot is not a resolution.
3. Validate one final integrated runtime composition after code review; current live evidence is honestly identified as a Preview overlay composition.
4. Merge the technical evidence/status update and approved runtime changes, then perform the written production release gates only under separate deployment authorization. Keep P02 locked.
