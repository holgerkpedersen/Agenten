"""i18n migration for agent_tasks.py — critical system messages sent to LLM"""
import os, sys, json

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
from i18n import K

# === Define new keys for agent_tasks.py critical messages ===
NEW_KEYS = [
    # Chunk hint - section headers
    ("SYS_AVAILABLE_FILES", "system.available_files", {
        "da": "\n\n## TILG\u00c6NGELIGE FILER (projektmappe: {dir})",
        "en": "\n\n## AVAILABLE FILES (project: {dir})",
        "es": "\n\n## ARCHIVOS DISPONIBLES (proyecto: {dir})",
        "zh": "\n\n## 可用文件（项目：{dir}）"
    }),
    ("SYS_DELEGATIONS_HEADER", "system.delegations_header", {
        "da": "\n\n## DELEGERINGER\nNogle funktioner i de indl\u00e6ste filer er stubs, der kun videresender til en anden fil.",
        "en": "\n\n## DELEGATIONS\nSome functions in the loaded files are stubs that only forward to another file.",
        "es": "\n\n## DELEGACIONES\nAlgunas funciones en los archivos cargados son stubs que solo reenv\u00edan a otro archivo.",
        "zh": "\n\n## 委托\n一些已加载文件中的函数是仅转发到另一个文件的存根。"
    }),
    # Same-tool-loop escape
    ("SYS_SAME_TOOL_LOOP", "system.same_tool_loop", {
        "da": "[SYSTEM: Du har kaldt '{tool}' {count} gange i tr\u00e6k.{tip} Brug <<<DONE>>> eller skift v\u00e6rkt\u00f8j.]",
        "en": "[SYSTEM: You have called '{tool}' {count} times in a row.{tip} Use <<<DONE>>> or switch tools.]",
        "es": "[SYSTEM: Has llamado a '{tool}' {count} veces seguidas.{tip} Usa <<<DONE>>> o cambia de herramienta.]",
        "zh": "[SYSTEM: 您已连续调用'{tool}'{count}次。{tip}使用<<<DONE>>>或切换工具。]"
    }),
    # Dedup duplicate result
    ("SYS_DUP_RESULT", "system.dup_result", {
        "da": "Du har allerede dette resultat. G\u00e5 videre eller brug <<<DONE>>>.",
        "en": "You already have this result. Continue or use <<<DONE>>>.",
        "es": "Ya tienes este resultado. Contin\u00faa o usa <<<DONE>>>.",
        "zh": "您已有此结果。继续或使用<<<DONE>>>。"
    }),
    # Dedup-loop escape
    ("SYS_DEDUP_LOOP", "system.dedup_loop", {
        "da": "[SYSTEM: Du er i en l\u00f8kke med identiske resultater. STOP med at l\u00e6se. BRUG et v\u00e6rkt\u00f8j der SKRIVER: {tools}. Forklar HVORFOR du ikke kan skrive \u2014 hvad mangler du?]",
        "en": "[SYSTEM: You are in a loop with identical results. STOP reading. USE a tool that WRITES: {tools}. Explain WHY you can't write \u2014 what are you missing?]",
        "es": "[SYSTEM: Est\u00e1s en un bucle con resultados id\u00e9nticos. DEJA de leer. USA una herramienta que ESCRIBA: {tools}. Explica POR QU\u00c9 no puedes escribir \u2014 \u00bfqu\u00e9 te falta?]",
        "zh": "[SYSTEM: 您陷入了重复结果的循环。停止读取。使用能写入的工具：{tools}。解释为什么无法写入 \u2014 缺少什么？]"
    }),
    # Read-loop escape
    ("SYS_READ_LOOP", "system.read_loop", {
        "da": "[SYSTEM: Du har lavet {count} l\u00e6sekald i tr\u00e6k uden at skrive noget. STOP med at l\u00e6se. BRUG et v\u00e6rkt\u00f8j der SKRIVER: {tools}. Forklar HVORFOR du bliver ved med at l\u00e6se \u2014 hvad mangler du?]",
        "en": "[SYSTEM: You have made {count} read calls in a row without writing anything. STOP reading. USE a tool that WRITES: {tools}. Explain WHY you keep reading \u2014 what are you missing?]",
        "es": "[SYSTEM: Has hecho {count} llamadas de lectura seguidas sin escribir nada. DEJA de leer. USA una herramienta que ESCRIBA: {tools}. Explica POR QU\u00c9 sigues leyendo \u2014 \u00bfqu\u00e9 te falta?]",
        "zh": "[SYSTEM: 您已连续进行{count}次读取调用而未写入任何内容。停止读取。使用能写入的工具：{tools}。解释为什么继续读取 \u2014 缺少什么？]"
    }),
    # Read-loop blocked
    ("SYS_READ_BLOCKED", "system.read_blocked", {
        "da": "[SYSTEM: L\u00e6sekald blokeret. Brug {tools}.]",
        "en": "[SYSTEM: Read calls blocked. Use {tools}.]",
        "es": "[SYSTEM: Lecturas bloqueadas. Usa {tools}.]",
        "zh": "[SYSTEM: 读取调用已被阻止。使用{tools}。]"
    }),
    # Issue resolved block
    ("SYS_ISSUE_RESOLVED", "system.issue_resolved", {
        "da": "BLOCKERET \u2014 issuet er allerede markeret som resolved. Redig\u00e9r IKKE filer. Brug <<<DONE>>> for at afslutte, eller gen\u00e5bn issuet med update_issue_status('<id>', 'open') f\u00f8rst.",
        "en": "BLOCKED \u2014 the issue is already marked as resolved. Do NOT edit files. Use <<<DONE>>> to finish, or reopen the issue with update_issue_status('<id>', 'open') first.",
        "es": "BLOQUEADO \u2014 el issue ya est\u00e1 marcado como resuelto. NO edites archivos. Usa <<<DONE>>> para finalizar, o reabre el issue con update_issue_status('<id>', 'open') primero.",
        "zh": "已阻止 \u2014 该问题已标记为已解决。不要编辑文件。使用<<<DONE>>>完成，或先使用update_issue_status('<id>', 'open')重新打开问题。"
    }),
    # edit_file old_text not found - with content
    ("SYS_EDIT_OLDTEXT_CONTENT", "system.edit_oldtext_content", {
        "da": "old_text blev ikke fundet i filen. Her er filens nuv\u00e6rende indhold:\n--- START AF FIL ---\n{content}\n--- SLUT AF FIL ---\nBrug teksten ovenfor som old_text. Kopier den pr\u00e6cise tekst du vil erstatte, og s\u00e6t den som old_text. Pr\u00f8v igen.",
        "en": "old_text was not found in the file. Here is the file's current content:\n--- START OF FILE ---\n{content}\n--- END OF FILE ---\nUse the text above as old_text. Copy the exact text you want to replace and set it as old_text. Try again.",
        "es": "old_text no se encontr\u00f3 en el archivo. Aqu\u00ed est\u00e1 el contenido actual:\n--- INICIO DEL ARCHIVO ---\n{content}\n--- FIN DEL ARCHIVO ---\nUsa el texto de arriba como old_text. Copia el texto exacto a reemplazar y establ\u00e9celo como old_text. Intenta de nuevo.",
        "zh": "在文件中未找到old_text。以下是文件的当前内容：\n--- 文件开始 ---\n{content}\n--- 文件结束 ---\n使用上面的文本作为old_text。复制要替换的确切文本并设置为old_text。重试。"
    }),
    # edit_file old_text not found - no prior read
    ("SYS_EDIT_OLDTEXT_NOREAD", "system.edit_oldtext_noread", {
        "da": "old_text blev ikke fundet i nogen tidligere l\u00e6seresultat. Du skal l\u00e6se filen F\u00d8RST med read_chunk eller locate, og derefter kopiere den pr\u00e6cise tekst som old_text. Pr\u00f8v igen.",
        "en": "old_text was not found in any previous read result. You must read the file FIRST with read_chunk or locate, then copy the exact text as old_text. Try again.",
        "es": "old_text no se encontr\u00f3 en ning\u00fan resultado de lectura anterior. Debes leer el archivo PRIMERO con read_chunk o locate, luego copiar el texto exacto como old_text. Intenta de nuevo.",
        "zh": "在任何先前的读取结果中未找到old_text。您必须先用read_chunk或locate读取文件，然后将确切的文本复制为old_text。重试。"
    }),
    # Fail-loop escape
    ("SYS_FAIL_LOOP", "system.fail_loop", {
        "da": "[SYSTEM: {count} v\u00e6rkt\u00f8jskald i tr\u00e6k fejlede.{tip}]",
        "en": "[SYSTEM: {count} tool calls in a row failed.{tip}]",
        "es": "[SYSTEM: {count} llamadas a herramientas seguidas fallaron.{tip}]",
        "zh": "[SYSTEM: 连续{count}次工具调用失败。{tip}]"
    }),
    # DONE when tests fail
    ("SYS_DONE_TESTS_FAIL", "system.done_tests_fail", {
        "da": "DU KAN IKKE afslutte med <<<DONE>>> n\u00e5r tests fejler. Ret koden med edit_file og k\u00f8r run_tests() igen indtil ALLE tests best\u00e5r.",
        "en": "You CANNOT finish with <<<DONE>>> when tests fail. Fix the code with edit_file and run run_tests() again until ALL tests pass.",
        "es": "NO PUEDES finalizar con <<<DONE>>> cuando las pruebas fallan. Arregla el c\u00f3digo con edit_file y ejecuta run_tests() de nuevo hasta que TODAS pasen.",
        "zh": "测试失败时不能使用<<<DONE>>>完成。使用edit_file修复代码，然后重新运行run_tests()直到所有测试通过。"
    }),
    # Required tools missing - refactor
    ("SYS_REQUIRED_TOOLS_REFACTOR", "system.required_tools_refactor", {
        "da": "FEJL: Du har ikke kaldt write_file, edit_file, extract_symbol, remove_symbol eller add_import i {count} iterationer. Refactor kr\u00e6ver at du SKRIVER kode. Brug write_file for nye moduler eller edit_file for at opdatere api_server.py.",
        "en": "ERROR: You have not called write_file, edit_file, extract_symbol, remove_symbol or add_import in {count} iterations. Refactor requires you to WRITE code. Use write_file for new modules or edit_file to update api_server.py.",
        "es": "ERROR: No has llamado a write_file, edit_file, extract_symbol, remove_symbol o add_import en {count} iteraciones. Refactor requiere que ESCRIBAS c\u00f3digo. Usa write_file para nuevos m\u00f3dulos o edit_file para actualizar.",
        "zh": "错误：您在{count}次迭代中未调用write_file、edit_file、extract_symbol、remove_symbol或add_import。重构要求您编写代码。使用write_file创建新模块或使用edit_file更新。"
    }),
    # Required tools missing - programming
    ("SYS_REQUIRED_TOOLS_PROGRAMMING", "system.required_tools_programming", {
        "da": "FEJL: Du har ikke kaldt write_file eller edit_file i {count} iterationer. Programming kr\u00e6ver at du SKRIVER kode og design. Brug write_file til at oprette filer (arkitektur, plan, kode). Stop med at l\u00e6se og begynd at skrive.",
        "en": "ERROR: You have not called write_file or edit_file in {count} iterations. Programming requires you to WRITE code and design. Use write_file to create files (architecture, plan, code). Stop reading and start writing.",
        "es": "ERROR: No has llamado a write_file o edit_file en {count} iteraciones. Programaci\u00f3n requiere que ESCRIBAS c\u00f3digo y dise\u00f1o. Usa write_file para crear archivos. Deja de leer y empieza a escribir.",
        "zh": "错误：您在{count}次迭代中未调用write_file或edit_file。编程要求您编写代码和设计。使用write_file创建文件。停止阅读，开始编写。"
    }),
    # Auto-resolved notice
    ("SYS_AUTO_RESOLVED", "system.auto_resolved", {
        "da": "Auto-resolved: Analyse konkluderede at fejlen allerede er l\u00f8st. {source}",
        "en": "Auto-resolved: Analysis concluded that the error is already fixed. {source}",
        "es": "Auto-resuelto: El an\u00e1lisis concluy\u00f3 que el error ya est\u00e1 solucionado. {source}",
        "zh": "自动解决：分析得出结论，错误已修复。{source}"
    }),
    # Incomplete result warning
    ("SYS_INCOMPLETE_RESULT", "system.incomplete_result", {
        "da": "\u26a0\ufe0f ADVARSEL: Dette resultat ser ufuldst\u00e6ndigt ud. Overvej at k\u00f8re opgaven igen med en tydeligere prompt.",
        "en": "\u26a0\ufe0f WARNING: This result appears incomplete. Consider running the task again with a clearer prompt.",
        "es": "\u26a0\ufe0f ADVERTENCIA: Este resultado parece incompleto. Considera ejecutar la tarea de nuevo con un prompt m\u00e1s claro.",
        "zh": "\u26a0\ufe0f 警告：此结果似乎不完整。考虑使用更清晰的提示重新运行任务。"
    }),
    # Tool call failed retry hint
    ("SYS_TOOL_FAILED_RETRY", "system.tool_failed_retry", {
        "da": "{tool} mislykkedes. L\u00e6s filen igen og kopier teksten n\u00f8jagtigt som old_text.",
        "en": "{tool} failed. Read the file again and copy the text exactly as old_text.",
        "es": "{tool} fall\u00f3. Lee el archivo de nuevo y copia el texto exactamente como old_text.",
        "zh": "{tool}失败。重新读取文件并精确复制文本作为old_text。"
    }),
    # Context truncated
    ("SYS_CONTEXT_TRUNCATED", "system.context_truncated", {
        "da": "[... tidligere kontekst afkortet ...]",
        "en": "[... previous context truncated ...]",
        "es": "[... contexto anterior truncado ...]",
        "zh": "[... 先前的上下文已截断 ...]"
    }),
    # No files loaded
    ("SYS_NO_FILES_LOADED", "system.no_files_loaded", {
        "da": "OBS: Ingen filer er indl\u00e6st. Du KAN svare direkte uden at kalde v\u00e6rkt\u00f8jer f\u00f8rst. Sp\u00f8rg IKKE efter filnavne \u2014 brug din egen viden til at besvare opgaven.",
        "en": "NOTE: No files are loaded. You CAN answer directly without calling tools first. Do NOT ask for filenames \u2014 use your own knowledge to answer the task.",
        "es": "NOTA: No hay archivos cargados. PUEDES responder directamente sin llamar herramientas primero. NO preguntes por nombres de archivos \u2014 usa tu propio conocimiento.",
        "zh": "注意：未加载任何文件。您可以直接回答而无需先调用工具。不要询问文件名 \u2014 使用您自己的知识来回答任务。"
    }),
    # Read after locate hint
    ("SYS_READ_AFTER_LOCATE", "system.read_after_locate", {
        "da": "\u2705 OBS: Du har allerede l\u00e6st funktion(er) i denne fil med locate. Brug locate(filepath='...', name='andet_navn') i stedet for read_chunk \u2014 det er hurtigere.",
        "en": "\u2705 NOTE: You have already read function(s) in this file with locate. Use locate(filepath='...', name='other_name') instead of read_chunk \u2014 it's faster.",
        "es": "\u2705 NOTA: Ya has le\u00eddo funci\u00f3n(es) en este archivo con locate. Usa locate(filepath='...', name='otro_nombre') en lugar de read_chunk \u2014 es m\u00e1s r\u00e1pido.",
        "zh": "\u2705 注意：您已使用locate读取了此文件中的函数。使用locate(filepath='...', name='other_name')代替read_chunk \u2014 更快。"
    }),
]

# === 1. Update i18n.py ===
i18n_path = os.path.join(BASE, 'i18n.py')
with open(i18n_path, 'r', encoding='utf-8') as f:
    i18n_content = f.read()

# Find position to insert after TOOL_ANALYZE_OWN_LOGS
insert_after = 'TOOL_ANALYZE_OWN_LOGS'
idx = i18n_content.find(insert_after)
if idx >= 0:
    # Find end of that line
    idx = i18n_content.find('\n', idx) + 1
    new_block = '\n'
    for const_name, dot_key, _ in NEW_KEYS:
        new_block += f'    {const_name:<35s} = "{dot_key}"\n'
    i18n_content = i18n_content[:idx] + new_block + i18n_content[idx:]

with open(i18n_path, 'w', encoding='utf-8') as f:
    f.write(i18n_content)
print(f'Updated i18n.py with {len(NEW_KEYS)} new keys')

# === 2. Update lang files ===
lang_files = {}
for lang in ['da', 'en', 'es', 'zh']:
    path = os.path.join(BASE, 'lang', f'{lang}.json')
    with open(path, 'r', encoding='utf-8') as f:
        lang_files[lang] = json.load(f)

for const_name, dot_key, translations in NEW_KEYS:
    parts = dot_key.split('.')
    section, key = parts
    for lang in ['da', 'en', 'es', 'zh']:
        if section not in lang_files[lang]:
            lang_files[lang][section] = {}
        lang_files[lang][section][key] = translations[lang]

for lang in ['da', 'en', 'es', 'zh']:
    path = os.path.join(BASE, 'lang', f'{lang}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(lang_files[lang], f, ensure_ascii=False, indent=4)
        f.write('\n')
    print(f'Updated {lang}.json')

# === 3. Read agent_tasks.py and make replacements ===
tasks_path = os.path.join(BASE, 'agent_tasks.py')
with open(tasks_path, 'r', encoding='utf-8') as f:
    c = f.read()

# Define replacements: (old_text_fragment, new_text_template)
# The new_text_template uses {variables} for f-string params
REPLACEMENTS = [
    # _build_chunk_hint - section header
    ('hint = f"\\n\\n## TILGÆNGELIGE FILER (projektmappe: {base_dir})"',
     'hint = t(K.SYS_AVAILABLE_FILES, agent.lang).format(dir=base_dir)'),
    
    # _build_chunk_hint - delegations header
    ('hint += \'\\n\\n## DELEGERINGER\\nNogle funktioner i de indlæste filer er stubs, der kun videresender til en anden fil.\\n\'',
     'hint += t(K.SYS_DELEGATIONS_HEADER, agent.lang) + \'\\n\' + \'\\n\'.join(delegation_lines) + \'\\n\''),
     
    # same-tool-loop escape
    ('f"[SYSTEM: Du har kaldt \'{tool_name}\' {consecutive_same_tool} gange i træk.{tip}"',
     't(K.SYS_SAME_TOOL_LOOP, agent.lang, tool=tool_name, count=consecutive_same_tool, tip=tip or "")'),
    ('f" Brug <<<DONE>>> eller skift værktøj.]"', ''),  # This line should be removed (merged with above)
    
    # dup result
    ('f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: Du har allerede dette resultat. Gå videre eller brug <<<DONE>>>."',
     'f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {t(K.SYS_DUP_RESULT, agent.lang)}"'),
    
    # dedup-loop escape
    ('f"[SYSTEM: Du er i en løkke med identiske resultater. '
     'STOP med at læse. BRUG et værktøj der SKRIVER: '
     '{", ".join(write_tools)}. '
     'Forklar HVORFOR du ikke kan skrive — hvad mangler du?]"',
     't(K.SYS_DEDUP_LOOP, agent.lang, tools=", ".join(write_tools))'),
    
    # read-loop escape
    ('f"[SYSTEM: Du har lavet {consecutive_reads} læsekald i træk uden at skrive noget. '
     'STOP med at læse. BRUG et værktøj der SKRIVER: '
     '{", ".join(write_tools)}. '
     'Forklar HVORFOR du bliver ved med at læse — hvad mangler du?]"',
     't(K.SYS_READ_LOOP, agent.lang, count=consecutive_reads, tools=", ".join(write_tools))'),
    
    # read-loop blocked
    ('f"[SYSTEM: Læsekald blokeret. Brug {", ".join(write_tools)}.]"',
     't(K.SYS_READ_BLOCKED, agent.lang, tools=", ".join(write_tools))'),
    
    # issue resolved
    ('f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: BLOCKERET — issuet er allerede markeret som resolved. Redigér IKKE filer. Brug <<<DONE>>> for at afslutte, eller genåbn issuet med update_issue_status(\'<id>\', \'open\') først."',
     'f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {t(K.SYS_ISSUE_RESOLVED, agent.lang)}"'),
    
    # edit_file old_text not found - with content
    ('f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: old_text blev ikke fundet i '
     'filen. Her er filens nuværende indhold:\\n'
     '--- START AF FIL ---\\n{_actual_content}\\n--- SLUT AF FIL ---\\n'
     'Brug teksten ovenfor som old_text. Kopier den præcise tekst '
     'du vil erstatte, og sæt den som old_text. Prøv igen."',
     'f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {t(K.SYS_EDIT_OLDTEXT_CONTENT, agent.lang, content=_actual_content)}"'),
    
    # edit_file old_text not found - no prior read
    ('f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: old_text blev ikke fundet i '
     'nogen tidligere læseresultat. Du skal læse filen FØRST med '
     'read_chunk eller locate, og derefter kopiere den præcise tekst '
     'som old_text. Prøv igen."',
     'f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {t(K.SYS_EDIT_OLDTEXT_NOREAD, agent.lang)}"'),
    
    # fail-loop escape
    ('f"[SYSTEM: {consecutive_failures} værktøjskald i træk fejlede.{tip}]"',
     't(K.SYS_FAIL_LOOP, agent.lang, count=consecutive_failures, tip=tip or "")'),
    
    # tool failed retry
    ('f"\\n\\n⚠️ {t(K.SYS_ERROR_PREFIX, agent.lang)}: {tool_label} mislykkedes. Læs filen igen og kopier teksten nøjagtigt som old_text."',
     'f"\\n\\n⚠️ {t(K.SYS_ERROR_PREFIX, agent.lang)}: {t(K.SYS_TOOL_FAILED_RETRY, agent.lang, tool=tool_label)}"'),
    
    # done when tests fail
    ('f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: DU KAN IKKE afslutte med <<<DONE>>> når tests fejler. Ret koden med edit_file og kør run_tests() igen indtil ALLE tests består."',
     'f"{t(K.SYS_ERROR_PREFIX, agent.lang)}: {t(K.SYS_DONE_TESTS_FAIL, agent.lang)}"'),
    
    # context truncated
    ('"[... tidligere kontekst afkortet ...]"',
     't(K.SYS_CONTEXT_TRUNCATED, agent.lang)'),
    
    # no files loaded
    ('"OBS: Ingen filer er indlæst. Du KAN svare direkte uden at kalde værktøjer først. Spørg IKKE efter filnavne — brug din egen viden til at besvare opgaven."',
     't(K.SYS_NO_FILES_LOADED, agent.lang)'),
    
    # read after locate hint
    ('"\\n\\n✅ OBS: Du har allerede læst funktion(er) i denne fil med locate. Brug locate(filepath=\'...\', name=\'andet_navn\') i stedet for read_chunk — det er hurtigere."',
     't(K.SYS_READ_AFTER_LOCATE, agent.lang)'),
    
    # auto-resolved
    ('f"Auto-resolved: Analyse konkluderede at fejlen allerede er løst. {source_text[:200]}"',
     't(K.SYS_AUTO_RESOLVED, agent.lang, source=source_text[:200])'),
    
    # incomplete result
    ('full_response + "\\n\\n⚠️ ADVARSEL: Dette resultat ser ufuldstændigt ud. Overvej at køre opgaven igen med en tydeligere prompt."',
     'full_response + "\\n\\n" + t(K.SYS_INCOMPLETE_RESULT, agent.lang)'),
]

changes = 0
for old, new in REPLACEMENTS:
    if old in c:
        c = c.replace(old, new, 1)
        changes += 1
    else:
        print(f'  NOT FOUND: {old[:60]}...')

with open(tasks_path, 'w', encoding='utf-8') as f:
    f.write(c)
print(f'\nUpdated agent_tasks.py with {changes}/{len(REPLACEMENTS)} replacements')

# === 4. Also remove the second line of the same-tool-loop that becomes orphaned ===
# Check for lines that just have 'f" Brug <<<DONE>>> eller skift værktøj.]"'
# These should have been merged into the t() call

print('\n=== DONE ===')
