# نشر إصلاح المخازن القديمة — ميزان

تأكد السبب من بيانات المستخدم: سجل مخزن قديم نشط لا يحمل inventory_scope، فكانت شاشة الإدخال تخفي خاناته. الإصلاح يستخدم الخانات الموجودة دون تغيير تصنيف المخزن أو الأكواد أو أي كمية حالية. المخازن المصنّفة صراحة shared تبقى مستبعدة، وفحوص المخزون المختلط والصلاحيات ومنع التكرار والترحيل المالي تبقى قائمة.

تم دمج [PR #2045](https://github.com/AMASI-SA/AMASI-SA/pull/2045) بشكل عادي مع الحفاظ على A/B.101اختبار محلي ناجح، و23فحصًا ناجحًا للإصدار B و6وظائف شرطية غير منطبقة؛ لا إخفاقات. [فحوص الإصدار والبناء النظيف](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44920818343) ناجحة لكلا المضيفين Node20/24.

- فرع الإنتاج: hotfix/prod-snap-meta-final
- commit النشر D: `c7d97f3cdf1ebcd99ed8a86975c463d9729b9d73`
- المصدر A: `56af6ab9d997688311f339eab8d2ca12f6a0e034`
- intent B: `71c53711796e972b9cdca4b1c7258709807be05e`
- هوية الإصدار: `rg5-f6614ad3eee05b048bc4b3b606d6a8fdb602438f8ee0db19ef61273b91e1eaac`

الوكيل لم ينشر أو يلمس /app أو lease أو بيانات تشغيلية. الصحة العامة وقت التسليم ما زالت تعلن الإصدار السابق. لا يُعد دمج GitHub نشرًا. تعليمات الإصدار السابق5019 تاريخية ولا تُستخدم.

## التحضير اليدوي

داخل Terminal في Emergent الصق هذا المقطع كاملًا. ينفذ داخل Bash منفصل كي لا يغلق الطرفية عند الخطأ؛ يحفظ المخرجات المنقحة في /tmp/mezan-legacy-location-release.log. لا تحفظ السكربت كملف جديد ضمن مصدر /app المراجع. أول أمر يتحقق من Guard؛ عند active:true توقف واترك الإغلاق لمالك العملية. التعديلات المتتبعة أو تقدم GitHub عن D يوقفان التحضير. لا reset أو clean ولا تعديل أو طباعة ملفات البيئة.

```bash
bash <<'MEZAN_RELEASE' 2>&1 | tee /tmp/mezan-legacy-location-release.log || printf '%s\n' 'STOP: do not publish; preserve the last error lines.'
set -Eeuo pipefail
step='initialization'
trap 'printf "\nFAILED: step=%s line=%s exit=%s\n" "$step" "$LINENO" "$?" >&2' ERR
cd /app
release_sha='c7d97f3cdf1ebcd99ed8a86975c463d9729b9d73'

guard_idle() {
  python scripts/production_release_guard.py status | python -c '
import json, sys
state = json.load(sys.stdin)
print(json.dumps(state, indent=2))
if state.get("active") is not False:
    raise SystemExit("STOP: active release; contact its owner.")
'
}

step='guard'
guard_idle
step='tracked_source'
git status --short --untracked-files=no
test -z "$(git status --porcelain --untracked-files=no)"
step='github_fetch'
GIT_TERMINAL_PROMPT=0 git fetch origin hotfix/prod-snap-meta-final
test "$(git rev-parse origin/hotfix/prod-snap-meta-final)" = "$release_sha"
git switch hotfix/prod-snap-meta-final
git merge --ff-only "$release_sha"
test "$(git rev-parse HEAD)" = "$release_sha"

step='clean_clone_rehearsal'
trial_dir="$(mktemp -d /tmp/mezan-legacy-location-v5.XXXXXX)"
git clone --local --no-hardlinks /app "$trial_dir"
(
  cd "$trial_dir"
  git checkout --detach "$release_sha"
  REACT_APP_BACKEND_URL=https://mezansalla.com yarn --cwd frontend build
)

step='shared_materialization'
guard_idle
test "$(git rev-parse HEAD)" = "$release_sha"
REACT_APP_BACKEND_URL=https://mezansalla.com yarn --cwd frontend build
step='prepare'
guard_idle
test "$(git rev-parse HEAD)" = "$release_sha"
python scripts/production_release_guard.py prepare --actor inventory-legacy-manual-20261121
step='prepublish'
python scripts/production_release_guard.py prepublish
printf '\nPREPUBLISH_PASSED: choose Re-publish changes now.\n'
MEZAN_RELEASE
```

البناء الأول تجربة نظيفة في نسخة مؤقتة، ثم يعاد فحص Guard قبل materialization في /app. المكيف المعتمد يزيل مخرجاته المولدة فقط، ويستخدم Node22.23.2 وYarn1.22.22 لبناء A/B حتمي ومطابقة intent والتحقق من حدود الحزم. لا تنسخ مخرجات Windows أو تفرض إضافة أي ملفات تجاهلها Git. احتفظ بمخرجات المكيف. السكربت مدقق نحويًا محليًا؛ لم ينفذه الوكيل في /app. لا تكرر التحضير إذا أصبح lease نشطًا.

## النشر

فقط بعد ظهور **PREPUBLISH_PASSED**، اختر **Re-publish changes** مرة واحدة مباشرة. لا تختَر Preview ولا تفعل ضوابط المخزون المحاسبي أو التشغيل ولا تغير تصنيف المخزن أو أي كميات/قيم. انتظر رسالة جديدة صريحة **Deployment Succeeded** لهذا النشر. احتفظ بدليلها وبمخرجات Cloud Build المتاحة؛ نجاح تجارب CI ليس إثباتًا لتسليم المنصة الفعلي. إذا توقف السكربت لا تنشر؛ أرسل آخر سطور الخطأ بما فيها FAILED step/line/exit.

## التحقق بعد رسالة النجاح الجديدة

```bash
bash <<'MEZAN_VERIFY' 2>&1 | tee /tmp/mezan-legacy-location-verify.log || printf '%s\n' 'STOP: verification failed; preserve the log.'
set -euo pipefail
cd /app
python scripts/production_release_guard.py verify --url https://mezansalla.com
python scripts/production_release_guard.py status
MEZAN_VERIFY
```

النجاح يتطلب3فحوص متتالية بهوية `rg5-f6614ad3eee05b048bc4b3b606d6a8fdb602438f8ee0db19ef61273b91e1eaac` والمصدر `56af6ab9d997688311f339eab8d2ca12f6a0e034`، مع verified_identity_available=true وcritical_file_hashes_match=true وfrontend_build_verified=true. يجب أن يعيد build-meta.json بايتات JSON المطابقة بنوع application/json للطلب العادي والمتجاوز للتخزين المؤقت؛ HTML إخفاق. Guard ناجح يغلق lease المطابق ثم status يعرض active:false. بعدها حدّث صفحة إضافة المخزون لرؤية مواقع المخزن الموجودة؛ لا تنفذ حفظًا حيًا لمجرد الاختبار.

حدّث سجل INV-UI-20261120 في Issue #1006 وSTATUS على فرع الاستمرارية codex/inventory-ui-replacement-20261120 بعد الدليل. لا تضف مصدرًا أو توثيقًا بعد A على فرع الإصدار المجمّد ولا تعد صياغة A/B. معلومات العميل الخاصة لم تُحفظ في الدليل.
