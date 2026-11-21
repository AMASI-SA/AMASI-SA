# النشر اليدوي — مخزون ميزان 2

> **تجاوزته نسخة أحدث:** تقدّم فرع الإنتاج إلى9f47a47ff16bfa02a8132bd83fe44db3bdb86da0 وأصبحت الصحة العامة تعلن المصدر الأحدث الذي يتضمن المخزون. أوامر5019 أدناه سجل تاريخي فقط؛ لا تنفذها أو تعِد نشر الإصدار السابق. المتابعة الحالية في ENTRY-LOCATION-DIAGNOSIS.md لتشخيص مواقع المخزن دون كتابة بيانات.

> **التحديث الحالي:** نجح التشخيص الأول في المخرجات التي أرسلها المستخدم: Guard غير نشط، لا تعديلات متتبعة، فرع الإنتاج عند commit الصحيح والأوامر المطلوبة متاحة. سبب فشل المحاولة الأولى ما زال غير ظاهر. الخطوة التالية هي `manual-rehearsal.sh` فقط لالتقاط نتيجة الوصول إلى GitHub والبناء في نسخة مؤقتة؛ لا تستأنف التحضير المشترك أو النشر حتى نجاح هذه الخطوة.

> **توقف التحضير:** أفاد المستخدم بأن الطرفية أُغلقت مباشرة برمز 1. سبب فشل الخطوة غير معروف؛ لا تعِد البناء أو التحضير أو النشر الآن. افتح طرفية جديدة وشغّل `manual-terminal-diagnostic.sh` للقراءة فقط، ثم شارك نص المخرجات. الصيغة أدناه عُدلت لتشغيل التحضير داخل Bash منفصل مع حفظ الخطأ وعدم إغلاق الطرفية، لكن لا تستأنفها قبل تشخيص الخطأ الأصلي.

تم دمج [PR #2038](https://github.com/AMASI-SA/AMASI-SA/pull/2038) مع الحفاظ على مصدر الإصدار والملف المعتمد. GitHub يحتوي التغيير؛ الوكيل لم ينشر الموقع أو يلمس `/app` أو ينشئ lease أو يغيّر بيانات المخزون والمحاسبة. آخر فحص صحة للقراءة فقط ما زال يعرض الإصدار السابق.

- فرع الإنتاج: `hotfix/prod-snap-meta-final`
- commit النشر D: `5019e957cd1bb578f7b7375099547a470e51cb05`
- المصدر المراجع A: `03dce00590c565a125ee0219537c86abeeb76819`
- intent B: `a03d05d474d1c0a5fba879c7706761d8f94f5d58`
- هوية الإصدار المتوقعة: `rg5-270cec5290c45a86904ee96b2b2b958625091c630b5bac2bf1a9fde9f7c8a429`
- [فحص البناء والنشر المراجع](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44910528896): ناجح. جميع فحوص B اكتملت: 68 ناجحًا و7 وظائف شرطية غير منطبقة؛ لا إخفاقات.

## 1. التحضير داخل Emergent

انسخ الكود التالي إلى Terminal بصيغة Bash في مشروع Emergent الحالي. لا تحفظ السكربت كملف جديد ضمن مصدر `/app`؛ يمكن لصقه أو تشغيل نسخة مؤقتة خارجه. السكربت مدقق نحويًا محليًا فقط؛ لم ينفذه الوكيل في `/app`. لا تنفذ أي خطوة تالية إذا توقفت خطوة أو ظهر خطأ.

يلزم أن يكون `/app` نسخة Git للمشروع، وأن تتوفر أوامر Python وGit وYarn. أول خطوة تتحقق من أن Release Guard غير نشط. عند وجود lease قديم أو نشط، يتولى مالكه إغلاقه بالأداة التي أنشأته؛ لا تمسحه. التعديلات المحلية على الملفات المتتبعة أو تقدم فرع الإنتاج سيوقفان الخطوات لحين المراجعة.

```bash
bash <<'MEZAN_RELEASE' 2>&1 | tee /tmp/mezan-inventory-release.log || printf '%s\n' 'STOP: preparation failed; do not publish. Preserve the log.'
#!/usr/bin/env bash
set -Eeuo pipefail
trap 'printf "STOP: line %s, exit %s; do not publish.\n" "$LINENO" "$?" >&2' ERR
cd /app

release_sha='5019e957cd1bb578f7b7375099547a470e51cb05'

guard_idle() {
  python scripts/production_release_guard.py status | python -c '
import json, sys
state = json.load(sys.stdin)
print(json.dumps(state, indent=2))
if state.get("active") is not False:
    raise SystemExit("STOP: active release; contact its owner. Do not clear it.")
'
}

guard_idle
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  printf '%s\n' 'STOP: tracked files have local changes. Preserve them before continuing.' >&2
  exit 1
fi
git fetch origin hotfix/prod-snap-meta-final
if [ "$(git rev-parse origin/hotfix/prod-snap-meta-final)" != "$release_sha" ]; then
  printf '%s\n' 'STOP: Production Git advanced. This release must be reviewed again.' >&2
  exit 1
fi
git switch hotfix/prod-snap-meta-final
git merge --ff-only "$release_sha"
test "$(git rev-parse HEAD)" = "$release_sha"

trial_dir="$(mktemp -d /tmp/mezan-inventory-v5.XXXXXX)"
git clone --local --no-hardlinks /app "$trial_dir"
(
  cd "$trial_dir"
  git checkout --detach "$release_sha"
  REACT_APP_BACKEND_URL=https://mezansalla.com yarn --cwd frontend build
)

guard_idle
test "$(git rev-parse HEAD)" = "$release_sha"
REACT_APP_BACKEND_URL=https://mezansalla.com yarn --cwd frontend build
python scripts/production_release_guard.py prepare --actor inventory-ui-manual-20261121
python scripts/production_release_guard.py prepublish
printf '%s\n' 'PREPUBLISH PASSED. Manually choose Re-publish changes now.'
printf '%s\n' 'After a NEW Deployment Succeeded, run the separate verify commands.'
MEZAN_RELEASE
```

التجربة النظيفة تجري خارج `/app` ثم يُبنى الإصدار في `/app` عبر dispatcher المعتمد. المكيف يثبت الأدوات المعتمدة Node22.23.2 وYarn1.22.22، وينفذ البناء الحتمي والتحقق من intent وحدود الحزم. ينظف مخرجات البناء المولدة التابعة له فقط؛ لا تفتح أو تنسخ أو تطبع `.env` أو تستخدم `git reset --hard` أو تمسح ملفات المشروع غير المتعلقة بالمهمة. لا تغيّر أو تعيد صياغة A/B، ولا تضف مصدرًا أو intent جديدًا لهذا الإصدار. احتفظ بمخرجات المكيف المنقحة.

## 2. النشر اليدوي

بعد نجاح `prepublish` مباشرة اختر **Re-publish changes** مرة واحدة في Emergent. لا تختَر Preview ولا تفعّل أعلام ترحيل المخزون أو صلاحيات الاعتماد/الترحيل أو أي عملية تشغيلية. انتظر ظهور رسالة جديدة صريحة **Deployment Succeeded** لهذا النشر. احتفظ بدليلها وبمخرجات Cloud Build المتاحة، خصوصًا أوامر dispatcher والمنصة المنقحة. نجاح GitHub لا يثبت أن Cloud Build نشر هذا الإصدار.

إذا فشل التحضير أو النشر، احتفظ بالمخرجات المنقحة وتوقف؛ لا تعدّل الهوية أو intent لتجاوز الحماية ولا تغلق lease يخص جلسة أخرى. أوامر التحضير لا تُعاد أثناء وجود lease نشط.

## 3. التحقق بعد نجاح النشر

نفذ هذه الأوامر فقط بعد رسالة النجاح الجديدة:

```bash
set -euo pipefail
cd /app
python scripts/production_release_guard.py verify --url https://mezansalla.com
python scripts/production_release_guard.py status
```

النجاح يتطلب ثلاثة فحوص متتالية بهوية `rg5-270cec5290c45a86904ee96b2b2b958625091c630b5bac2bf1a9fde9f7c8a429` والمصدر `03dce00590c565a125ee0219537c86abeeb76819` مع `verified_identity_available=true` و`critical_file_hashes_match=true` و`frontend_build_verified=true`، وJSON مطابقًا من `/build-meta.json` بنوع `application/json` في الطلب العادي والمتجاوز للتخزين المؤقت. بعدها يجب أن تكون حالة Guard `active: false`. عرض HTML بدل JSON إخفاق. لا تدّع اكتمال النشر قبل هذه الأدلة.

بعد التحقق، حدّث قسم INV-UI-20261120 في Issue #1006 وSTATUS.json على فرع الاستمرارية `codex/inventory-ui-replacement-20261120` فقط. لا تضف توثيقًا جديدًا بعد A على فرع الإصدار المجمّد. هذا التسليم يغيّر واجهات المخزون ومسار التطوير المراجع؛ تفعيل الترحيل الفعلي وأي تغيير كميات أو قيم أو صلاحيات إنتاج يحتاج قرارًا منفصلًا، ولم يُنفّذ. لا توجد مطالبة بتحقق بصري من مطابقة الموقع الحالي. راجع CHECKPOINT.md وSTOCKTAKE-CONTRACT.md للحدود المعتمدة.
