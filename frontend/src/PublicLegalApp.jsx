import { useEffect } from "react";

const SUPPORT_EMAIL = "support@amasi-sa.com";
const COMPANY_AR = "مؤسسة أماسي الخليج التجارية";
const COMPANY_EN = "Establishment AMASI AL-KHALIJ Commercial";
const UPDATED_AR = "31 يوليو 2026";
const UPDATED_EN = "31 July 2026";

const LEGAL_PATHS = new Set([
    "/privacy-policy",
    "/data-deletion",
    "/terms",
]);

export function normalizeLegalPath(pathname) {
    const value = String(pathname || "/").split(/[?#]/, 1)[0] || "/";
    if (value.length > 1 && value.endsWith("/")) return value.slice(0, -1);
    return value;
}

export function isPublicLegalPath(pathname) {
    return LEGAL_PATHS.has(normalizeLegalPath(pathname));
}

function NavLink({ href, children, active }) {
    return (
        <a
            href={href}
            className={`rounded-full px-3 py-2 text-xs font-extrabold transition sm:text-sm ${
                active
                    ? "bg-emerald-950 text-white"
                    : "border border-slate-200 bg-white text-slate-700 hover:border-emerald-300 hover:text-emerald-900"
            }`}
        >
            {children}
        </a>
    );
}

function Section({ title, children }) {
    return (
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-7">
            <h2 className="text-lg font-black text-slate-950 sm:text-xl">{title}</h2>
            <div className="mt-3 space-y-3 text-sm font-medium leading-7 text-slate-700 sm:text-[15px]">
                {children}
            </div>
        </section>
    );
}

function List({ children }) {
    return <ul className="list-disc space-y-2 pe-5">{children}</ul>;
}

function ContactCard() {
    return (
        <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-5 text-sm leading-7 text-emerald-950">
            <div className="font-black">التواصل بخصوص الخصوصية والبيانات</div>
            <div className="mt-1">Privacy and data contact</div>
            <a
                className="mt-2 inline-flex rounded-lg bg-white px-3 py-2 font-mono font-bold text-emerald-900 shadow-sm"
                href={`mailto:${SUPPORT_EMAIL}`}
            >
                {SUPPORT_EMAIL}
            </a>
        </div>
    );
}

function LegalShell({ path, titleAr, titleEn, intro, children }) {
    return (
        <main className="min-h-screen bg-slate-50 text-slate-950" dir="rtl">
            <header className="border-b border-slate-200 bg-white">
                <div className="mx-auto flex max-w-5xl flex-col gap-4 px-4 py-5 sm:flex-row sm:items-center sm:justify-between sm:px-6">
                    <a href="/" className="min-w-0">
                        <div className="text-xl font-black tracking-tight text-emerald-950">Mezan OS</div>
                        <div className="mt-0.5 text-xs font-bold text-slate-500">نظام ميزان · Amasi Analytics</div>
                    </a>
                    <nav className="flex flex-wrap gap-2" aria-label="الصفحات النظامية">
                        <NavLink href="/privacy-policy" active={path === "/privacy-policy"}>سياسة الخصوصية</NavLink>
                        <NavLink href="/data-deletion" active={path === "/data-deletion"}>حذف البيانات</NavLink>
                        <NavLink href="/terms" active={path === "/terms"}>شروط الاستخدام</NavLink>
                    </nav>
                </div>
            </header>

            <div className="mx-auto max-w-5xl space-y-5 px-4 py-8 sm:px-6 sm:py-12">
                <section className="overflow-hidden rounded-3xl border border-emerald-900 bg-emerald-950 p-6 text-white shadow-lg sm:p-9">
                    <div className="text-xs font-extrabold uppercase tracking-[0.18em] text-emerald-300">Mezan OS Legal</div>
                    <h1 className="mt-3 text-3xl font-black tracking-tight sm:text-4xl">{titleAr}</h1>
                    <div className="mt-2 text-lg font-bold text-emerald-200">{titleEn}</div>
                    <p className="mt-5 max-w-3xl text-sm font-medium leading-7 text-emerald-50 sm:text-base">{intro}</p>
                    <div className="mt-5 text-xs font-bold text-emerald-300">
                        آخر تحديث: {UPDATED_AR} · Last updated: {UPDATED_EN}
                    </div>
                </section>

                {children}

                <ContactCard />
                <footer className="pb-5 text-center text-xs font-semibold leading-6 text-slate-500">
                    <div>{COMPANY_AR}</div>
                    <div dir="ltr">{COMPANY_EN}</div>
                    <div className="mt-1">المملكة العربية السعودية · Kingdom of Saudi Arabia</div>
                </footer>
            </div>
        </main>
    );
}

function PrivacyPolicy({ path }) {
    return (
        <LegalShell
            path={path}
            titleAr="سياسة الخصوصية"
            titleEn="Privacy Policy"
            intro="توضح هذه السياسة كيف يجمع نظام ميزان البيانات ويستخدمها ويحميها عند تشغيل خدمات المتجر والتكاملات الإعلانية، بما فيها ربط Meta للأصول والحسابات المصرح بها."
        >
            <Section title="1. الجهة المسؤولة ونطاق السياسة">
                <p>
                    الجهة المسؤولة عن معالجة البيانات في نظام ميزان هي <strong>{COMPANY_AR}</strong>. يعمل
                    Mezan OS حاليًا كنظام داخلي لإدارة العمليات والتحليلات والتكاملات المصرح بها لمتجر أماسي
                    والمستخدمين المخولين منه.
                </p>
                <p>
                    تنطبق هذه السياسة على موقع <span dir="ltr" className="font-mono">mezansalla.com</span>،
                    وتسجيل الدخول، وربط منصات التجارة والإعلانات، والصفحات والخدمات المرتبطة بنظام ميزان.
                </p>
            </Section>

            <Section title="2. البيانات التي قد نجمعها">
                <List>
                    <li>بيانات الحساب: الاسم، البريد الإلكتروني، الدور والصلاحيات، ومعرّفات المصادقة والجلسة.</li>
                    <li>بيانات المتجر والعمليات التي يربطها المستخدم المخول، مثل الطلبات والمنتجات والشحن والتسويات.</li>
                    <li>
                        بيانات تكامل Meta والمنصات الإعلانية: معرّفات الأعمال والحسابات الإعلانية، أسماء الحسابات،
                        العملة والمنطقة الزمنية، الصلاحيات الممنوحة، الحملات والتقارير ومؤشرات الأداء والتحويلات.
                    </li>
                    <li>
                        رموز الوصول اللازمة للتكاملات. تُخزن رموز الوصول الحساسة مشفرة على الخادم ولا تُعرض في
                        المتصفح أو الاستجابات العامة.
                    </li>
                    <li>بيانات تقنية وأمنية، مثل سجلات التشغيل، أوقات الطلبات، الأخطاء، عنوان IP ومعلومات الجهاز عند توفرها للبنية المستضيفة.</li>
                    <li>المراسلات وطلبات الدعم والخصوصية التي يرسلها المستخدم.</li>
                </List>
            </Section>

            <Section title="3. مصادر البيانات وطرق جمعها">
                <p>نجمع البيانات مباشرة عندما ينشئ المستخدم حسابًا أو يرسل طلب دعم، ومن الأنظمة التي يربطها المستخدم ويمنحها صلاحية صريحة، مثل Meta وSalla ومنصات الإعلانات.</p>
                <p>قد تُجمع بعض البيانات التقنية تلقائيًا بواسطة الخادم أو مزود الاستضافة لحماية الخدمة وتشخيص الأعطال.</p>
            </Section>

            <Section title="4. أغراض المعالجة">
                <List>
                    <li>تسجيل الدخول والتحقق من الهوية وتطبيق الأدوار والصلاحيات.</li>
                    <li>تشغيل التكاملات التي يطلبها المستخدم، واكتشاف الحسابات المصرح بها ومزامنة التقارير.</li>
                    <li>تقديم التحليلات ومقارنة الأداء ومراقبة جودة البيانات وأمان الربط.</li>
                    <li>تقديم الدعم، والتحقيق في الأخطاء، ومنع الاحتيال والوصول غير المصرح به.</li>
                    <li>الوفاء بالمتطلبات النظامية والتعاقدية والمحاسبية عند انطباقها.</li>
                    <li>تحسين موثوقية النظام دون استخدام بيانات العملاء لتدريب نماذج عامة أو مشاركتها لأغراض إعلانية خارجية.</li>
                </List>
            </Section>

            <Section title="5. المسوغ النظامي">
                <p>
                    تتم المعالجة وفق المسوغ النظامي المنطبق على كل غرض، مثل موافقة صاحب البيانات عند لزومها،
                    تنفيذ العلاقة التعاقدية، الوفاء بالتزام نظامي، حماية المصالح الحيوية أو الأمنية، أو المصالح
                    المشروعة ضمن الحدود والضوابط النظامية.
                </p>
            </Section>

            <Section title="6. مشاركة البيانات والمعالجة خارج المملكة">
                <p>لا نبيع البيانات الشخصية. قد نفصح عن الحد الأدنى اللازم في الحالات الآتية:</p>
                <List>
                    <li>لمزودي الاستضافة والأمن والدعم التقني الذين يعملون لمصلحة النظام وتحت التزامات حماية مناسبة.</li>
                    <li>للمنصات التي يختار المستخدم ربطها، مثل Meta، لتنفيذ طلب التفويض وجلب البيانات المصرح بها.</li>
                    <li>للجهات المختصة عندما يوجب النظام ذلك أو لحماية الحقوق والأمن.</li>
                </List>
                <p>
                    قد تتضمن بعض التكاملات معالجة أو نقلًا تقنيًا خارج المملكة بحسب بنية مزود المنصة. نقيّد ذلك
                    بالغرض المطلوب ونتخذ الضمانات المتاحة وفق الأنظمة المنطبقة.
                </p>
            </Section>

            <Section title="7. مدة الاحتفاظ والإتلاف">
                <p>
                    نحتفظ بالبيانات طوال المدة اللازمة لتشغيل الخدمة وتحقيق الغرض الذي جُمعت من أجله، أو للمدة
                    المطلوبة نظامًا أو تعاقديًا. تُحذف رموز ربط Meta والبيانات المرتبطة بالمستخدم عند قبول طلب
                    الحذف أو إنهاء الربط، ما لم يلزم الاحتفاظ بجزء محدد لأغراض نظامية أو أمنية أو لإثبات المعاملات.
                </p>
                <p>عند انتهاء الحاجة، يتم حذف البيانات أو إخفاء هويتها أو إتلافها بطريقة آمنة بحسب طبيعة التخزين.</p>
            </Section>

            <Section title="8. حماية البيانات">
                <p>
                    نستخدم ضوابط وصول قائمة على الأدوار، وتشفيرًا للأسرار ورموز الوصول الحساسة، وسجلات تدقيق،
                    وفصلًا بين بيانات المستخدمين، واختبارات تمنع الكتابة غير المقصودة إلى المنصات أو الأنظمة المحاسبية.
                    لا توجد وسيلة إلكترونية خالية تمامًا من المخاطر، لذلك نراجع الضوابط باستمرار.
                </p>
            </Section>

            <Section title="9. حقوق صاحب البيانات">
                <p>وفق الأنظمة المنطبقة، يمكن لصاحب البيانات طلب:</p>
                <List>
                    <li>العلم بكيفية جمع بياناته والغرض والمسوغ النظامي.</li>
                    <li>الوصول إلى بياناته والحصول عليها بصيغة مقروءة وواضحة.</li>
                    <li>تصحيح البيانات أو إكمالها أو تحديثها.</li>
                    <li>إتلاف البيانات عندما لا يعود الاحتفاظ بها لازمًا، مع مراعاة الاستثناءات النظامية.</li>
                    <li>العدول عن الموافقة عندما تكون الموافقة هي أساس المعالجة.</li>
                    <li>تقديم شكوى إلى الجهة المختصة عند تعذر ممارسة الحقوق.</li>
                </List>
                <p>
                    تُرسل الطلبات إلى <a className="font-bold text-emerald-800 underline" href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>.
                    قد نطلب معلومات للتحقق من الهوية. نتصرف على الطلب خلال مدة لا تتجاوز 30 يومًا، وقد تُمدد
                    المدة عند الحاجة وفق الأنظمة مع إشعار مقدم الطلب بالأسباب.
                </p>
            </Section>

            <Section title="10. ملفات الارتباط والخدمات الخارجية">
                <p>
                    قد يستخدم النظام ملفات ارتباط أو وسائل تخزين ضرورية للجلسة والأمان وتسجيل الدخول. تخضع منصات
                    الطرف الثالث، ومنها Meta، لسياساتها وشروطها المستقلة عند انتقال المستخدم إليها لإتمام التفويض.
                </p>
            </Section>

            <Section title="11. English summary">
                <p>
                    {COMPANY_EN} operates Mezan OS for authorized commerce operations and advertising analytics.
                    We may process account identifiers, authorized store and advertising data, integration scopes,
                    reporting metrics, technical logs, and encrypted OAuth access tokens. We use the data to
                    authenticate users, operate requested integrations, provide analytics, secure the service,
                    provide support, and comply with applicable obligations.
                </p>
                <p>
                    We do not sell personal data. Data may be shared only with necessary service providers,
                    connected platforms at the user&apos;s direction, or competent authorities where legally required.
                    Some providers may process data outside Saudi Arabia subject to applicable safeguards.
                </p>
                <p>
                    Data subjects may request information, access, a readable copy, correction, deletion, or
                    withdrawal of consent where applicable by emailing {SUPPORT_EMAIL}. Identity verification may
                    be required. Requests are handled within 30 days, subject to permitted extensions and legal
                    retention obligations.
                </p>
            </Section>
        </LegalShell>
    );
}

function DataDeletion({ path }) {
    return (
        <LegalShell
            path={path}
            titleAr="تعليمات حذف بيانات المستخدم"
            titleEn="User Data Deletion Instructions"
            intro="يمكن للمستخدم طلب حذف بياناته المرتبطة بتسجيل الدخول أو تكامل Meta من نظام ميزان عبر الخطوات الموضحة أدناه."
        >
            <Section title="1. إرسال طلب الحذف">
                <p>أرسل بريدًا إلكترونيًا من البريد المرتبط بحسابك في ميزان إلى:</p>
                <a
                    className="inline-flex rounded-lg bg-emerald-950 px-4 py-2 font-mono font-bold text-white"
                    href={`mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent("طلب حذف بيانات Meta - Mezan OS")}`}
                >
                    {SUPPORT_EMAIL}
                </a>
                <p>اكتب في عنوان الرسالة: <strong>طلب حذف بيانات Meta - Mezan OS</strong>.</p>
                <p>ضمّن الاسم والبريد المرتبط بحسابك، ومعرّف حساب Meta أو الحساب الإعلاني إن كان متاحًا. لا ترسل كلمة المرور أو App Secret أو Access Token.</p>
            </Section>

            <Section title="2. إزالة التطبيق من Meta">
                <p>
                    يمكنك كذلك إزالة تطبيق Amasi Analytics من إعدادات التطبيقات والتكاملات في حساب Facebook/Meta.
                    إزالة التطبيق توقف وصوله المستقبلي، لكنها لا تغني عن إرسال طلب الحذف إذا رغبت في حذف البيانات
                    المخزنة مسبقًا داخل ميزان.
                </p>
            </Section>

            <Section title="3. التحقق من الهوية">
                <p>
                    لحماية الحسابات، قد نطلب التحقق من أن مقدم الطلب هو صاحب الحساب أو ممثله المخول. لن نطلب
                    كلمة مرور Meta أو رموز الوصول السرية.
                </p>
            </Section>

            <Section title="4. ما الذي نحذفه">
                <List>
                    <li>رمز وصول Meta المشفر والمخزن لحساب المستخدم.</li>
                    <li>الربط بين مستخدم ميزان وهوية Meta والأعمال والحسابات الإعلانية المصرح بها.</li>
                    <li>الأصول والبيانات الشخصية المشتقة من Meta التي لم يعد وجودها ضروريًا لتقديم الخدمة.</li>
                    <li>بيانات الجلسة أو الدعم المرتبطة بالطلب عندما لا يوجد مسوغ للاحتفاظ بها.</li>
                </List>
            </Section>

            <Section title="5. البيانات التي قد يلزم الاحتفاظ بها">
                <p>
                    قد نحتفظ بسجلات محدودة عندما يلزم ذلك للامتثال النظامي أو الأمن أو منع الاحتيال أو إثبات
                    المعاملات والقيود المحاسبية. تُعزل هذه السجلات عن الاستخدام التشغيلي ولا تُستخدم لإعادة تفعيل
                    تكامل Meta دون تفويض جديد.
                </p>
            </Section>

            <Section title="6. مدة التنفيذ والتأكيد">
                <p>
                    نراجع الطلب ونتخذ الإجراء خلال مدة لا تتجاوز 30 يومًا بعد التحقق من الهوية. إذا تطلب التنفيذ
                    جهدًا غير متناسب أو تعددت الطلبات، قد نمدد المدة وفق الأنظمة مع إشعارك بالأسباب. سنرسل تأكيدًا
                    إلى بريدك عند اكتمال الحذف أو توضيحًا لأي جزء يجب الاحتفاظ به نظامًا.
                </p>
            </Section>

            <Section title="English instructions">
                <p>
                    Email {SUPPORT_EMAIL} from the email address associated with your Mezan account. Use the subject
                    “Meta Data Deletion Request - Mezan OS” and include your name, account email, and Meta user or ad
                    account identifier if available. Never send passwords, App Secrets, or access tokens.
                </p>
                <p>
                    After identity verification, we will delete the encrypted Meta access token, account mappings,
                    and Meta-derived personal data that is no longer necessary. Limited records may be retained only
                    where required for legal, security, fraud-prevention, accounting, or audit purposes. We act on
                    verified requests within 30 days, subject to legally permitted extensions.
                </p>
            </Section>
        </LegalShell>
    );
}

function Terms({ path }) {
    return (
        <LegalShell
            path={path}
            titleAr="شروط الاستخدام"
            titleEn="Terms of Use"
            intro="تنظم هذه الشروط استخدام نظام ميزان والموقع والتكاملات المرتبطة به. باستخدام النظام، يقر المستخدم بأنه مخول من الجهة المالكة للحسابات والبيانات المتصلة."
        >
            <Section title="1. الخدمة والقبول">
                <p>
                    يوفر Mezan OS أدوات داخلية لإدارة العمليات والتحليلات والتكاملات. استخدام الموقع أو تسجيل
                    الدخول أو ربط منصة خارجية يعني الموافقة على هذه الشروط وسياسة الخصوصية.
                </p>
            </Section>

            <Section title="2. المستخدمون المخولون والحسابات">
                <List>
                    <li>يجب أن يكون المستخدم مخولًا من الجهة المالكة للمتجر أو الحساب الإعلاني.</li>
                    <li>يلتزم المستخدم بصحة بيانات الحساب وحماية وسائل تسجيل الدخول وعدم مشاركتها مع غير المصرح لهم.</li>
                    <li>يجب الإبلاغ فورًا عن أي استخدام غير مصرح به أو اشتباه بتسرب بيانات الدخول.</li>
                </List>
            </Section>

            <Section title="3. التكاملات الخارجية">
                <p>
                    يتم ربط Meta وSalla والمنصات الأخرى بناءً على تفويض صريح من المستخدم. يخضع استخدام كل منصة
                    كذلك لشروطها وسياساتها. قد تتغير واجهات المنصات أو صلاحياتها أو حدودها دون تحكم من ميزان.
                </p>
            </Section>

            <Section title="4. حدود التنفيذ والذكاء الاصطناعي">
                <p>
                    قد يقدم النظام تحليلات أو اقتراحات آلية. لا تُعد المخرجات ضمانًا للنتائج أو بديلًا عن التحقق
                    البشري أو المشورة المحاسبية أو القانونية أو الاستثمارية. أي تنفيذ حساس يجب أن يمر بالصلاحيات
                    والمراجعة والاعتماد المقررة داخل النظام.
                </p>
            </Section>

            <Section title="5. الاستخدامات المحظورة">
                <List>
                    <li>الوصول إلى حسابات أو بيانات دون تفويض أو تجاوز الأدوار والصلاحيات.</li>
                    <li>استخدام النظام للاحتيال أو التضليل أو انتهاك الأنظمة أو حقوق الآخرين.</li>
                    <li>محاولة استخراج الأسرار أو رموز الوصول أو تعطيل الخدمة أو اختراقها.</li>
                    <li>رفع برمجيات ضارة أو إجراء اختبارات هجومية دون موافقة كتابية.</li>
                    <li>إعادة بيع النظام أو بياناته أو إتاحته لطرف خارجي دون تصريح.</li>
                </List>
            </Section>

            <Section title="6. البيانات والملكية الفكرية">
                <p>
                    تظل بيانات المتجر والحسابات المتصلة مملوكة لأصحابها. يحتفظ مالك Mezan OS بحقوق البرمجيات
                    والتصميم والوثائق والعلامات، مع عدم اكتساب المستخدم أي حق يتجاوز الترخيص المحدود للاستخدام المصرح به.
                </p>
            </Section>

            <Section title="7. التوافر والتغييرات">
                <p>
                    نسعى إلى تشغيل الخدمة بصورة موثوقة، لكن قد تحدث صيانة أو أعطال أو تغييرات لدى مزودي المنصات.
                    يجوز تحديث الميزات أو إيقاف جزء منها لحماية الأمن أو الامتثال أو جودة البيانات.
                </p>
            </Section>

            <Section title="8. تعليق الوصول وإنهاؤه">
                <p>
                    يجوز تعليق أو إنهاء الوصول عند مخالفة الشروط، أو فقدان التفويض، أو وجود خطر أمني، أو بناءً على
                    طلب الجهة المالكة للحساب. لا يؤدي الإنهاء إلى إسقاط الالتزامات النظامية السابقة.
                </p>
            </Section>

            <Section title="9. إخلاء المسؤولية وحدودها">
                <p>
                    تُعرض البيانات والتحليلات بحسب المعلومات المتاحة من الأنظمة المرتبطة. لا نضمن خلو بيانات
                    المنصات الخارجية من التأخير أو النقص. إلى الحد الذي يسمح به النظام، لا تتحمل الجهة المشغلة
                    خسائر غير مباشرة ناتجة عن قرارات اتخذت دون تحقق مناسب من المصدر.
                </p>
            </Section>

            <Section title="10. النظام الواجب التطبيق">
                <p>
                    تخضع هذه الشروط لأنظمة المملكة العربية السعودية. تُحل النزاعات وديًا قدر الإمكان، وإلا فتُحال
                    إلى الجهة القضائية المختصة في المملكة.
                </p>
            </Section>

            <Section title="11. English summary">
                <p>
                    Mezan OS is an internal commerce operations and analytics service. Users must be authorized by
                    the owner of the connected store, business, or advertising accounts. Third-party integrations
                    remain subject to the third party&apos;s own terms and technical availability.
                </p>
                <p>
                    Automated analytics are informational and must be reviewed before sensitive action. Unauthorized
                    access, credential extraction, malicious activity, circumvention of permissions, and resale are
                    prohibited. These terms are governed by the laws of the Kingdom of Saudi Arabia.
                </p>
            </Section>
        </LegalShell>
    );
}

export default function PublicLegalApp({ path = globalThis?.location?.pathname || "/privacy-policy" }) {
    const normalizedPath = normalizeLegalPath(path);
    const pageTitle = normalizedPath === "/terms"
        ? "شروط الاستخدام | Mezan OS"
        : normalizedPath === "/data-deletion"
            ? "حذف بيانات المستخدم | Mezan OS"
            : "سياسة الخصوصية | Mezan OS";

    useEffect(() => {
        document.title = pageTitle;
        document.documentElement.lang = "ar";
        document.documentElement.dir = "rtl";
    }, [pageTitle]);

    if (normalizedPath === "/data-deletion") return <DataDeletion path={normalizedPath} />;
    if (normalizedPath === "/terms") return <Terms path={normalizedPath} />;
    return <PrivacyPolicy path="/privacy-policy" />;
}
