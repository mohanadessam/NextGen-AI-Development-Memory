# NextGen AI Development Memory + Smart Agent Router

**العربية أولاً · English below**

---

## 🇸🇦 نظرة عامة

أداة محلية تجعل **Claude Code** و**OpenAI Codex** و**Google Antigravity** يعملون كفريق واحد على نفس المشروع، بدون أي مفتاح API مدفوع (تستعمل اشتراكاتك المسجَّل دخولها في الأدوات).

تتكوّن من جزأين مستقلين:

| الجزء | الوظيفة |
|---|---|
| **الذاكرة المشتركة** (`ai-memory/` + `tools/ngmem.py`) | ملفات Markdown (PROJECT / STATUS / DECISIONS / IDEAS / HANDOFF / LOG) وخادم MCP صغير، حتى يكمل أي وكيل من حيث توقّف غيره، دون نسيان القرارات. |
| **الموجِّه الذكي** (`tools/ngrouter.py` وما حوله) | خادم MCP مستقل اسمه `nextgen-router`: يحلّل المهمة، يختار الوكيل الأنسب **ديناميكياً**، ينفّذ، يراجع باستقلالية، يختبر، ويسجّل كل شيء في الذاكرة. |

### المبدأ الأساسي: لا أدوار ثابتة
لا يوجد "Claude للبرمجة" ولا "Codex للاختبار". كل وكيل عامّ الغرض، والاختيار يُحسب لكل مهمة من:
الجاهزية الحالية، القدرات **المُتحقَّق منها فعلياً**، تعقيد المهمة ومخاطرها، الأداء السابق المثبت بأدلة موضوعية (نتائج اختبارات، اقتباسات مطابقة للملفات، عيوب مؤكدة)، التأخير، تاريخ الأعطال، والتكلفة. **تقييم الوكيل لنفسه لا يُحتسب أبداً.** وإذا تعطّل وكيل أو بلغ حدّه ينتقل العمل تلقائياً إلى غيره.

### ماذا يفعل الموجِّه
- **تنفيذ فردي أو متسلسل أو متوازٍ**، مع تقسيم تلقائي للمهام المستقلة.
- **مراجعة مستقلة بحسب الخطورة**: المراجع ليس المنفّذ وليس المنسّق.
- **كتابة معزولة**: كل تعديل يجري في `git worktree` مستقل بنطاق ملفات صريح، ولا يُدمج شيء تلقائياً — الناتج **مقترح دمج** (patch) تراجعه أنت.
- **حماية المسارات**: ملفات الموجِّه والذاكرة والخطافات وإعدادات الوكلاء وملفات الاختبار محمية من الوكلاء.
- **سجل أداء لكل مشروع** (`ai-memory/router/events.jsonl`) ومصفوفة قدرات حيّة تُعاد فحصها عند تغيّر إصدار الأداة أو بعد الأعطال.
- **استمرارية**: تقدّم المهمة محفوظ على القرص، ويمكن استئنافها (`resume`) إذا توقّف الخادم أثناء العمل.
- **اختبارات معزولة**: تُنفَّذ أوامر التحقق داخل صندوق رمل نظام التشغيل (Windows AppContainer + Job Object: بلا شبكة، بلا متغيرات بيئة المضيف، على نسخة مؤقتة من الشغل).
- **تسليم تلقائي** (`HANDOFF.md`) عبر خادم الذاكرة الموجود في المشروع، دون ترقية أي اقتراح إلى "قرار معتمد".

### المتطلبات
Python 3.9+، Git، وأدوات الوكلاء المراد استعمالها مسجَّلاً دخولها: `claude` و`codex` و`agy` (Antigravity CLI). صندوق الرمل يحتاج Windows (اختُبر على Windows 11 فقط)؛ بدونه تُسجَّل الاختبارات "لم تُنفَّذ" ولا تُشغَّل أي شيفرة وكيل على جهازك.

### التثبيت
```powershell
# مشروع جديد (الذاكرة فقط)
python install.py --project "C:\path\to\project"

# مشروع فيه ذاكرة موجودة (مثلاً v1.1 مع خطافات): أضف الموجِّه فقط — لا يمسّ ngmem.py ولا الخطافات ولا السجلات
python install.py --project "C:\path\to\project" --router-only --dry-run   # معاينة
python install.py --project "C:\path\to\project" --router-only
# تراجع كامل بإرجاع البايتات الأصلية
python install.py --project "C:\path\to\project" --rollback-router
```
بعد التثبيت أعد تشغيل Claude Code / Codex / Antigravity ليظهر خادم `nextgen-router`.

### الاستعمال
من داخل أي وكيل عبر أدوات MCP، أو من الطرفية:
```powershell
python tools/ngrouter.py --project . status
python tools/ngrouter.py --project . probe-caps claude          # فحص قدرات حيّ (مرة أولى لكل وكيل)
python tools/ngrouter.py --project . plan '{"task":"راجع inventory.py بحثاً عن أخطاء","files":["inventory.py"]}'
python tools/ngrouter.py --project . delegate '{"task":"...","mode":"write","scope":["src/"],"verify_cmd":"python-unittest","wait":true}'
python tools/ngrouter.py --project . cleanup <plan_id>          # إزالة الـ worktrees بعد مراجعة المقترح
```
أدوات MCP: `router_status` · `router_plan` · `router_delegate` · `router_result` · `router_resume` · `router_registry` · `router_capabilities` · `router_probe_capabilities` · `router_record_evidence`.

أمثلة لطلبات يومية للمنسّق:
1. «راجع وحدة المخزون وأصلح الأخطاء في `backend/` فقط، ثم اجعل وكيلاً آخر يراجع التعديل.»
2. «حلّل هذه النقاط الثلاث بالتوازي: 1. قفل البيانات 2. مسارات الخطأ 3. التسمية.»
3. «أكمل المهمة التي توقّفت عند انقطاع الخادم.» (`router_resume`)

### الأمان — ما الذي يُفرض وما الذي لا يُفرض
- كتابة **عالية الخطورة** (مسارات حساسة مثل `config/` و`database/` و`migrations/` و`.github/` و`.env`، أو كلمات مثل tenant/billing/production) **معطّلة تماماً**؛ لا توجد آلية موافقة، لأن الموافقة لا يمكن فرضها ضد وكلاء يعملون بنفس حساب Windows.
- لا يُنفَّذ `git commit` ولا `push` ولا نشر من الوكلاء؛ المقترح يبقى patch.
- أوامر التحقق **أسماء مُعرَّفة في الإعداد** وليست أوامر حرة.
- حدود معروفة: لا صندوق رمل لعمليات الوكلاء أنفسهم (فقط للمُحقِّق)؛ MariaDB عبر TCP غير ممكن داخل الصندوق؛ Antigravity يعمل عبر ملف تعريف معزول لأن إضافة تليمتري في الـ IDE تعطّل أدواته في الوضع الصامت.

### حالة المشروع
| الإصدار | الحالة |
|---|---|
| **v1.2.2** | نسخة مُجازة بعد مراجعتين مستقلتين وبوابة إصدار (`FINAL_RELEASE_GATE.md`) — الحكم: **CONDITIONAL GO** |
| **v1.3** | **CONDITIONAL GO** — اكتمل: استرجاع Antigravity، سجل القدرات الحيّ، التوجيه الديناميكي، الكتابة المعزولة، المُحقِّق المعزول، جولات التصحيح والاستئناف، تمرين حيّ بـ11 سيناريو، مراجعة Codex مستقلة، تثبيت للقراءة فقط في Nextory. غير مفعّل عمداً: الكتابة عالية الخطورة، الكتابة في Nextory، التحقق عبر MariaDB. التقرير: `NEXTGEN_AI_TEAM_V1_3_FINAL_REPORT.md` |

### الاختبارات
```powershell
python tests/test_memory.py           # قالب الذاكرة v1.0
python tests/test_router.py           # الموجِّه (وكلاء محاكاة)
python tests/test_v13.py  tests/test_v13_collab.py  tests/test_v13_verifier.py
python tests/test_sandbox.py          # صندوق الرمل (Windows)
python tests/test_install_upgrade.py  # التثبيت/الترقية/التراجع على مشروع v1.1
```
والسكربتات `tests/e2e_*.py` و`tests/verify_*.py` تستعمل الوكلاء الحقيقيين (تستهلك اشتراكاتك).

---

## 🇬🇧 Overview

A local toolkit that lets **Claude Code**, **OpenAI Codex** and **Google Antigravity** work as one team on the same project — no paid API keys (it reuses the subscription logins of the installed CLIs).

Two independent parts:

| Part | What it does |
|---|---|
| **Shared memory** (`ai-memory/` + `tools/ngmem.py`) | Markdown records (PROJECT / STATUS / DECISIONS / IDEAS / HANDOFF / LOG) plus a small MCP server, so any agent can continue where another stopped. |
| **Smart Agent Router** (`tools/ngrouter.py` and friends) | A standalone MCP server `nextgen-router`: analyses a task, picks the best agent **dynamically**, executes, reviews independently, verifies, and records everything in memory. |

**No fixed roles.** Selection is recomputed per task from live availability, *verified* capabilities, task complexity/risk, objective performance evidence (tests, grounded citations, validated defects), latency, failure history and cost. An agent's self-assessment is never scored. Failures trigger automatic failover and circuit breakers.

**Highlights:** single / sequential / parallel execution · independent review proportional to risk (reviewer ≠ executor ≠ coordinator) · writes only in isolated `git worktree`s with an explicit literal scope · results delivered as an **integration proposal (patch)**, never auto-merged · protected paths and test-harness protection · live capability registry · durable progress with `resume` · verifier commands run inside an OS sandbox (Windows AppContainer + Job Object: no network, no host env, throwaway copy) · automatic handoff through the project's own memory server.

**Requirements:** Python 3.9+, Git, and the agent CLIs you want to use, logged in (`claude`, `codex`, `agy`). The sandbox needs Windows (tested on Windows 11 only); without it verification is reported as *not run* and agent-written code is never executed on the host.

**Install**
```powershell
python install.py --project "C:\path\to\project"                       # new project (memory kit)
python install.py --project "C:\path\to\project" --router-only --dry-run   # existing project: preview
python install.py --project "C:\path\to\project" --router-only             # add only the router (never replaces ngmem.py/hooks/records)
python install.py --project "C:\path\to\project" --rollback-router         # exact rollback
```

**Layout**
```
install.py                 installer (idempotent, backs up, manifest-based rollback)
template/ai-memory/        memory scaffold          template/tools/ngmem.py   memory MCP server (v1.0)
template/tools/ngrouter.py router + MCP server      template/tools/ngcaps.py  capability registry / probes
template/tools/ngsandbox.py AppContainer sandbox    template/AGENTS*.md       shared agent instructions
tests/                     unit, integration and live (authenticated) suites
FINAL_RELEASE_GATE.md      v1.2.2 security review and verdict
NEXTGEN_AI_TEAM_V1_3_FINAL_REPORT.md  v1.3 final report and per-capability verdicts
```

**Security model in one paragraph.** High-risk writes are disabled outright (an approval cannot be enforced against agents that share the same OS account). Agents never commit, push or deploy. Verifiers are named commands from config, not free-form. Known limits: agent processes themselves are not OS-sandboxed (only verifiers are); MariaDB over TCP cannot run in the sandbox; Antigravity runs through an isolated profile because an IDE telemetry plugin breaks its headless tools.

**Status:** v1.3 — **CONDITIONAL GO** (11-scenario live exercise, independent Codex review, router-only read-only install in Nextory). High-risk writes, writes in Nextory and MariaDB-backed verification remain disabled/blocked by design. See `NEXTGEN_AI_TEAM_V1_3_FINAL_REPORT.md`.

Private repository, managed by NextGen Git AutoSync.
