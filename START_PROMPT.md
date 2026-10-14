# Shared start prompt — NextGen AI Development Memory

استخدم هذا النص مع Claude Code أو Codex أو Antigravity عند بدء العمل على مشروع مجهز بالذاكرة:

> اقرأ `ai-memory/PROJECT.md` و`STATUS.md` و`DECISIONS.md` و`HANDOFF.md`. إذا كان MCP متصلاً استخدم `memory_overview` ثم `search_memory` لأي موضوع يتعلق بالمهمة. افحص Git والكود بنفسك قبل اعتماد أي تقرير قديم. حدد ما هو معتمد من المستخدم وما هو مجرد فكرة، واستمر من آخر خطوة مثبتة. عند نهاية المهمة سجّل ما تم والاختبارات الفعلية والمخاطر والخطوة التالية باستخدام `create_handoff`، وحدّث `STATUS.md` إذا تغيّرت حالة المشروع. لا تنشر ولا تعدل الإنتاج دون إذن.

When starting a new task: read the shared project memory, verify the worktree and tests, and only then continue. At the end: preserve user-approved decisions, ideas, tested results, risks and next actions in the handoff. Other agents cannot see your private transcript; record the crucial context explicitly.
