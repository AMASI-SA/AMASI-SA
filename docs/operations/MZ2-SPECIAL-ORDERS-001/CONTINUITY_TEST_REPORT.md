# تحقق جولة الاستمرارية — 28 سبتمبر 2026

## نتائج نُفذت فعلًا في هذه الجولة

- إعادة حساب بصمة R5 JSON وXZ والقطع الأربع: PASS.
- جلب مصدر GitHub عبر Artifact10981041580، run36445987938، commit `cab173686650356fc6c61037a5603edca8fa1848`، tree `3c4e5df06d6199d160ff7525b7f8b5e519d43004`؛ تحقق2390 ملفًا وفق manifest.
  ZIP SHA256 `9964ae9397962beac5cc92802a7e56ecb62732040279f819f481611b947029f5`.
- تطابق33 بصمة سابقة وتطبيق git apply --check: exit0 على نسخة مصدر معزولة.
- تطبيق R5 في نسخة sandbox ثانية فقط: تطابق33 بصمة لاحقة؛ ast.parse نجح لكل26 ملف Python. هذا فحص تركيب/نقل، لا تشغيل وظيفي.
- recover.py على النسخة السابقة: APPLICABLE_CHECK_ONLY. على النسخة التي طُبّق عليها R5: ALREADY_MATERIALIZED_DO_NOT_REAPPLY. الأداة نفسها لا تطبق patch.
- فاحص أدلة الرجوع: **50 اختبار unittest ناجحًا**، exit0؛ أمثلة اصطناعية، تشمل فرق هللة، تبدل هوية/محتوى مع ثبات العدد أو الإجمالي، نقص أي سطح/كاتب، مخطط/عميل غير متوافق، قالب أو دليل قديم، وغياب اعتماد/تجربة مرتبطين بالهدف.
- القالب التشغيلي المجهول يرجع BLOCKED/exit2؛ يجب ألا يفسر كدليل نجاح.
- الفارق في dependency lock حُصر بسطر pypdf==6.19.0؛ بايتات backend requirements تستخدم لإصلاح lock فقط. حارس النشر والاختبارات وintent لم تتغير.

الأوامر القابلة للإعادة من جذر checkout:

```sh
python -m unittest discover -s docs/operations/MZ2-SPECIAL-ORDERS-001/tools -p 'test_*.py' -v
python docs/operations/MZ2-SPECIAL-ORDERS-001/R5_RECOVERY/recover.py --check .
python docs/operations/MZ2-SPECIAL-ORDERS-001/tools/rollback_gate.py docs/operations/MZ2-SPECIAL-ORDERS-001/ROLLBACK_EVIDENCE.template.json
```

الأمر الأخير **متوقع أن يفشل مغلقًا exit2**. لا تغيّر الاختبار ليصير القالب PASS.

## حدود الدليل

الـ50 تخص برنامجًا offline يفحص حقول الأدلة؛ لا توقف عمليات ولا تثبت صحة مصدر البيانات، وليست تنفيذ رجوع على خادم أو فحص نسخة احتياطية.
لا R5 combined backend CI، ولا React/browser/device acceptance، ولا تجربة rollback/restore مع التشغيل الحقيقي في هذه الجولة.
سجل نتائج CI الجديد إن نُفذ سيذكر في تعليق Issue1006 بعد قراءة النتيجة. لا ننقل نجاح R4 إلى R5 أو إلى شجرة أحدث افتراضًا.
جميع ملفات اللقطات التشغيلية الحقيقية تبقى في مخزن خاص؛ أمثلة الوحدة والنتائج هنا منقحة وخالية من عملاء حقيقيين.
