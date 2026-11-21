# تشخيص تعطيل إضافة المخزون رغم التفعيل

المستخدم أكد وجود الدواليب والخانات وأرفق صورة فيها9خانات فارغة. لا يُطلب إنشاء مستودع بديل أو حذف الموجود. صورة الإدخال تعرض أن الكاتب مستقل مفعّل ومعها رسالة إعداد المواقع؛ هذا النص يُعرض فقط عندما تستلم الصفحة قائمة مواقع فارغة، ويعطّل fieldset بصرف النظر عن حالة الكاتب.

## ما ثبت وما يحتاج قراءة

- `StandaloneInventoryEntry.jsx:90,92` يفصل تفعيل الكاتب عن وجود مواقع الإدخال.
- `standalone_inventory_entry.py:36` يقبل المخازن التي تحمل `inventory_scope=standalone` و`status=active` فقط. المخازن القديمة بلا تصنيف أو ذات shared لا تدخل تلقائيًا.
- إنشاء المستودعات الجديد يدعم standalone ويجعله افتراضيًا في الواجهة. لا توجد واجهة لتعديل تصنيف مخزن قائم.
- قيمة الحقل الفعلية للمخزن الحالي لم تُقرأ؛ استبعاده بسبب التصنيف القديم فرضية مرجحة وليست قيمة مؤكدة من قاعدة البيانات. حالة المخزن أو بيانات واجهة قديمة بدائل تحتاج التحقق. لا تحويل تلقائي أو تحديث Mongo مباشر أو تجاوز شرط السيرفر.

## الخطوة التالية للقراءة فقط

افتح من نفس متصفح جلسة ميزان [قائمة المخازن JSON](https://mezansalla.com/api/warehouse-locations/warehouses) وأرسل فقط code وstatus وinventory_scope للمخزن الظاهر. إذا غاب الحقل، اذكر أنه غير موجود. إذا ظهر401/403، أرسل رمز الحالة فقط؛ لا ترسل رموز جلسة أو ملفات بيئة.

بديل اختياري في Console صفحة ميزان نفسها؛ لا يقرأ التخزين المحلي أو رموز الدخول، ولا ينفذ أي POST أو PATCH أو تغيير:

```javascript
(async () => {
  try {
    const response = await fetch('/api/warehouse-locations/warehouses', {credentials: 'include'});
    if (!response.ok) {
      console.log('HTTP', response.status);
      return;
    }
    const data = await response.json();
    if (!Array.isArray(data.items)) {
      console.log('CHECK_FAILED: unexpected response');
      return;
    }
    console.table(data.items.map(warehouse => ({
      code: warehouse.code,
      status: warehouse.status,
      inventory_scope: warehouse.inventory_scope ?? 'غير محدد'
    })));
  } catch (error) {
    console.log('CHECK_FAILED', error.message);
  }
})();
```

أداة CUA تعطلت مجددًا بخطأ مسار kernel؛ لم يحدث فحص مباشر للجلسة. الاختبارات7PASS تشمل منع الإدخال دون مواقع والحفظ الصحيح ومنع التكرار. واجه أمر الاختبار الافتراضي مشكلة اكتشاف بسبب مسارات Windows؛ نجح الأمر المحدد `yarn.cmd test --watchAll=false --runInBand --testMatch=**/StandaloneInventoryEntry.test.jsx` دون تعديل المصدر أو الإعدادات.

## حالة الإصدار

نجحت تجربة المستخدم النظيفة للإصدار السابق A وطابقت artifact/meta/identity ومرتي البناء intent المتتبع. تقدّم Production إلى9f47a47ff16bfa02a8132bd83fe44db3bdb86da0 عبرPR2041، مع الاحتفاظ بدمج المخزون السابق. التغيير الإضافي في استقبال البنك وintent فقط؛ كود الإدخال والمخازن مطابق. الصحة العامة الآن تعلن المصدر d60e2d73f47a59e7a7b919b3dc60e956f0f274a9 وهوية الإصدار rg5-2ca7f1f7c5a05da2a34a1fbcbf46fe6820b3b868af794ed63d2b1fa451966aff. هذا رصد صحة عام وليس تحقق Guard الكامل أو دليل مالك lease. الوكيل لم ينشر أو يعدل مخزونًا أو تصنيف مخازن. لا تستخدم أوامر النشر القديمة المثبتة على5019.
