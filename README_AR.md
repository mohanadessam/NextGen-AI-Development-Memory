# NextGen AI Development Memory — الإصدار 1.0

ذاكرة مشاريع مشتركة بين **Claude Code + OpenAI Codex + Google Antigravity** على Windows وmacOS وLinux. بدون API مدفوع وبدون خدمة سحابية جديدة. مبنية على ملفات Markdown قابلة للمزامنة مع Git، بالإضافة إلى خادم **MCP stdio محلي** بلغة Python من المكتبة القياسية فقط.

> **مهم:** هذه ليست محادثة واحدة حرفياً بين الأدوات. إنها مصدر موحد لملخص المشروع، القرارات، الأفكار، حالة التنفيذ، والسجل المختصر للتسليمات. لا تستورد تاريخ المحادثات السابق تلقائياً؛ ولا تضمن كتابة كل شيء دون تدخل أو تهيئة. الوكيل يقرأ التعليمات ويستخدم أدوات الذاكرة؛ تحقق منه عند التسليم.

## المتطلبات

- Python **3.9 أو أحدث** على الجهاز المستهدف (`py -3 --version` في Windows؛ `python3 --version` في Mac).
- مشروع موجود على القرص (لا تعدّل الحزمة المشروع مباشرة قبل تشغيل المثبّت).
- إضافات/برامج Claude Code وCodex وAntigravity منصّبة ومسجل الدخول فيها باشتراكاتك الاعتيادية، لتفعيل استخدام أدوات MCP من داخلها.

## التثبيت التفاعلي الأسهل

- **Windows:** انقر بزر الماوس الأيمن على `INSTALL_WINDOWS.ps1` وشغله من PowerShell الموثوق (يمكن أيضاً من PowerShell: `./INSTALL_WINDOWS.ps1`). سيسألك عن مسار مشروعك وهل هو Nextory. قد تتطلب سياسة تشغيل PowerShell السماح لهذا الملف الموثوق؛ لا توقف سياسات الأمان العامة لمجرد تشغيله.
- **Mac:** افتح `INSTALL_MAC.command` من Terminal أو شغّل `bash INSTALL_MAC.command`، وأدخل المسار عندما يسألك.

إذا تفضّل أمراً واحداً من Terminal استخدم التعليمات التالية.

## التثبيت السريع — Windows (PowerShell)

فك ضغط الملف ثم افتح PowerShell داخل مجلد `NextGen-AI-Development-Memory`:

```powershell
py -3 install.py --project "C:\Users\mohan\Projects\NextGen-Factory_erp" --preset nextory
```

**المسار مثال فقط**؛ غيّره إلى مسار Nextory الحقيقي. لمنصّة ثانية (بدون معلومات Nextory الخاصة):

```powershell
py -3 install.py --project "C:\path\to\OtherProject"
```

## التثبيت السريع — macOS / Linux (Terminal)

```bash
python3 install.py --project "/Users/yourname/Projects/NextGen-Factory_erp" --preset nextory
```

تغيير المسار مطلوب حسب مكان مشروعك.

## عدة مشاريع بأمر واحد

```powershell
py -3 install.py --project "C:\Work\ProjectA" --project "C:\Work\ProjectB"
```

لا تستخدم `--preset nextory` إلا عند تثبيت مشروع Nextory وحده؛ معلوماته ليست مناسبة لجميع المشاريع.

## ماذا يصنع داخل كل مشروع؟

```text
project/
├── AGENTS.md                  # التعليمات المشتركة: Codex + Antigravity + Claude
├── CLAUDE.md                  # يستورد AGENTS.md، ويبقى متوافقاً مع Claude
├── ai-memory/
│   ├── PROJECT.md             # وصف النظام، المعمارية، قواعد العمل
│   ├── STATUS.md              # آخر حالة تحقق حقيقية
│   ├── DECISIONS.md           # قرارات معتمدة/مقترحة مع الأسباب
│   ├── IDEAS.md               # أفكار قيد النقاش، ليست أوامر
│   ├── HANDOFF.md             # آخر تسليم بين الأدوات
│   ├── LOG.md                 # سجل النشاط والتسليمات السابقة
│   └── README.md
├── tools/ngmem.py             # CLI + MCP، مكتبة Python فقط
├── .mcp.json                  # إعداد Claude المحلي
├── .agents/mcp_config.json   # إعداد Antigravity المحلي
├── .codex/config.toml        # إعداد Codex المحلي
└── .gitignore                # استثناء مسارات الإعداد الشخصية والنسخ الاحتياطية
```

**لا يحذف أو يستبدل الملفات الموجودة.** إذا كانت `AGENTS.md` أو `CLAUDE.md` موجودة، يضيف قسم الربط فقط ويحفظ نسخة احتياطية قبل التعديل. إذا كانت ملفات الذاكرة موجودة يحتفظ بها. إعدادات MCP الموجودة تُدمَج دون إزالة السيرفرات الأخرى؛ وإذا كان هناك خادم باسم `nextgen-memory` مسبقاً، يترك إعداده لتراجعه. لا يمس ملفات كود التطبيق، ولا يعمل Commit أو Push أو Deployment.

## خطوات التفعيل والتحقق

1. افتح المشروع في VS Code/Antigravity وابدأ جلسة **جديدة** مع كل Agent كي تقرأ التعليمات.
2. **Claude Code:** افتح `/mcp` للتحقق من `nextgen-memory`، ووافق على خادم المشروع عند ظهور طلب الثقة. يمكن فحص التعليمات عبر `/context`.
3. **Codex:** افتح `/mcp` أو `codex mcp list`؛ **قد يتطلب تثبيت MCP في المشروع اعتباره Trusted Project**.
4. **Antigravity:** Agent panel → `…` → MCP Servers → Manage MCP Servers. تحقق من `nextgen-memory` في ملف إعداد المشروع. كما يقرأ `AGENTS.md` في جذر المشروع.
5. اختبر الذاكرة يدوياً:

```bash
python tools/ngmem.py overview
python tools/ngmem.py search "tenant"
python tools/ngmem.py idea "تقارير مبكرة" "نناقش إضافة تقرير ملخص للمالك" --agent human
python tools/ngmem.py decision "حدود المستخدمين" "اقتراح تحديد سقف مستخدمين حسب الباقة" --state proposed --agent human
```

على Windows استخدم `py -3` بدل `python` إن لم يكن مثبتاً باسم `python`.

6. من كل وكيل أرسل الرسالة التالية مرة واحدة في جلسة جديدة، حتى تتأكد من قراءته للذاكرة (أيضاً محفوظة في `START_PROMPT.md`):

> اقرأ `ai-memory/PROJECT.md` و`STATUS.md` و`DECISIONS.md` و`HANDOFF.md` ثم افحص Git. إذا توفر MCP، استخدم `memory_overview`. أخبرني بآخر حالة مثبتة والمهمة التالية. عند نهاية العمل احفظ النتائج والاختبارات والخطوة المقبلة عبر `create_handoff`. لا تعتبر المقترحات قرارات معتمدة.

## استخدام يومي

- **Claude → Codex:** Claude ينفذ، ثم يحفظ `create_handoff` بنتيجة الاختبارات والتغييرات. Codex يبدأ بـ`memory_overview`، يتحقق من Git ويفحص أو يكمل.
- **Codex → Antigravity:** Codex يسجل الملاحظات والتسليم. Antigravity يفحصها ثم يتولى الواجهة أو اختبار المتصفح.
- **أفكارك في المحادثة:** اطلب من الوكيل `record_idea` مباشرة حتى لا تضيع. لا يوجد نقل تلقائي مضمون للرسائل التي لم يتم تسجيلها.
- **القرارات:** `record_decision` بحالة `user-approved` فقط عندما تعطي موافقة واضحة، وإلا `proposed`.

## مزامنة Windows ↔ Mac

ارفع إلى **مستودع Git خاص** الملفات `AGENTS.md` و`CLAUDE.md` و`ai-memory/` و`tools/ngmem.py` و`.gitignore` بعد مراجعة خلوها من الأسرار. على Mac نفّذ المثبّت من الحزمة نفسها بعد `git clone` ليتولد ربط MCP المحلي بمسار Mac الصحيح. الإعدادات المحلية الثلاثة مستبعدة من Git لأنها تحتوي مسار Python ومسار المشروع الخاص بكل جهاز. لا ترفع محتويات خاصة بالعملاء أو أسراراً.

## حدود مهمة

- لا توجد ذاكرة للمحادثات الخاصة لدى Claude أو Codex أو Antigravity إلا ما تختار تسجيله.
- تسجيل القرارات والتسليم **تلقائي بحسب التزام Agent بالتعليمات واستخدامه الأدوات**، وليس ضماناً تقنياً شاملاً. اطلب منه التأكيد عند نهاية كل جلسة.
- MCP stdio يعمل محلياً عند فتح كل تطبيق؛ لا يحتاج سيرفر إنترنت ولا يعمل كخدمة دائمة.
- حتى مع وجود تعليمات الأمان، طبّق موافقات تشغيل الأوامر وسياسات Git/CI والصلاحيات الفعلية. القواعد النصية لا تمنع أوامر خطيرة وحدها.
- إذا حصل تعارض Git في Markdown، حله يدوياً؛ لا تجعل وكلاء متعددين يعدلون ملفات كود نفسها في وقت واحد دون فروع منفصلة.
- `--preset nextory` يحتوي ملخصاً تاريخياً أُبلغ عنه، **وليس تدقيقاً على مستودعك الحالي**.

## مصادر التوافق الرسمية

- Claude Code memory: https://code.claude.com/docs/en/memory
- Claude Code MCP: https://code.claude.com/docs/en/mcp
- Codex AGENTS.md: https://developers.openai.com/codex/guides/agents-md
- Codex MCP: https://developers.openai.com/codex/mcp
- Antigravity rules: https://www.antigravity.google/docs/rules/
- Antigravity MCP: https://antigravity.google/docs/mcp?tab=ide
- MCP stdio specification: https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
