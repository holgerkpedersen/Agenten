"""Auto-research engine for Agenten.

Design:
  - trigger_if_needed() is called from _finalize_task_stream when a phase fails.
  - It classifies the failure, deduplicates, rate-limits, and creates a CORE-issue.
  - The caller (agent_tasks._finalize_task_stream) then executes the CORE-issue
    inline via _execute_autoresearch_issue() as a selvforbedring sub-session.
  - The sub-session runs in the same SSE stream — user sees Analyser→Diagnosticér
    →Ret→Verificér→Commit live in the UI.
  - Depth-limited (max 2 nested sessions) to prevent infinite recursion.
"""

import json
import os
import re
import time
import uuid
from typing import Any

from i18n import K
from lang import t

# ── Issue text translations (titles & impacts) ──────────────────
_ISSUE_TEXTS: dict[str, dict[str, str]] = {
    "title_missing_tool": {
        "da": "Manglende {uncalled} i {template}/{phase} — LLM kaldte ikke påkrævet værktøj",
        "en": "Missing {uncalled} in {template}/{phase} — LLM did not call required tool",
        "es": "Falta {uncalled} en {template}/{phase} — el LLM no llamó a la herramienta requerida",
        "zh": "在 {template}/{phase} 中缺失 {uncalled} — LLM 未调用必需工具",
    },
    "title_tool_failed": {
        "da": "Værktøj {tool} fejlede i {template}/{phase} — alle {attempts} forsøg slog fejl",
        "en": "Tool {tool} failed in {template}/{phase} — all {attempts} attempts failed",
        "es": "Herramienta {tool} falló en {template}/{phase} — todos los {attempts} intentos fallaron",
        "zh": "工具 {tool} 在 {template}/{phase} 中失败 — 所有 {attempts} 次尝试均失败",
    },
    "title_read_loop": {
        "da": "Læse-loop i {template}/{phase} — {reads} reads uden write",
        "en": "Read loop in {template}/{phase} — {reads} reads without write",
        "es": "Bucle de lectura en {template}/{phase} — {reads} lecturas sin escritura",
        "zh": "在 {template}/{phase} 中循环读取 — {reads} 次读取无写入",
    },
    "title_short_output": {
        "da": "Kort output i {template}/{phase} — {length} tegn, ingen tools",
        "en": "Short output in {template}/{phase} — {length} chars, no tools",
        "es": "Salida corta en {template}/{phase} — {length} caracteres, sin herramientas",
        "zh": "在 {template}/{phase} 中输出过短 — {length} 个字符，无工具调用",
    },
    "title_incomplete": {
        "da": "Ufuldstændig ekstrahering i {template}/{phase} — {c}/{p} moduler oprettet",
        "en": "Incomplete extraction in {template}/{phase} — {c}/{p} modules created",
        "es": "Extracción incompleta en {template}/{phase} — {c}/{p} módulos creados",
        "zh": "在 {template}/{phase} 中提取不完整 — 已创建 {c}/{p} 个模块",
    },
    "title_unknown": {
        "da": "Uforklaret fejl i {template}/{phase}",
        "en": "Unexplained failure in {template}/{phase}",
        "es": "Fallo inexplicado en {template}/{phase}",
        "zh": "{template}/{phase} 中发生未知错误",
    },
    "impact_missing_tool": {
        "da": "Fasen {phase} i {template} kan ikke gennemføres fordi LLM'en ikke kalder {uncalled}. Dette blokerer hele selvforbedrings-cyklussen.",
        "en": "Phase {phase} in {template} cannot complete because the LLM doesn't call {uncalled}. This blocks the entire self-improvement cycle.",
        "es": "La fase {phase} en {template} no puede completarse porque el LLM no llama a {uncalled}. Esto bloquea todo el ciclo de auto-mejora.",
        "zh": "由于 LLM 未调用 {uncalled}，{template} 中的 {phase} 阶段无法完成。这阻碍了整个自我改进循环。",
    },
    "impact_tool_failed": {
        "da": "Værktøjet {tool} fejler i {template}/{phase}. Alle forsøg på at bruge det slog fejl.",
        "en": "Tool {tool} fails in {template}/{phase}. All attempts to use it failed.",
        "es": "La herramienta {tool} falla en {template}/{phase}. Todos los intentos de usarla fallaron.",
        "zh": "工具 {tool} 在 {template}/{phase} 中失败。所有使用尝试均失败。",
    },
    "impact_read_loop": {
        "da": "LLM'en læser uden at skrive i {template}/{phase}, hvilket spilder iterationer og fører til timeout.",
        "en": "LLM reads without writing in {template}/{phase}, wasting iterations and causing timeout.",
        "es": "El LLM lee sin escribir en {template}/{phase}, desperdiciando iteraciones y causando tiempo de espera.",
        "zh": "LLM 在 {template}/{phase} 中只读不写，浪费迭代并导致超时。",
    },
    "impact_incomplete": {
        "da": "Kun {c}/{p} moduler blev oprettet i {template}/{phase}. Fasen kan ikke fuldføres før ALLE planlagte moduler findes.",
        "en": "Only {c}/{p} modules were created in {template}/{phase}. The phase cannot complete until ALL planned modules exist.",
        "es": "Solo se crearon {c}/{p} módulos en {template}/{phase}. La fase no puede completarse hasta que EXISTAN TODOS los módulos planificados.",
        "zh": "在 {template}/{phase} 中仅创建了 {c}/{p} 个模块。在所有计划模块都存在之前，阶段无法完成。",
    },
    "impact_unknown": {
        "da": "Fasen {phase} i {template} fejler af uforklarede årsager.",
        "en": "Phase {phase} in {template} fails for unexplained reasons.",
        "es": "La fase {phase} en {template} falla por razones inexplicadas.",
        "zh": "{template} 中的 {phase} 阶段因未知原因失败。",
    },
}

# ── Description texts for _build_issue_description ────────────
_DESC_TEXTS: dict[str, dict[str, str]] = {
    "header": {
        "da": "## Auto-research analyse: {failure_type}",
        "en": "## Auto-research analysis: {failure_type}",
        "es": "## Análisis de auto-investigación: {failure_type}",
        "zh": "## 自动研究分析：{failure_type}",
    },
    "phase_line": {
        "da": "**Skabelon:** {template}  |  **Fase:** {phase}",
        "en": "**Template:** {template}  |  **Phase:** {phase}",
        "es": "**Plantilla:** {template}  |  **Fase:** {phase}",
        "zh": "**模板：** {template}  |  **阶段：** {phase}",
    },
    "what_happened": {
        "da": "### Hvad skete der?",
        "en": "### What happened?",
        "es": "### ¿Qué sucedió?",
        "zh": "### 发生了什么？",
    },
    "why_problem": {
        "da": "### Hvorfor er dette et problem?",
        "en": "### Why is this a problem?",
        "es": "### ¿Por qué es esto un problema?",
        "zh": "### 为什么这是个问题？",
    },
    "possible_causes": {
        "da": "### Mulige årsager",
        "en": "### Possible causes",
        "es": "### Causas posibles",
        "zh": "### 可能的原因",
    },
    "analysis": {
        "da": "### Analyse",
        "en": "### Analysis",
        "es": "### Análisis",
        "zh": "### 分析",
    },
    "expected_next": {
        "da": "### Forventet næste skridt",
        "en": "### Expected next steps",
        "es": "### Próximos pasos esperados",
        "zh": "### 预期的后续步骤",
    },
    "missing_tool_what": {
        "da": "LLM'en kaldte IKKE de påkrævede værktøjer: {uncalled}.",
        "en": "The LLM did NOT call the required tools: {uncalled}.",
        "es": "El LLM NO llamó a las herramientas requeridas: {uncalled}.",
        "zh": "LLM 未调用必需的工具：{uncalled}。",
    },
    "called_tools": {
        "da": "Kaldte værktøjer: {called}.",
        "en": "Called tools: {called}.",
        "es": "Herramientas llamadas: {called}.",
        "zh": "已调用的工具：{called}。",
    },
    "active_tools": {
        "da": "Aktive værktøjer i fasen: {required}.",
        "en": "Active tools in phase: {required}.",
        "es": "Herramientas activas en la fase: {required}.",
        "zh": "阶段中的活动工具：{required}。",
    },
    "missing_tool_why": {
        "da": "Fasen kan ikke fuldføres uden at det påkrævede værktøj kaldes. Systemet afviser <<<DONE>>> når _check_required_tools fejler.",
        "en": "The phase cannot complete without the required tool being called. The system rejects <<<DONE>>> when _check_required_tools fails.",
        "es": "La fase no puede completarse sin llamar a la herramienta requerida. El sistema rechaza <<<DONE>>> cuando _check_required_tools falla.",
        "zh": "如果不调用必需的工具，阶段就无法完成。当 _check_required_tools 失败时，系统会拒绝 <<<DONE>>>。",
    },
    "missing_tool_reasons": {
        "da": "- LLM'en forstår ikke instruktionen (sektionsinstruktionen er uklar)\n- Modellen nægter at kalde skriveværktøjer (kendt begrænsning)\n- Fasen mangler write-tools i TEMPLATE_TASK_TOOLS\n- Der mangler en alternativ sti (create_issue i stedet for edit_file)",
        "en": "- LLM doesn't understand the instruction (section instruction is unclear)\n- Model refuses to call write tools (known limitation)\n- Phase lacks write tools in TEMPLATE_TASK_TOOLS\n- Missing an alternative path (create_issue instead of edit_file)",
        "es": "- El LLM no entiende la instrucción (la instrucción de sección no es clara)\n- El modelo se niega a llamar herramientas de escritura (limitación conocida)\n- La fase carece de herramientas de escritura en TEMPLATE_TASK_TOOLS\n- Falta una ruta alternativa (create_issue en lugar de edit_file)",
        "zh": "- LLM 不理解指令（部分指令不清晰）\n- 模型拒绝调用写入工具（已知限制）\n- 阶段在 TEMPLATE_TASK_TOOLS 中缺少写入工具\n- 缺少替代路径（使用 create_issue 代替 edit_file）",
    },
    "tool_failed_what": {
        "da": "Værktøjet {tool} blev kaldt {attempts} gange men fejlede hver gang.",
        "en": "Tool {tool} was called {attempts} times but failed each time.",
        "es": "La herramienta {tool} fue llamada {attempts} veces pero falló en cada intento.",
        "zh": "工具 {tool} 被调用了 {attempts} 次，但每次都失败了。",
    },
    "last_error": {
        "da": "Sidste fejl: {error}",
        "en": "Last error: {error}",
        "es": "Último error: {error}",
        "zh": "最后错误：{error}",
    },
    "last_args": {
        "da": "Sidste args: {args}",
        "en": "Last args: {args}",
        "es": "Últimos args: {args}",
        "zh": "最后参数：{args}",
    },
    "tool_failed_analysis": {
        "da": "Tool-fejl kan skyldes ugyldige argumenter, manglende rettigheder, eller en bug i værktøjets implementering.",
        "en": "Tool failures can be caused by invalid arguments, missing permissions, or a bug in the tool's implementation.",
        "es": "Las fallas de herramientas pueden ser causadas por argumentos inválidos, permisos faltantes, o un error en la implementación de la herramienta.",
        "zh": "工具失败可能由无效参数、缺少权限或工具实现中的错误引起。",
    },
    "read_loop_what": {
        "da": "LLM'en lavede {reads} læsekald i træk uden at skrive noget.",
        "en": "The LLM made {reads} consecutive read calls without writing anything.",
        "es": "El LLM realizó {reads} llamadas de lectura consecutivas sin escribir nada.",
        "zh": "LLM 连续进行了 {reads} 次读取调用，没有写入任何内容。",
    },
    "read_loop_analysis": {
        "da": "LLM'en mangler kontekst til at skrive. Overvej at øge iteration budget eller give en tom skabelon.",
        "en": "The LLM lacks context to write. Consider increasing iteration budget or providing an empty template.",
        "es": "Al LLM le falta contexto para escribir. Considera aumentar el presupuesto de iteración o proporcionar una plantilla vacía.",
        "zh": "LLM 缺乏写入的上下文。考虑增加迭代预算或提供空模板。",
    },
    "incomplete_what": {
        "da": "Fasen løb tør for iterationer før alt planlagt arbejde var færdigt ({c}/{p} moduler oprettet).",
        "en": "The phase ran out of iterations before all planned work was completed ({c}/{p} modules created).",
        "es": "La fase se quedó sin iteraciones antes de completar todo el trabajo planificado ({c}/{p} módulos creados).",
        "zh": "阶段在完成所有计划工作之前耗尽了迭代次数（已创建 {c}/{p} 个模块）。",
    },
    "missing_modules": {
        "da": "Manglende moduler: {missing}",
        "en": "Missing modules: {missing}",
        "es": "Módulos faltantes: {missing}",
        "zh": "缺少的模块：{missing}",
    },
    "incomplete_analysis": {
        "da": "LLM'en kaldte værktøjer korrekt, men iteration budgettet var for lavt til at fuldføre alle moduler. Budgettet skal beregnes dynamisk baseret på antal moduler i refactor_plan.md i stedet for at være fast.",
        "en": "The LLM called tools correctly, but the iteration budget was too low to complete all modules. The budget should be calculated dynamically based on the number of modules in refactor_plan.md instead of being fixed.",
        "es": "El LLM llamó herramientas correctamente, pero el presupuesto de iteración era demasiado bajo para completar todos los módulos. El presupuesto debe calcularse dinámicamente según el número de módulos en refactor_plan.md en lugar de ser fijo.",
        "zh": "LLM 正确调用了工具，但迭代预算太低，无法完成所有模块。预算应根据 refactor_plan.md 中的模块数量动态计算，而不是固定的。",
    },
    "incomplete_next_steps": {
        "da": "1. Læs _get_max_iterations() i agent_tasks.py\n2. Beregn dynamisk budget: max(20, 2 + antal_moduler * 2 + 5)\n3. Tilføj system-besked når todos auto-opdateres\n4. Opdater instructions/refactor.json — fjern 'Brug update_todo'",
        "en": "1. Read _get_max_iterations() in agent_tasks.py\n2. Calculate dynamic budget: max(20, 2 + module_count * 2 + 5)\n3. Add system message when todos auto-update\n4. Update instructions/refactor.json — remove 'Brug update_todo'",
        "es": "1. Lee _get_max_iterations() en agent_tasks.py\n2. Calcula presupuesto dinámico: max(20, 2 + count_modulos * 2 + 5)\n3. Añade mensaje del sistema cuando los todos se auto-actualicen\n4. Actualiza instructions/refactor.json — elimina 'Brug update_todo'",
        "zh": "1. 阅读 agent_tasks.py 中的 _get_max_iterations()\n2. 计算动态预算：max(20, 2 + 模块数 * 2 + 5)\n3. 当待办事项自动更新时添加系统消息\n4. 更新 instructions/refactor.json — 删除 'Brug update_todo'",
    },
    "unknown_what": {
        "da": "Kaldte værktøjer: {tools}",
        "en": "Called tools: {tools}",
        "es": "Herramientas llamadas: {tools}",
        "zh": "已调用的工具：{tools}",
    },
    "unknown_length": {
        "da": "Output længde: {length}",
        "en": "Output length: {length}",
        "es": "Longitud de salida: {length}",
        "zh": "输出长度：{length}",
    },
}

# ── Fix texts for _build_issue_fix ────────────────────────────
_FIX_TEXTS: dict[str, dict[str, str]] = {
    "missing_tool_intro": {
        "da": "Fasen \"{phase}\" i \"{template}\" kræver at LLM'en kalder {uncalled}.",
        "en": "Phase \"{phase}\" in \"{template}\" requires the LLM to call {uncalled}.",
        "es": "La fase \"{phase}\" en \"{template}\" requiere que el LLM llame a {uncalled}.",
        "zh": "阶段 \"{phase}\" 在 \"{template}\" 中要求 LLM 调用 {uncalled}。",
    },
    "context_label": {
        "da": "Kontekst",
        "en": "Context",
        "es": "Contexto",
        "zh": "上下文",
    },
    "edit_file_problem": {
        "da": "Problem: {template}/{phase} har edit_file i active_tools, men LLM'en kaldte det ikke.",
        "en": "Problem: {template}/{phase} has edit_file in active_tools, but the LLM did not call it.",
        "es": "Problema: {template}/{phase} tiene edit_file en active_tools, pero el LLM no lo llamó.",
        "zh": "问题：{template}/{phase} 在 active_tools 中有 edit_file，但 LLM 未调用它。",
    },
    "solution_choose": {
        "da": "Løsning (vælg én):",
        "en": "Solution (choose one):",
        "es": "Solución (elige una):",
        "zh": "解决方案（选择一项）：",
    },
    "solution_edit_file_1": {
        "da": "Hvis edit_file skal være påkrævet: Opdater sektionsinstruktionen i instructions/{template}.json så \"{phase}\" starter med \"DIN FØRSTE handling SKAL være edit_file\".",
        "en": "If edit_file should be required: Update the section instruction in instructions/{template}.json so \"{phase}\" starts with \"YOUR FIRST action MUST be edit_file\".",
        "es": "Si edit_file debe ser requerido: Actualiza la instrucción de sección en instructions/{template}.json para que \"{phase}\" comience con \"TU PRIMERA acción DEBE ser edit_file\".",
        "zh": "如果 edit_file 应为必需：更新 instructions/{template}.json 中的部分指令，使 \"{phase}\" 以 \"你的第一个操作必须是 edit_file\" 开头。",
    },
    "solution_edit_file_2": {
        "da": "Hvis create_issue er et acceptabelt alternativ: Tilføj create_issue til TEMPLATE_TASK_TOOLS for {template}/{phase} i agent_skills.py.",
        "en": "If create_issue is an acceptable alternative: Add create_issue to TEMPLATE_TASK_TOOLS for {template}/{phase} in agent_skills.py.",
        "es": "Si create_issue es una alternativa aceptable: Añade create_issue a TEMPLATE_TASK_TOOLS para {template}/{phase} en agent_skills.py.",
        "zh": "如果 create_issue 是可接受的替代方案：在 agent_skills.py 中为 {template}/{phase} 将 create_issue 添加到 TEMPLATE_TASK_TOOLS。",
    },
    "solution_edit_file_3": {
        "da": "Hvis fasen er read-only: Fjern edit_file fra TEMPLATE_TASK_TOOLS for {template}/{phase}.",
        "en": "If the phase is read-only: Remove edit_file from TEMPLATE_TASK_TOOLS for {template}/{phase}.",
        "es": "Si la fase es de solo lectura: Elimina edit_file de TEMPLATE_TASK_TOOLS para {template}/{phase}.",
        "zh": "如果阶段是只读的：从 {template}/{phase} 的 TEMPLATE_TASK_TOOLS 中移除 edit_file。",
    },
    "solution_write_file": {
        "da": "Tilføj write_file til TEMPLATE_TASK_TOOLS for {template}/{phase} i agent_skills.py.",
        "en": "Add write_file to TEMPLATE_TASK_TOOLS for {template}/{phase} in agent_skills.py.",
        "es": "Añade write_file a TEMPLATE_TASK_TOOLS para {template}/{phase} en agent_skills.py.",
        "zh": "在 agent_skills.py 中为 {template}/{phase} 将 write_file 添加到 TEMPLATE_TASK_TOOLS。",
    },
    "solution_update_issue_status": {
        "da": "Tjek at {template}/{phase} er i CLOSE_PHASE_ALIASES i agent_tasks.py:993.",
        "en": "Check that {template}/{phase} is in CLOSE_PHASE_ALIASES in agent_tasks.py:993.",
        "es": "Verifica que {template}/{phase} esté en CLOSE_PHASE_ALIASES en agent_tasks.py:993.",
        "zh": "检查 {template}/{phase} 是否在 agent_tasks.py:993 的 CLOSE_PHASE_ALIASES 中。",
    },
    "root_cause": {
        "da": "Rodårsag: _check_required_tools() håndhæver at påkrævede værktøjer kaldes før <<<DONE>>>.",
        "en": "Root cause: _check_required_tools() enforces that required tools are called before <<<DONE>>>.",
        "es": "Causa raíz: _check_required_tools() exige que las herramientas requeridas sean llamadas antes de <<<DONE>>>.",
        "zh": "根本原因：_check_required_tools() 强制执行必需工具在 <<<DONE>>> 之前被调用。",
    },
    "tool_failed_intro": {
        "da": "Værktøjet {tool} fejlede i {template}/{phase} efter {attempts} forsøg.",
        "en": "Tool {tool} failed in {template}/{phase} after {attempts} attempts.",
        "es": "La herramienta {tool} falló en {template}/{phase} después de {attempts} intentos.",
        "zh": "工具 {tool} 在 {template}/{phase} 中失败，尝试了 {attempts} 次。",
    },
    "tool_failed_solution": {
        "da": "Løsning: Tjek {tool}'s implementering for denne edge case. Overvej at tilføje bedre fejlhåndtering.",
        "en": "Solution: Check {tool}'s implementation for this edge case. Consider adding better error handling.",
        "es": "Solución: Verifica la implementación de {tool} para este caso extremo. Considera añadir mejor manejo de errores.",
        "zh": "解决方案：检查 {tool} 对此边缘情况的实现。考虑添加更好的错误处理。",
    },
    "read_loop_intro": {
        "da": "LLM'en læste {reads} gange uden at skrive i {template}/{phase}.",
        "en": "The LLM read {reads} times without writing in {template}/{phase}.",
        "es": "El LLM leyó {reads} veces sin escribir en {template}/{phase}.",
        "zh": "LLM 在 {template}/{phase} 中读取了 {reads} 次但没有写入。",
    },
    "read_loop_solution": {
        "da": "Løsning: Øg iteration budget for {template}/{phase} i TEMPLATE_PHASE_ITERATION_LIMITS, eller tilføj \"DIN FØRSTE handling SKAL være edit_file\" i instructions/{template}.json.",
        "en": "Solution: Increase iteration budget for {template}/{phase} in TEMPLATE_PHASE_ITERATION_LIMITS, or add \"YOUR FIRST action MUST be edit_file\" in instructions/{template}.json.",
        "es": "Solución: Aumenta el presupuesto de iteración para {template}/{phase} en TEMPLATE_PHASE_ITERATION_LIMITS, o añade \"TU PRIMERA acción DEBE ser edit_file\" en instructions/{template}.json.",
        "zh": "解决方案：增加 {template}/{phase} 在 TEMPLATE_PHASE_ITERATION_LIMITS 中的迭代预算，或在 instructions/{template}.json 中添加 \"你的第一个操作必须是 edit_file\"。",
    },
    "incomplete_intro": {
        "da": "Fasen \"{phase}\" i \"{template}\" løb tør for iterationer før ALLE planlagte moduler var oprettet ({c}/{p}).",
        "en": "Phase \"{phase}\" in \"{template}\" ran out of iterations before ALL planned modules were created ({c}/{p}).",
        "es": "La fase \"{phase}\" en \"{template}\" se quedó sin iteraciones antes de que TODOS los módulos planificados fueran creados ({c}/{p}).",
        "zh": "阶段 \"{phase}\" 在 \"{template}\" 中在创建所有计划模块之前耗尽了迭代次数（{c}/{p}）。",
    },
    "incomplete_missing_modules": {
        "da": "Manglende moduler: {missing}",
        "en": "Missing modules: {missing}",
        "es": "Módulos faltantes: {missing}",
        "zh": "缺少的模块：{missing}",
    },
    "incomplete_changes_header": {
        "da": "Tre forbedringer er nødvendige:",
        "en": "Three improvements are needed:",
        "es": "Se necesitan tres mejoras:",
        "zh": "需要三个改进：",
    },
    "incomplete_change_1_header": {
        "da": "=== 1. Dynamisk iteration budget (agent_tasks.py) ===",
        "en": "=== 1. Dynamic iteration budget (agent_tasks.py) ===",
        "es": "=== 1. Presupuesto de iteración dinámico (agent_tasks.py) ===",
        "zh": "=== 1. 动态迭代预算 (agent_tasks.py) ===",
    },
    "incomplete_change_1_desc": {
        "da": "I funktionen _get_max_iterations() tilføj et tjek for refactor/Ekstraher der beregner budgettet dynamisk:",
        "en": "In the _get_max_iterations() function, add a check for refactor/Ekstraher that calculates the budget dynamically:",
        "es": "En la función _get_max_iterations(), añade una verificación para refactor/Ekstraher que calcule el presupuesto dinámicamente:",
        "zh": "在 _get_max_iterations() 函数中，添加对 refactor/Ekstraher 的检查，动态计算预算：",
    },
    "incomplete_budget_estimate": {
        "da": "Anslået budget for denne fase: {guess} iterationer (nuværende: {current} for refactor Ekstraher).",
        "en": "Estimated budget for this phase: {guess} iterations (current: {current} for refactor Ekstraher).",
        "es": "Presupuesto estimado para esta fase: {guess} iteraciones (actual: {current} para refactor Ekstraher).",
        "zh": "此阶段的估计预算：{guess} 次迭代（当前：refactor Ekstraher 为 {current}）。",
    },
    "incomplete_change_2_header": {
        "da": "=== 2. System-besked ved auto-todo opdatering (agent_tasks.py) ===",
        "en": "=== 2. System message on auto-todo update (agent_tasks.py) ===",
        "es": "=== 2. Mensaje del sistema al actualizar auto-todo (agent_tasks.py) ===",
        "zh": "=== 2. 自动待办事项更新时的系统消息 (agent_tasks.py) ===",
    },
    "incomplete_change_2_desc": {
        "da": "I solve_task_stream(), efter _reconcile_llm_todos(), tilføj:",
        "en": "In solve_task_stream(), after _reconcile_llm_todos(), add:",
        "es": "En solve_task_stream(), después de _reconcile_llm_todos(), añade:",
        "zh": "在 solve_task_stream() 中，在 _reconcile_llm_todos() 之后添加：",
    },
    "incomplete_change_3_header": {
        "da": "=== 3. Fjern 'Brug update_todo' fra instruktion (instructions/refactor.json) ===",
        "en": "=== 3. Remove 'Brug update_todo' from instruction (instructions/refactor.json) ===",
        "es": "=== 3. Elimina 'Brug update_todo' de la instrucción (instructions/refactor.json) ===",
        "zh": "=== 3. 从指令中删除 'Brug update_todo' (instructions/refactor.json) ===",
    },
    "incomplete_change_3_replacement": {
        "da": "Erstat '📋 Brug **update_todo** for at markere hvert modul færdigt.' med: '✅ TODO'er opdateres automatisk — spring update_todo over.'",
        "en": "Replace '📋 Use **update_todo** to mark each module done.' with: '✅ TODOs update automatically — skip update_todo.'",
        "es": "Reemplaza '📋 Usa **update_todo** para marcar cada módulo completo.' con: '✅ Los TODOs se actualizan automáticamente — salta update_todo.'",
        "zh": "将 '📋 使用 **update_todo** 标记每个模块完成。' 替换为：'✅ 待办事项自动更新 — 跳过 update_todo。'",
    },
    "short_output_intro": {
        "da": "Fasen \"{phase}\" i \"{template}\" har ingen eller for kort sektionsinstruktion.",
        "en": "Phase \"{phase}\" in \"{template}\" has no or too short section instruction.",
        "es": "La fase \"{phase}\" en \"{template}\" no tiene instrucción de sección o es demasiado corta.",
        "zh": "阶段 \"{phase}\" 在 \"{template}\" 中没有或只有太短的部分指令。",
    },
    "short_output_solution_fri": {
        "da": "Løsning: Tilføj en \"{phase}\"-sektion til SECTION_INSTRUCTIONS for \"{template}\"-templaten.\n\nÅbn instructions/selvforbedring.json (eller instructions/{template}.json) og tilføj:\n\n  \"{phase}\": \"Kald relevante værktøjer og producér mindst 200 tegn output. Brug edit_file til at redigere og run_tests til at verificere.\"\n\nBrug edit_file med old_text/new_text fra JSON-filen.",
        "en": "Solution: Add a \"{phase}\" section to SECTION_INSTRUCTIONS for the \"{template}\" template.\n\nOpen instructions/selvforbedring.json (or instructions/{template}.json) and add:\n\n  \"{phase}\": \"Call relevant tools and produce at least 200 characters of output. Use edit_file to edit and run_tests to verify.\"\n\nUse edit_file with old_text/new_text from the JSON file.",
        "es": "Solución: Añade una sección \"{phase}\" a SECTION_INSTRUCTIONS para la plantilla \"{template}\".\n\nAbre instructions/selvforbedring.json (o instructions/{template}.json) y añade:\n\n  \"{phase}\": \"Llama a las herramientas relevantes y produce al menos 200 caracteres de salida. Usa edit_file para editar y run_tests para verificar.\"\n\nUsa edit_file con old_text/new_text del archivo JSON.",
        "zh": "解决方案：为 \"{template}\" 模板添加一个 \"{phase}\" 部分到 SECTION_INSTRUCTIONS。\n\n打开 instructions/selvforbedring.json（或 instructions/{template}.json）并添加：\n\n  \"{phase}\": \"调用相关工具并输出至少 200 个字符。使用 edit_file 编辑，使用 run_tests 验证。\"\n\n使用 JSON 文件中的 old_text/new_text 调用 edit_file。",
    },
    "short_output_solution_other": {
        "da": "Løsning: Tjek instructions/{template}.json og tilføj en instruktion for \"{phase}\" der beder LLM'en om at kalde værktøjer og producere mindst 200 tegn.",
        "en": "Solution: Check instructions/{template}.json and add an instruction for \"{phase}\" that tells the LLM to call tools and produce at least 200 characters.",
        "es": "Solución: Verifica instructions/{template}.json y añade una instrucción para \"{phase}\" que indique al LLM llamar herramientas y producir al menos 200 caracteres.",
        "zh": "解决方案：检查 instructions/{template}.json 并为 \"{phase}\" 添加指令，指示 LLM 调用工具并生成至少 200 个字符。",
    },
    "short_output_root_cause": {
        "da": "Rodårsag: LLM'en afsluttede uden værktøjskald eller output. Manglende eller for vag sektionsinstruktion.",
        "en": "Root cause: The LLM ended without tool calls or output. Missing or too vague section instruction.",
        "es": "Causa raíz: El LLM terminó sin llamadas a herramientas o salida. Instrucción de sección faltante o demasiado vaga.",
        "zh": "根本原因：LLM 在没有工具调用或输出的情况下结束。部分指令缺失或过于模糊。",
    },
    "unknown_intro": {
        "da": "Uforklaret fejl i {template}/{phase}.",
        "en": "Unexplained failure in {template}/{phase}.",
        "es": "Fallo inexplicado en {template}/{phase}.",
        "zh": "{template}/{phase} 中发生未知错误。",
    },
    "unknown_approach": {
        "da": "Fremgangsmåde:\n1. Læs agent_log for at forstå hvad der skete.\n2. Tjek om fasen har en instruktion i instructions/ mappen.\n3. Hvis instruktionen mangler: tilføj den.\n4. Hvis instruktionen er for vag: gør den mere specifik.\n5. Kør run_tests() for at verificere.",
        "en": "Approach:\n1. Read agent_log to understand what happened.\n2. Check if the phase has an instruction in the instructions/ folder.\n3. If the instruction is missing: add it.\n4. If the instruction is too vague: make it more specific.\n5. Run run_tests() to verify.",
        "es": "Enfoque:\n1. Lee agent_log para entender qué sucedió.\n2. Verifica si la fase tiene una instrucción en la carpeta instructions/.\n3. Si falta la instrucción: agrégala.\n4. Si la instrucción es demasiado vaga: hazla más específica.\n5. Ejecuta run_tests() para verificar.",
        "zh": "方法：\n1. 阅读 agent_log 了解发生了什么。\n2. 检查阶段在 instructions/ 文件夹中是否有指令。\n3. 如果缺少指令：添加它。\n4. 如果指令太模糊：使其更具体。\n5. 运行 run_tests() 验证。",
    },
}


def _desc_text(key: str, lang: str = "da", **kwargs: str) -> str:
    """Look up localized description text, format with kwargs, fall back to DA."""
    entry = _DESC_TEXTS.get(key, {})
    txt = entry.get(lang) or entry.get("en") or entry.get("da") or key
    return txt.format(**kwargs) if kwargs else txt


def _fix_text(key: str, lang: str = "da", **kwargs: str) -> str:
    """Look up localized fix text, format with kwargs, fall back to DA."""
    entry = _FIX_TEXTS.get(key, {})
    txt = entry.get(lang) or entry.get("en") or entry.get("da") or key
    return txt.format(**kwargs) if kwargs else txt

def _issue_text(key: str, lang: str = "da", **kwargs: str) -> str:
    """Look up localized issue text, format with kwargs, fall back to DA."""
    entry = _ISSUE_TEXTS.get(key, {})
    txt = entry.get(lang) or entry.get("en") or entry.get("da") or key
    return txt.format(**kwargs) if kwargs else txt

# ── Rate limiting ──────────────────────────────────────────────
_RATE_LIMIT_SEC = 300        # 5 minutes between sessions
_last_analysis: dict[str, float] = {}  # session_id → timestamp

# ── Failure types (kept for test compatibility) ────────────────
FAILURE_MISSING_TOOL      = "missing_tool"
FAILURE_TOOL_FAILED       = "tool_failed"
FAILURE_READ_LOOP         = "read_loop"
FAILURE_SHORT_OUTPUT      = "short_output"
FAILURE_PHASE_CHECK       = "phase_check"
FAILURE_INCOMPLETE        = "incomplete"
FAILURE_UNKNOWN           = "unknown"

# ── Research log dir ───────────────────────────────────────────
_LOG_DIR = "logs/autoresearch"

# ── Event queue ────────────────────────────────────────────────
# Each session writes events to: logs/autoresearch/{session_id}/events.jsonl
# The API endpoint reads and returns new events since last poll.


def _event_path(session_id: str) -> str:
    return os.path.join(_LOG_DIR, session_id, "events.jsonl")


def _state_path(session_id: str) -> str:
    return os.path.join(_LOG_DIR, session_id, "state.json")


def _emit_event(session_id: str, event_type: str, data: dict) -> None:
    """Append a progress event to the event queue for this session."""
    dirpath = os.path.join(_LOG_DIR, session_id)
    os.makedirs(dirpath, exist_ok=True)
    entry = {
        "timestamp": time.time(),
        "type": event_type,
        **data,
    }
    try:
        with open(_event_path(session_id), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def get_events(session_id: str, since: float = 0.0) -> list[dict]:
    """Return events for a session since the given timestamp."""
    path = _event_path(session_id)
    if not os.path.exists(path):
        return []
    events = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    if ev.get("timestamp", 0) > since:
                        events.append(ev)
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        pass
    return events


def get_active_sessions() -> list[dict]:
    """Return all active (not done/failed) research sessions."""
    if not os.path.isdir(_LOG_DIR):
        return []
    sessions = []
    for sid in os.listdir(_LOG_DIR):
        sp = _state_path(sid)
        if os.path.exists(sp):
            try:
                with open(sp, encoding="utf-8") as f:
                    state = json.load(f)
                if state.get("status") in ("running", "paused"):
                    sessions.append(state)
            except (OSError, json.JSONDecodeError):
                pass
    return sessions


def get_all_sessions(limit: int = 50) -> list[dict]:
    """Return all research sessions (newest first)."""
    if not os.path.isdir(_LOG_DIR):
        return []
    sessions = []
    for sid in sorted(os.listdir(_LOG_DIR), reverse=True):
        sp = _state_path(sid)
        if os.path.exists(sp):
            try:
                with open(sp, encoding="utf-8") as f:
                    state = json.load(f)
                sessions.append(state)
                if len(sessions) >= limit:
                    break
            except (OSError, json.JSONDecodeError):
                pass
    return sessions


def _paused_path(session_id: str) -> str:
    return os.path.join(_LOG_DIR, session_id, ".paused")


def pause_session(session_id: str) -> bool:
    """Pause a running research session."""
    sp = _state_path(session_id)
    if not os.path.exists(sp):
        return False
    try:
        with open(sp, encoding="utf-8") as f:
            state = json.load(f)
        state["status"] = "paused"
        state["paused_at"] = time.time()
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        # Signal the running thread to pause
        with open(_paused_path(session_id), "w") as f:
            f.write("1")
        _emit_event(session_id, "paused", {"reason": "User requested pause"})
        return True
    except (OSError, json.JSONDecodeError):
        return False


def resume_session(session_id: str) -> bool:
    """Resume a paused research session."""
    sp = _state_path(session_id)
    if not os.path.exists(sp):
        return False
    try:
        with open(sp, encoding="utf-8") as f:
            state = json.load(f)
        state["status"] = "running"
        state.pop("paused_at", None)
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        # Remove pause signal
        pp = _paused_path(session_id)
        if os.path.exists(pp):
            os.remove(pp)
        # Launch a new thread to continue
        _emit_event(session_id, "resumed", {"reason": "User requested resume"})
        return True
    except (OSError, json.JSONDecodeError):
        return False


# ── Classification (preserved from original, used by tests) ────

def classify_failure(task_node: Any, called_tools: dict,
                     tool_log: list, full_response: str,
                     agent: Any) -> tuple[str, dict]:
    """Classify the failure and return (type, evidence).

    Returns:
        Tuple of (failure_type_string, evidence_dict).
    """
    called_names = {k.split("{")[0] for k in (called_tools or {})}
    active = set(agent.tool_registry.active_tools or [])
    required_action = {"edit_file", "write_file", "update_issue_status"}

    # 1. MISSING_TOOL — required action tool never called
    needed = active & required_action
    uncalled = needed - called_names
    if uncalled:
        return FAILURE_MISSING_TOOL, {
            "required": list(needed),
            "called": list(called_names),
            "uncalled": list(uncalled),
        }

    # 2. READ_LOOP — 5+ consecutive reads, no writes
    if tool_log and not (active & required_action):
        recent = tool_log[-8:]
        read_tools = {"read_location", "read_chunk", "list_chunks",
                       "list_files", "list_symbols", "locate", "read_issue"}
        reads = sum(1 for e in recent if e.get("tool") in read_tools)
        writes = sum(1 for e in recent if e.get("tool") in
                       {"edit_file", "write_file", "update_issue_status"})
        if reads >= 5 and writes == 0:
            return FAILURE_READ_LOOP, {
                "consecutive_reads": reads, "total_recent": len(recent)}

    # 3. TOOL_FAILED — tool was called but ALL attempts failed
    if tool_log:
        for tool_name in needed:
            attempts = [e for e in tool_log if e.get("tool") == tool_name]
            if attempts and all(not e.get("success") for e in attempts):
                last_err = (attempts[-1].get("error", "") or
                            str(attempts[-1].get("args", {})))
                return FAILURE_TOOL_FAILED, {
                    "tool": tool_name,
                    "attempts": len(attempts),
                    "last_error": last_err[:200],
                    "last_args": str(attempts[-1].get("args", {}))[:200],
                }

    # 4. SHORT_OUTPUT — no tools called, short text response
    if not called_tools and full_response and len(full_response) < 100:
        return FAILURE_SHORT_OUTPUT, {
            "response_length": len(full_response),
            "response_preview": full_response[:100],
        }

    # 5. INCOMPLETE — budget exhausted before all planned work completed
    _phase_name = getattr(task_node, "name", "") or ""
    _active_tmpl = getattr(agent, "active_template", "") or ""
    _phase_v = _phase_name.lower()
    if _active_tmpl == "refactor" and _phase_v in ("ekstraher", "opdatér"):
        try:
            import os as _os
            _wd = _os.environ.get('AGENT_WORKDIR', '') or _os.getcwd()
            _plan_path = _os.path.join(_wd, "refactor_plan.md")
            if _os.path.exists(_plan_path):
                from file_checks import _parse_refactor_plan_modules
                mods = _parse_refactor_plan_modules(_plan_path)
                if mods:
                    created = [m for m in mods if _os.path.exists(m)]
                    if len(created) < len(mods):
                        return FAILURE_INCOMPLETE, {
                            "modules_planned": len(mods),
                            "modules_created": len(created),
                            "missing_modules": sorted(set(mods) - set(created)),
                            "all_modules": mods,
                        }
        except Exception:
            pass

    return FAILURE_UNKNOWN, {
        "called_tools": list(called_names) if called_tools else [],
        "response_length": len(full_response or ""),
    }


def _find_duplicate_issue(failure_type: str, template: str,
                           phase: str, evidence: dict,
                           issues: list[dict]) -> str | None:
    """Return issue_id if an open issue matches > 70 %."""
    for issue in issues:
        if issue.get("status") not in ("open", "in_progress"):
            continue
        title = (issue.get("title", "") or "").lower()
        desc = (issue.get("description", "") or "").lower()
        combined = title + " " + desc

        score = 0.0

        # Template match (25 %)
        if template and template.lower() in combined:
            score += 0.25

        # Phase match (25 %)
        if phase and phase.lower() in combined:
            score += 0.25

        # Failure type match (30 %)
        type_label = failure_type.replace("_", " ")
        type_matched = type_label in combined
        if not type_matched:
            labels = {
                "missing_tool": {
                    "da": ["manglende værktøj", "manglende vaerktoej", "ikke kaldt"],
                    "en": ["missing tool", "not called"],
                    "es": ["herramienta faltante", "no llamado"],
                    "zh": ["缺少工具", "未调用"],
                },
                "tool_failed": {
                    "da": ["værktøj fejlede", "vaerktoej fejlede", "fejlede"],
                    "en": ["tool failed", "failed"],
                    "es": ["herramienta falló", "falló"],
                    "zh": ["工具失败", "失败"],
                },
                "read_loop": {
                    "da": ["læse-loop", "laese-loop", "læser gentagne", "laeser gentagne"],
                    "en": ["read loop", "reading repeatedly"],
                    "es": ["bucle de lectura", "leyendo repetidamente"],
                    "zh": ["读取循环", "重复读取"],
                },
                "short_output": {
                    "da": ["kort output", "for kort"],
                    "en": ["short output", "too short"],
                    "es": ["salida corta", "demasiado corto"],
                    "zh": ["输出太短", "过短"],
                },
                "incomplete": {
                    "da": ["ufuldstændig", "manglende moduler", "ikke alle moduler"],
                    "en": ["incomplete", "missing modules", "not all modules"],
                    "es": ["incompleto", "módulos faltantes", "no todos los módulos"],
                    "zh": ["不完整", "缺少模块", "并非所有模块"],
                },
                "unknown": {
                    "da": ["uforklaret"],
                    "en": ["unexplained"],
                    "es": ["inexplicado"],
                    "zh": ["未知"],
                },
            }
            _agent_lang = 'da'  # dedup matching works with any language
            lang_labels = labels.get(failure_type, {}).get(_agent_lang, labels.get(failure_type, {}).get("da", []))
            type_matched = any(dl in combined for dl in lang_labels)
        if type_matched:
            score += 0.30

        # Keyword overlap (20 %)
        ev_text = ""
        for v in (evidence or {}).values():
            if isinstance(v, str):
                ev_text += " " + v
            elif isinstance(v, list):
                ev_text += " " + " ".join(str(x) for x in v)
        ev_keywords = {w for w in ev_text.lower().split()
                       if len(w) > 4}
        title_keywords = {w for w in title.split() if len(w) > 4}
        if ev_keywords and title_keywords:
            overlap = ev_keywords & title_keywords
            ratio = len(overlap) / max(len(ev_keywords), len(title_keywords))
            score += 0.20 * ratio

        if score >= 0.70:
            return issue.get("id")

    return None


def _check_issue_fix_applied(failure_type: str, evidence: dict,
                             template: str, phase: str) -> bool:
    """Return True if the fix for this failure type is already in the codebase,
    meaning the existing CORE issue can be auto-resolved.

    Checks the current code state programmatically. Only supports failure
    types where a deterministic code-check is possible.
    """
    if failure_type == FAILURE_INCOMPLETE:
        # Check if _get_max_iterations already has dynamic budget logic
        # for refactor Ekstraher.
        try:
            import ast as _ast
            _tasks_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "agent_tasks.py")
            if not os.path.exists(_tasks_path):
                return False
            with open(_tasks_path, encoding="utf-8") as _f:
                _tree = _ast.parse(_f.read())
            for _node in _ast.walk(_tree):
                if isinstance(_node, _ast.FunctionDef) and _node.name == "_get_max_iterations":
                    _src = _ast.unparse(_node)
                    # Dynamic budget: checks refactor plan and parses modules
                    if "_parse_refactor_plan_modules" in _src and "refactor_plan.md" in _src:
                        return True
            return False
        except Exception:
            return False

    # For unknown failure types, assume fix NOT applied (conservative)
    return False


def _check_filters(agent: Any, issue: dict | None = None,
                    template: str = "", phase: str = "",
                    failure_type: str = "") -> bool:
    """Check whether autoresearch should run based on agent filters.

    Agent can have:
      agent.autoresearch_enabled = True/False (master switch)
      agent.autoresearch_filters = {
          "types": ["bug", "security", ...],       # default: all
          "templates": ["issue_handler", ...],      # default: all
          "failure_types": ["missing_tool", ...],   # default: all
      }
    """
    if not getattr(agent, "autoresearch_enabled", False):
        return False

    filters = getattr(agent, "autoresearch_filters", {}) or {}
    if not isinstance(filters, dict):
        filters = {}

    # Filter by issue type
    allowed_types = filters.get("types", [])
    if allowed_types and issue:
        itype = issue.get("type", "")
        if itype and itype not in allowed_types:
            return False

    # Filter by template
    allowed_templates = filters.get("templates", [])
    if allowed_templates and template:
        if template not in allowed_templates:
            return False

    # Filter by failure type
    allowed_failures = filters.get("failure_types", [])
    if allowed_failures and failure_type:
        if failure_type not in allowed_failures:
            return False

    return True
    """Check rate limit — max 1 analysis per 5 min per session."""
    now = time.time()
    last = _last_analysis.get(session_id, 0)
    if now - last < _RATE_LIMIT_SEC:
        return False
    _last_analysis[session_id] = now
    return True


def _rate_limit_ok(session_id: str) -> bool:
    """Check rate limit — max 1 analysis per 5 min per session."""
    now = time.time()
    last = _last_analysis.get(session_id, 0)
    if now - last < _RATE_LIMIT_SEC:
        return False
    _last_analysis[session_id] = now
    return True


def trigger_if_needed(agent: Any, task_node: Any,
                       called_tools: dict,
                       full_response: str,
                       messages: list[dict] | None = None) -> str | None:
    """Called from _finalize_task_stream when a task fails.

    Checks autoresearch_enabled + filters, rate-limits, deduplicates,
    creates a CORE-issue, and returns the issue_id so the caller can
    start an inline sub-session (instead of a background thread).
    """
    if getattr(task_node, "status", "") != "failed":
        return None

    # Belt-and-suspenders: refuse if already inside an auto-research sub-session
    _d = getattr(agent, '_autoresearch_depth', 0)
    if isinstance(_d, int) and _d > 0:
        return None

    session_id = getattr(agent, "_session_id", "unknown")

    # Gather context for filter check
    tool_log = getattr(agent, "_tool_log", []) or []
    phase = getattr(task_node, "name", "ukendt")
    template = getattr(agent, "active_template", "ukendt")
    failure_type, evidence = classify_failure(
        task_node, called_tools, tool_log, full_response, agent)

    # Apply filters
    if not _check_filters(agent, template=template, phase=phase,
                          failure_type=failure_type):
        return None

    if not _rate_limit_ok(session_id):
        agent._log("AUTOR", "Auto-research: rate-limited", session_id)
        return None

    # Dedup
    try:
        from agent_issues import _load_issues, update_issue_status
        data = _load_issues()
        dup_id = _find_duplicate_issue(
            failure_type, template, phase, evidence, data.get("issues", []))
        if dup_id:
            # Check if the existing issue's fix is ALREADY applied
            if _check_issue_fix_applied(failure_type, evidence, template, phase):
                update_issue_status(
                    agent, dup_id, "resolved",
                    f"Auto-resolved: fix allerede implementeret i koden "
                    f"(verificeret via {failure_type} check)")
                agent._log("AUTOR", f"Auto-resolved {dup_id} — fix allerede implementeret",
                           f"{failure_type} i {template}/{phase}")
            else:
                agent._log("AUTOR", f"Auto-research: dublet — {dup_id}",
                           f"{failure_type} i {template}/{phase}")
            return None
    except Exception as exc:
        agent._log("AUTOR", "Auto-research: dedup fejlede", str(exc))

    # Create a CORE-issue documenting the failure
    issue_id = _create_issue(agent, failure_type, evidence, template, phase, "")
    if issue_id:
        _save_core_reference(session_id, issue_id, template, phase, failure_type)
        agent._log("AUTOR", f"Oprettede {issue_id} — original session: {session_id[:12]}",
                   f"{template}/{phase} fejlede med {failure_type}")
        # NOTE: No longer starts background thread — caller handles inline execution
    return issue_id


def start_research_for_issue(agent: Any, issue_id: str) -> None:
    """Start autonomous research for a specific issue.

    Now uses the inline sub-session flow. For POST endpoints without
    SSE context (e.g. /api/autoresearch/run/<issue_id>), this logs
    a depreciation notice. Use the automatic inline flow instead
    (trigger_if_needed → _finalize_task_stream → _execute_autoresearch_issue).
    """
    agent._log("AUTOR", f"Auto-research: {issue_id} — brug automatisk inline flow",
               "start_research_for_issue er deprecated. Auto-research kører nu "
               "automatisk via SSE under fase-eksekvering.")



def _save_state(research_id: str, state: dict) -> None:
    """Save research session state to disk."""
    dirpath = os.path.join(_LOG_DIR, research_id)
    os.makedirs(dirpath, exist_ok=True)
    try:
        with open(_state_path(research_id), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError:
        pass




def _create_issue(agent: Any, failure_type: str, evidence: dict,
                   template: str, phase: str, analysis: str) -> str | None:
    """Create a CORE-issue documenting the research result.
    
    Generates a specific, actionable issue based on failure type
    and evidence - not a generic template.
    
    Returns:
        The issue_id if created, or None on failure.
    """
    from agent_issues import create_issue
    lang = getattr(agent, 'lang', 'da')

    title = _build_issue_title(failure_type, evidence, template, phase, lang)
    desc = _build_issue_description(failure_type, evidence, template, phase, analysis, lang)
    impact = _build_issue_impact(failure_type, evidence, template, phase, lang)
    proposed_fix = _build_issue_fix(failure_type, evidence, template, phase, lang)

    result = create_issue(
        agent,
        title=title[:120],
        type="self",
        severity="medium",
        description=desc[:2000],
        location=f"agent_skills.py:selvforbedring:{template}/{phase}",
        impact=impact[:300],
        proposed_fix=proposed_fix[:500],
    )
    if result.get("success"):
        issue_id = result.get("issue", {}).get("id", "?")
        existing_label = "(existing)" if result.get("existing") else "(new)"
        agent._log("AUTOR", f"Auto-research issue {issue_id} {existing_label}", title[:120])
        return issue_id
    else:
        agent._log("AUTOR", "Auto-research: create_issue failed",
                   str(result.get("error", "")))
        return None


def _build_issue_title(failure_type: str, evidence: dict,
                        template: str, phase: str, lang: str = "da") -> str:
    """Build a specific title based on failure context."""
    if failure_type == FAILURE_MISSING_TOOL:
        uncalled = ', '.join(evidence.get("uncalled", []))
        return _issue_text("title_missing_tool", lang, uncalled=uncalled, template=template, phase=phase)
    elif failure_type == FAILURE_TOOL_FAILED:
        tool = evidence.get("tool", "?")
        attempts = str(evidence.get('attempts', 0))
        return _issue_text("title_tool_failed", lang, tool=tool, template=template, phase=phase, attempts=attempts)
    elif failure_type == FAILURE_READ_LOOP:
        reads = str(evidence.get('consecutive_reads', 0))
        return _issue_text("title_read_loop", lang, reads=reads, template=template, phase=phase)
    elif failure_type == FAILURE_SHORT_OUTPUT:
        length = str(evidence.get('response_length', 0))
        return _issue_text("title_short_output", lang, length=length, template=template, phase=phase)
    elif failure_type == FAILURE_INCOMPLETE:
        p = str(evidence.get("modules_planned", "?"))
        c = str(evidence.get("modules_created", "?"))
        return _issue_text("title_incomplete", lang, c=c, p=p, template=template, phase=phase)
    else:
        return _issue_text("title_unknown", lang, template=template, phase=phase)


def _build_issue_description(failure_type: str, evidence: dict,
                               template: str, phase: str,
                               analysis: str, lang: str = "da") -> str:
    """Build a detailed description with specific context."""
    ft_label = failure_type.replace('_', ' ')
    lines = [_desc_text("header", lang, failure_type=ft_label)]
    lines.append(_desc_text("phase_line", lang, template=template, phase=phase))
    lines.append("")

    if failure_type == FAILURE_MISSING_TOOL:
        uncalled = ', '.join(evidence.get('uncalled', []))
        called = ', '.join(evidence.get('called', []))
        required = ', '.join(evidence.get('required', []))
        lines.append(_desc_text("what_happened", lang))
        lines.append(_desc_text("missing_tool_what", lang, uncalled=uncalled))
        lines.append(_desc_text("called_tools", lang, called=called))
        lines.append(_desc_text("active_tools", lang, required=required))
        lines.append("")
        lines.append(_desc_text("why_problem", lang))
        lines.append(_desc_text("missing_tool_why", lang))
        lines.append("")
        lines.append(_desc_text("possible_causes", lang))
        lines.append(_desc_text("missing_tool_reasons", lang))

    elif failure_type == FAILURE_TOOL_FAILED:
        tool = evidence.get("tool", "?")
        attempts = str(evidence.get('attempts', 0))
        last_error = evidence.get('last_error', '?')
        last_args = evidence.get('last_args', '?')
        lines.append(_desc_text("what_happened", lang))
        lines.append(_desc_text("tool_failed_what", lang, tool=tool, attempts=attempts))
        lines.append(_desc_text("last_error", lang, error=last_error))
        lines.append(_desc_text("last_args", lang, args=last_args))
        lines.append("")
        lines.append(_desc_text("analysis", lang))
        lines.append(_desc_text("tool_failed_analysis", lang))

    elif failure_type == FAILURE_READ_LOOP:
        reads = str(evidence.get('consecutive_reads', 0))
        lines.append(_desc_text("what_happened", lang))
        lines.append(_desc_text("read_loop_what", lang, reads=reads))
        lines.append("")
        lines.append(_desc_text("analysis", lang))
        lines.append(_desc_text("read_loop_analysis", lang))

    elif failure_type == FAILURE_INCOMPLETE:
        p = str(evidence.get("modules_planned", "?"))
        c = str(evidence.get("modules_created", "?"))
        missing = ', '.join(evidence.get("missing_modules", []))
        lines.append(_desc_text("what_happened", lang))
        lines.append(_desc_text("incomplete_what", lang, c=c, p=p))
        lines.append(_desc_text("missing_modules", lang, missing=missing))
        lines.append("")
        lines.append(_desc_text("analysis", lang))
        lines.append(_desc_text("incomplete_analysis", lang))
        lines.append("")
        lines.append(_desc_text("expected_next", lang))
        lines.append(_desc_text("incomplete_next_steps", lang))

    else:
        tools_str = str(evidence.get('called_tools', []))
        length = str(evidence.get('response_length', 0))
        lines.append(_desc_text("what_happened", lang))
        lines.append(_desc_text("unknown_what", lang, tools=tools_str))
        lines.append(_desc_text("unknown_length", lang, length=length))
        lines.append("")
        lines.append(analysis[:500])

    return "\n".join(lines)


def _build_issue_impact(failure_type: str, evidence: dict,
                         template: str, phase: str, lang: str = "da") -> str:
    """Build impact description."""
    if failure_type == FAILURE_MISSING_TOOL:
        uncalled = ', '.join(evidence.get("uncalled", []))
        return _issue_text("impact_missing_tool", lang, phase=phase, template=template, uncalled=uncalled)
    elif failure_type == FAILURE_TOOL_FAILED:
        tool = evidence.get("tool", "?")
        return _issue_text("impact_tool_failed", lang, tool=tool, template=template, phase=phase)
    elif failure_type == FAILURE_READ_LOOP:
        return _issue_text("impact_read_loop", lang, template=template, phase=phase)
    elif failure_type == FAILURE_INCOMPLETE:
        c = str(evidence.get("modules_created", 0))
        p = str(evidence.get("modules_planned", 0))
        return _issue_text("impact_incomplete", lang, c=c, p=p, template=template, phase=phase)
    else:
        return _issue_text("impact_unknown", lang, phase=phase, template=template)


def _build_issue_fix(failure_type: str, evidence: dict,
                      template: str, phase: str, lang: str = "da") -> str:
    """Build a specific, actionable fix proposal based on failure context.

    Generates an EXECUTABLE proposed_fix — not just a description.
    The fix includes exact file paths, code context, and edit_file
    instructions so the LLM can execute it directly.
    """

    # Helper: try to find relevant code context
    def _find_context(*symbols: str) -> str:
        """Try to locate symbols and return file + line context."""
        try:
            from agent_files import locate_code
            for sym in symbols:
                loc = locate_code("agent_skills.py", sym)
                if loc.get("success"):
                    ctx = _fix_text("context_label", lang)
                    return (f"agent_skills.py around line {loc['line']}-{loc['end_line']}. "
                            f"Use locate(name=\"{sym}\") to see the exact code.")
        except Exception:
            pass
        return ""

    if failure_type == FAILURE_MISSING_TOOL:
        uncalled = evidence.get("uncalled", [])
        lines = [
            _fix_text("missing_tool_intro", lang,
                      phase=phase, template=template,
                      uncalled=', '.join(uncalled)),
        ]

        ctx = _find_context(*uncalled, "SECTION_INSTRUCTIONS",
                            "TEMPLATE_TASK_TOOLS", "TEMPLATE_PHASE_ITERATION_LIMITS")
        if ctx:
            lines.append(f"\n**{_fix_text('context_label', lang)}:** {ctx}")

        for tool in uncalled:
            if tool == "edit_file":
                lines.extend([
                    "",
                    _fix_text("edit_file_problem", lang, template=template, phase=phase),
                    "",
                    _fix_text("solution_choose", lang),
                    "1. " + _fix_text("solution_edit_file_1", lang, template=template, phase=phase),
                    "2. " + _fix_text("solution_edit_file_2", lang, template=template, phase=phase),
                    "3. " + _fix_text("solution_edit_file_3", lang, template=template, phase=phase),
                ])
            elif tool == "write_file":
                lines.append(
                    "\n" + _fix_text("solution_write_file", lang, template=template, phase=phase)
                )
            elif tool == "update_issue_status":
                lines.append(
                    "\n" + _fix_text("solution_update_issue_status", lang, template=template, phase=phase)
                )

        lines.append(
            "\n" + _fix_text("root_cause", lang)
        )
        return "\n".join(lines)

    elif failure_type == FAILURE_TOOL_FAILED:
        tool = evidence.get("tool", "?")
        attempts = str(evidence.get('attempts', 0))
        last_args = evidence.get('last_args', '?')
        last_error = evidence.get('last_error', '?')
        ctx = _find_context(tool, "git_ops.edit_file", "tools.ToolRegistry.execute")
        result = (
            _fix_text("tool_failed_intro", lang, tool=tool, template=template, phase=phase, attempts=attempts)
            + f"\nLast args: {last_args}\nLast error: {last_error}"
        )
        if ctx:
            result += f"\n\n**{_fix_text('context_label', lang)}:** {ctx}"
        result += "\n\n" + _fix_text("tool_failed_solution", lang, tool=tool)
        return result

    elif failure_type == FAILURE_READ_LOOP:
        reads = str(evidence.get('consecutive_reads', 0))
        ctx = _find_context("TEMPLATE_PHASE_ITERATION_LIMITS", "MAX_TASK_ITERATIONS")
        result = _fix_text("read_loop_intro", lang, reads=reads, template=template, phase=phase)
        if ctx:
            result += f"\n\n**{_fix_text('context_label', lang)}:** {ctx}"
        result += "\n\n" + _fix_text("read_loop_solution", lang, template=template, phase=phase)
        return result

    elif failure_type == FAILURE_INCOMPLETE:
        p = evidence.get("modules_planned", 0)
        c = evidence.get("modules_created", 0)
        missing = evidence.get("missing_modules", [])
        _current_budget = 20
        try:
            from config import TEMPLATE_PHASE_ITERATION_LIMITS
            _current_budget = TEMPLATE_PHASE_ITERATION_LIMITS.get("refactor", {}).get("Ekstraher", 20)
        except Exception:
            pass
        guess_budget = max(_current_budget, 2 + p * 2 + 5)

        result = (
            _fix_text("incomplete_intro", lang, phase=phase, template=template,
                      c=str(c), p=str(p))
            + "\n" + _fix_text("incomplete_missing_modules", lang,
                               missing=', '.join(missing))
            + "\n\n" + _fix_text("incomplete_changes_header", lang)
            + "\n\n" + _fix_text("incomplete_change_1_header", lang)
            + "\n" + _fix_text("incomplete_change_1_desc", lang)
            + "\n\n"
            + "  if template == \"refactor\" and task_lower == \"ekstraher\":\n"
            + "      import os\n"
            + "      from file_checks import _parse_refactor_plan_modules\n"
            + "      wd = os.environ.get('AGENT_WORKDIR', '') or os.getcwd()\n"
            + "      pp = os.path.join(wd, 'refactor_plan.md')\n"
            + "      if os.path.exists(pp):\n"
            + "          mods = _parse_refactor_plan_modules(pp)\n"
            + "          if mods:\n"
            + "              return max({_current_budget}, 2 + len(mods) * 2 + 5)\n\n"
            + _fix_text("incomplete_budget_estimate", lang,
                        guess=str(guess_budget), current=str(_current_budget))
            + "\n\n" + _fix_text("incomplete_change_2_header", lang)
            + "\n" + _fix_text("incomplete_change_2_desc", lang)
            + "\n\n"
            + "  if _auto_done_ids:\n"
            + "      messages.append({'role': 'user', 'content':\n"
            + "          f'[SYSTEM: \u2705 TODO auto-updated: {\", \".join(_auto_done_ids)}]'})\n\n"
            + _fix_text("incomplete_change_3_header", lang)
            + "\n" + _fix_text("incomplete_change_3_replacement", lang)
        )
        return result

    elif failure_type == FAILURE_SHORT_OUTPUT:
        ctx = _find_context("SECTION_INSTRUCTIONS", "get_templates")
        lines = [
            _fix_text("short_output_intro", lang, phase=phase, template=template),
        ]
        if ctx:
            lines.append(f"\n**{_fix_text('context_label', lang)}:** {ctx}\n")

        if template == "fri":
            lines.append(
                _fix_text("short_output_solution_fri", lang, phase=phase, template=template)
            )
        else:
            lines.append(
                _fix_text("short_output_solution_other", lang, phase=phase, template=template)
            )

        lines.append(
            "\n" + _fix_text("short_output_root_cause", lang)
        )
        return "\n".join(lines)

    else:
        ctx_lines = [_fix_text("unknown_intro", lang, template=template, phase=phase)]
        ctx = _find_context("SECTION_INSTRUCTIONS",
                            "TEMPLATE_TASK_TOOLS",
                            "TEMPLATE_PHASE_ITERATION_LIMITS",
                            "get_templates")
        if ctx:
            ctx_lines.append(f"\n**{_fix_text('context_label', lang)}:** {ctx}\n")

        ctx_lines.append(
            _fix_text("unknown_approach", lang)
        )
        return "\n".join(ctx_lines)


def _save_core_reference(session_id: str, core_id: str,
                          template: str, phase: str,
                          failure_type: str) -> None:
    """Gem en reference i den originale session så CORE-issue kan spores tilbage.

    Skriver direkte til session JSON-filen for at sikre at referencen
    overlever selv hvis sessionen ikke gemmes normalt.
    """
    if not session_id or session_id == "unknown":
        return
    sess_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
    path = os.path.join(sess_dir, f"{session_id}.json")
    if not os.path.exists(path):
        return
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        log = data.setdefault("agent_log", [])
        log.append({
            "timestamp": time.time(),
            "level": "CORE",
            "message": f"Oprettede {core_id} for fejl i {template}/{phase}",
            "detail": f"failure_type={failure_type}",
        })
        data.setdefault("core_issues", []).append({
            "id": core_id,
            "template": template,
            "phase": phase,
            "failure_type": failure_type,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _update_sessions_for_core_resolution(core_id: str, resolution_session: str) -> None:
    """Når et CORE-issue resolves, opdater alle sessions der refererer til det.

    Scannner sessions-mappen for JSON-filer med core_issues referencer.
    """
    if not core_id:
        return
    sess_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
    if not os.path.isdir(sess_dir):
        return
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    for fname in os.listdir(sess_dir):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(sess_dir, fname)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue
        refs = data.get("core_issues", [])
        if not refs:
            continue
        updated = False
        for ref in refs:
            if ref.get("id", "").upper() == core_id.upper():
                if not ref.get("resolved"):
                    ref["resolved"] = now
                    ref["resolved_by"] = resolution_session
                    updated = True
                break
        if updated:
            log = data.setdefault("agent_log", [])
            log.append({
                "timestamp": time.time(),
                "level": "CORE",
                "message": f"{core_id} er resolved af session {resolution_session[:12]}",
                "detail": "",
            })
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
