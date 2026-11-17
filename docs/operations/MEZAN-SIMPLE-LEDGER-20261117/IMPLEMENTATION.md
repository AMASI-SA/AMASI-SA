# Mezan simple balances and employee permissions

## Scope and source

This change implements the owner's approved Arabic balances workflow in the actual web/backend and standalone native app. It uses the existing operational balance aggregate and movement service. It does not introduce a parallel balance store or execute bank/provider transfers.

- Backend/web PR: https://github.com/AMASI-SA/AMASI-SA/pull/1966
- Native PR: https://github.com/AMASI-SA/amasi-mobile/pull/359
- Backend base: `hotfix/prod-snap-meta-final` at `71125711d8181da3b4e35d9129fd51c048ba2bdd`.
- Native base: `feature/operational-order-compensation-20261101` at `982a38a60ea77ef09f4e2924766c085ea0523bfd`. The standalone operational app must not be replaced with the assembly/staff app from another branch.

## تجربة المستخدم

الأقسام الأساسية: **الحركات، الأطراف، الحسابات**. صفحة الطرف تعرض رصيده الكامل عندما تسمح الصلاحية، وجدول خمس حركات في كل صفحة، وعمودي **له** بالأحمر و**عليه** بالأخضر. اللون يصاحبه نص. حفظ المبلغ يعيد المستخدم إلى كشف الطرف ويفتح الصفحة التي تحتوي الحركة الجديدة حتى لو كان تاريخها سابقًا.

اسم **مسجّل الحركة** يأتي من الحساب الذي نفذ الحفظ، ويُحفظ مع الحركة؛ لا يُؤخذ من الموظف المستفيد أو الموظف صاحب العهدة. السجل القديم الذي لا يحمل اسمًا موثقًا يعرض «غير موثق».

إضافة مبلغ لجهة خارجية:

- **عليه:** تسليم مبلغ للجهة، مع اختيار بنك المؤسسة أو الصندوق أو عهدة الموظف المسموح بها.
- **له:** تحديد السبب، مثل استلام مبلغ أو تسوية راتب أو صدقات المنشأة أو المالك أو المصروفات المنزلية أو الشخصية. الحقول التابعة تظهر حسب السبب.
- صرف راتب من جهة خارجية يسجل ساق الجهة وساق الموظف في عملية واحدة، ويحد المبلغ بصافي المستحق. لا يسجل راتبًا جديدًا ولا حركة بنك إضافية.
- المصروفات المنزلية والشخصية تتبع سحوبات المالك، وتُفصل عن المصروفات التشغيلية. تمويل الإعلانات منفصل عن الصرف الوارد من مصدر الإعلانات اليومي.

## إعداد الصلاحيات

من **صلاحيات الموظفين** يختار المالك الموظف ثم يحدد كل حساب أو جهة. هذه القرارات مستقلة:

| الإعداد | الخيارات |
| --- | --- |
| الدخول | مفعّل أو متوقف |
| استخدام الحساب أو الجهة | مسموح أو غير مسموح |
| الحركات المعروضة | لا يعرضها، التي سجلها بنفسه، حركات جميع الموظفين |
| الرصيد الكامل | ظاهر أو مخفي |
| العمليات | اختيار عمليات محددة من القائمة، مثل دفع المورد أو استلام مبلغ أو التحويل أو تسجيل استرجاع |

مثال: إعطاء موظف بنك الإنماء وموردًا واحدًا، مع عملية دفع المورد، وحركاته فقط، وإخفاء الرصيد. يجب اختيار الطرف ومصدر المبلغ معًا. اختيار البنك وحده لا يمنح عملية الدفع، واختيار العملية لا يمنح أي بنك إضافي.

الصلاحيات ملزمة من الخادم في القراءة والحفظ، بما فيها الحسابات المرتبطة بالعملية. الروابط المباشرة والمسارات القديمة لا تتجاوزها. صلاحيات التطبيق القديمة لا تمنح الوصول تلقائيًا إلى هذه المساحة؛ يحتاج الموظفون إلى سياسات صريحة يحددها المالك. يظل المالك قادرًا على فتح المسارات المتخصصة للمخزون والمشتريات والمرتجعات والتقارير.

## Order refunds

Search uses the order number; the staff member does not need to know the customer's name. The refundable amount is derived from confirmed collected money, prior refunds and pending reservations. Requests do not reduce bank balances.

The method starts with **اختر طريقة الاسترجاع**. The choices are a company bank transfer and, when supported and authorized, the actual provider used by that order (Tamara, Tabby or Salla). No customer IBAN or recipient bank-account field is collected.

- Bank confirmation records a transfer already executed outside this application. It saves the authenticated confirming user and actual execution date; a bank reference is optional. The existing canonical return calculation supplies the single bank debit.
- Provider reconciliation links the request to an existing canonical provider refund fact, validates its fingerprint and prevents reuse. It does not create a provider payment, bank movement or second refund fact.
- A provider execution date is shown only when the actual evidence supplies it. The matching timestamp is recorded separately.
- A pending request can be cancelled to release its reservation. Definitive business rejections are persisted; ambiguous responses retain the same request ID for retry.

## Verification and release boundary

The companion PRs contain actual implementation and regression tests. The web CI runs the existing operational test collection against an isolated Mongo replica set, the new refund contracts, existing component tests and an added browser fixture that imports the real screens at 390px and 1440px. The fixture uses synthetic transport and records its source hashes; it is not a production preview.

See `STATUS.json` and the PR checks for the checkpoint's verification state. Local synthetic tests are explicitly distinguished from real-Mongo and browser acceptance. No test fixture imports a live production server or uses live financial records.

Release requires the compatible scoped backend and clients together, explicit staff grants, the repository's governed release process and device acceptance. This source change does not create a production lease, merge the PRs, publish an OTA update or build/sign an APK.
