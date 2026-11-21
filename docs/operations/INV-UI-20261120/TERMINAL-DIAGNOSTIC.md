# تشخيص إغلاق الطرفية

الصورة تعرض إغلاق `/bin/bash` برمز 1 فقط. لا تثبت الأمر الذي فشل أو حالة guard أو التعديلات المحلية. تأكد محليًا أن لصق `set -e` داخل الطرفية يجعل أي أمر فاشل ينهيها، وأُصلحت طريقة عرض سكربت التحضير لتعمل في Bash منفصل. هذا لا يعالج شرط الفشل غير المعروف ولا يتجاوزه.

افتح طرفية جديدة في Emergent وشغّل التالي فقط. لا بناء أو تحديث Git أو تحضير lease أو نشر أو كتابة بيانات تشغيلية. يحفظ المخرجات في `/tmp/mezan-inventory-check.log`، ويبقي الطرفية مفتوحة حتى لو فشل الفحص. عدم نجاح guard لا يمنع قراءات Git التالية؛ لا توجد أي خطوة نشر في هذا التشخيص.

```bash
bash <<'MEZAN_CHECK' 2>&1 | tee /tmp/mezan-inventory-check.log || true
set +e
if ! cd /app; then
  printf '%s\n' 'Cannot open /app'
  exit 0
fi
printf '\n--- Release Guard ---\n'
python scripts/production_release_guard.py status
printf 'GUARD_EXIT=%s\n' "$?"
printf '\n--- Tracked changes ---\n'
git status --short --untracked-files=no
printf '\n--- Branch and commit ---\n'
git branch --show-current
git rev-parse HEAD
printf '\n--- Required commands ---\n'
command -v python python3 git yarn
printf '\n--- End: diagnosis only, no build or publish ---\n'
exit 0
MEZAN_CHECK
```

أرسل نص المخرجات. يبقى النشر متوقفًا حتى تحديد السبب. لا تحذف lease ولا تستخدم reset أو clean ولا تفتح ملفات البيئة أو تنشر قيمها. لا يُعد نجاح غلاف Bash الخارجي دليل جاهزية للإصدار؛ الجاهزية تتطلب المرور الفعلي لكل شروط البروتوكول ثم prepublish والاختبارات الحية المعتمدة.
