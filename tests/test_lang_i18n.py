"""Test i18n.py K enum and lang.py t() function."""
import pytest
from i18n import K
import lang


class TestKEnum:
    def test_K_is_strenum(self):
        assert hasattr(K, "__members__")

    def test_K_values_are_strings(self):
        for member in K:
            assert isinstance(member.value, str)

    def test_K_templates(self):
        assert K.T_RESUME == "templates.resume"
        assert K.T_KODEANALYSE == "templates.kodeanalyse"
        assert K.T_DIFFANALYSE == "templates.diffanalyse"
        assert K.T_FRI == "templates.fri"
        assert K.T_PROGRAMMERING == "templates.programmering"
        assert K.T_PYTHON_ARKITEKTUR == "templates.python-arkitektur"

    def test_K_template_prompts(self):
        assert K.TP_RESUME == "template_prompts.resume"
        assert K.TP_KODEANALYSE == "template_prompts.kodeanalyse"
        assert K.TP_DIFFANALYSE == "template_prompts.diffanalyse"
        assert K.TP_FRI == "template_prompts.fri"
        assert K.TP_PROGRAMMERING == "template_prompts.programmering"
        assert K.TP_PYTHON_ARKITEKTUR == "template_prompts.python-arkitektur"

    def test_K_template_fallback(self):
        assert K.TF_RESUME == "template_fallback.resume"
        assert K.TF_KODEANALYSE == "template_fallback.kodeanalyse"
        assert K.TF_DIFFANALYSE == "template_fallback.diffanalyse"
        assert K.TF_PROGRAMMERING == "template_fallback.programmering"
        assert K.TF_PYTHON_ARKITEKTUR == "template_fallback.python-arkitektur"

    def test_K_fallback_tree_nodes(self):
        expected = [
            K.FT_UNDERSTAND_PURPOSE,
            K.FT_READ_IMPORTS,
            K.FT_IDENTIFY_FRAMEWORKS,
            K.FT_ANALYZE_STRUCTURE,
            K.FT_REVIEW_ENDPOINTS,
            K.FT_CHECK_CONFIG,
            K.FT_ASSESS_QUALITY,
            K.FT_SECURITY_ANALYSIS,
            K.FT_ERROR_HANDLING,
            K.FT_DOCUMENT_FINDINGS,
            K.FT_UNDERSTAND_NUMBERS,
            K.FT_PERFORM_ADDITION,
            K.FT_CONCLUDE,
            K.FT_ANALYZE_PROBLEM,
            K.FT_FIND_STRATEGY,
            K.FT_IMPLEMENT_SOLUTION,
            K.FT_TEST_VALIDATE,
        ]
        for k in expected:
            assert isinstance(k.value, str)
            assert "." in k.value

    def test_K_tools(self):
        tools = [
            K.TOOL_GITHUB_CREATE_REPO,
            K.TOOL_GITHUB_LIST_REPOS,
            K.TOOL_GITHUB_CREATE_ISSUE,
            K.TOOL_GITHUB_CREATE_PR,
            K.TOOL_GIT_STATUS,
            K.TOOL_GIT_ADD_ALL,
            K.TOOL_GIT_COMMIT,
            K.TOOL_GIT_PUSH,
            K.TOOL_GIT_SET_REMOTE,
            K.TOOL_GIT_REMOTE_STATUS,
            K.TOOL_GIT_DIFF,
            K.TOOL_GIT_LOG,
            K.TOOL_GIT_CREATE_BRANCH,
            K.TOOL_GIT_CURRENT_BRANCH,
            K.TOOL_GIT_BRANCH_LIST,
            K.TOOL_GIT_PULL,
            K.TOOL_GIT_CHECKOUT,
        ]
        for k in tools:
            assert k.value.startswith("tools.")
            assert k in K

    def test_K_tool_messages(self):
        assert K.TOOL_DUPLICATE == "tool_duplicate"
        assert K.TOOL_DUPLICATE_MSG == "tool_duplicate_msg"
        assert K.TOOL_BLOCKED == "tool_blocked"
        assert K.TOOL_HALLUCINATED == "tool_hallucinated"

    def test_K_system_prompt_fragments(self):
        assert K.SYS_EXAMPLE_PREFIX == "sys_example_prefix"
        assert K.SYS_MARKER_WARNING == "sys_marker_warning"
        assert K.SYS_ERROR_PREFIX == "sys_error_prefix"
        assert K.SYS_FALLBACK_TOOL == "sys_fallback_tool"
        assert K.SYS_FILE_CONTEXT == "sys_file_context"

    def test_K_file_context(self):
        assert K.FILE_CONTEXT_HEADER == "file_context_header"
        assert K.FILE_TRUNCATED == "file_truncated"
        assert K.FILE_DIFF_HEADER == "file_diff_header"
        assert K.FILE_LOG_HEADER == "file_log_header"

    def test_K_log_messages(self):
        keys = [
            K.LOG_DECOMPOSE_START,
            K.LOG_TASK_START,
            K.LOG_TASK_DONE,
            K.LOG_TASK_FAILED,
            K.LOG_AUTO_DONE,
            K.LOG_TREE_EXECUTION,
            K.LOG_TOOL_CALLING,
            K.LOG_TOOL_RESULT,
        ]
        for k in keys:
            assert k.value.startswith("log.")
            assert k in K

    def test_K_errors(self):
        assert K.ERR_NO_PROMPT == "errors.no_prompt"
        assert K.ERR_DECOMPOSE_FIRST == "errors.decompose_first"

    def test_K_ui(self):
        assert K.UI_TITLE == "ui.title"
        assert K.UI_ALL_DONE == "ui.all_done"
        assert K.UI_SESSION_LOADED == "ui.session_loaded"
        assert K.UI_SESSION_SAVED == "ui.session_saved"

    def test_K_session_knowledge(self):
        assert K.DEMO_MATH_FACT == "session.demo_math_fact"
        assert K.DEMO_OPTIMIZATION == "session.demo_optimization"
        assert K.DEMO_KNOWLEDGE_HDR == "session.demo_knowledge_header"


class TestTFunction:
    def test_t_da(self):
        assert lang.t(K.UI_TITLE, "da") == "Agenten"
        assert lang.t(K.LOG_TASK_START, "da") == "Påbegynder opgave"

    def test_t_en(self):
        assert lang.t(K.UI_TITLE, "en") == "Agenten"
        assert lang.t(K.LOG_TASK_START, "en") == "Starting task"

    def test_t_es(self):
        assert lang.t(K.UI_TITLE, "es") == "Agenten"
        assert lang.t(K.LOG_TASK_START, "es") == "Iniciando tarea"

    def test_t_zh(self):
        assert lang.t(K.UI_TITLE, "zh") == "Agenten"
        assert lang.t(K.LOG_TASK_START, "zh") == "开始任务"

    def test_t_with_string_key(self):
        assert lang.t("language", "da") == "Dansk"
        assert lang.t("answer_in", "en") == "Answer in English"

    def test_t_nested_key(self):
        assert "Overblik" in lang.t(K.TF_RESUME, "da")
        assert "Nøglepunkter" in lang.t(K.TF_RESUME, "da")
        assert "Konklusion" in lang.t(K.TF_RESUME, "da")

    def test_t_unknown_key_returns_fallback(self):
        assert lang.t("nonexistent.key", "da") == "?nonexistent.key"
        assert lang.t("log.nonexistent", "en") == "?log.nonexistent"

    def test_t_sys_error_prefix(self):
        assert lang.t(K.SYS_ERROR_PREFIX, "da") == "FEJL"
        assert lang.t(K.SYS_ERROR_PREFIX, "en") == "ERROR"
        assert lang.t(K.SYS_ERROR_PREFIX, "es") == "ERROR"
        assert lang.t(K.SYS_ERROR_PREFIX, "zh") == "错误"

    def test_t_demo_knowledge(self):
        assert lang.t("session.demo_math_fact", "en") == "2 + 2 = 4 is true by definition of addition"
        assert lang.t("session.demo_math_fact", "da") == "2 + 2 = 4 er sandt per definition af addition"
        assert "compr" in lang.t("session.demo_optimization", "es").lower()

    def test_t_demo_knowledge_direct_value(self):
        v = lang.t("session.demo_math_fact", "en")
        assert "addition" in v.lower()
        assert v != "?session.demo_math_fact"
        assert v == "2 + 2 = 4 is true by definition of addition"

    def test_t_demo_knowledge_in_all_langs(self):
        for lc in ['da', 'en', 'es', 'zh']:
            v = lang.t("session.demo_math_fact", lc)
            assert v != f"?session.demo_math_fact", f"{lc} failed: got {v}"
            assert len(v) > 5

    def test_t_known_template_sections(self):
        assert isinstance(lang.t(K.TP_RESUME, "da"), str)
        assert len(lang.t(K.TP_RESUME, "da")) > 10
        assert "{lang_instruction}" in lang.t(K.TP_RESUME, "da")

    def test_t_known_log_messages(self):
        assert lang.t(K.LOG_DECOMPOSE_START, "da") == "Starter nedbrydning"
        assert lang.t(K.LOG_DECOMPOSE_START, "en") == "Starting decomposition"
        assert lang.t(K.LOG_DECOMPOSE_START, "es") == "Iniciando descomposición"
        assert lang.t(K.LOG_DECOMPOSE_START, "zh") == "开始分解"

    def test_t_unknown_lang_falls_back_to_da(self):
        result = lang.t(K.UI_TITLE, "fr")
        assert result == "?ui.title" or result == lang.t(K.UI_TITLE, "da")

    def test_t_with_format_args(self):
        val = lang.t("log.N_files", "da").format(n=5)
        assert "5" in val


class TestLangStructure:
    def test_LANG_has_all_four_langs(self):
        assert "da" in lang.LANG
        assert "en" in lang.LANG
        assert "es" in lang.LANG
        assert "zh" in lang.LANG

    def test_each_lang_has_required_sections(self):
        required = ["language", "answer_in", "templates", "template_prompts",
                    "tools", "tool_system_prompt", "log", "ui"]
        for lang_code in ["da", "en", "es", "zh"]:
            for section in required:
                assert section in lang.LANG[lang_code], f"{lang_code} missing '{section}'"

    def test_errors_section_in_all_langs(self):
        for lang_code in ["da", "en", "es", "zh"]:
            assert "errors" in lang.LANG[lang_code], f"{lang_code} missing 'errors'"

    def test_templates_all_four(self):
        for lang_code in ["da", "en", "es", "zh"]:
            tpls = lang.LANG[lang_code]["templates"]
            assert "resume" in tpls
            assert "kodeanalyse" in tpls
            assert "diffanalyse" in tpls
            assert "fri" in tpls
            assert "agenten" in tpls, f"{lang_code} missing template 'agenten'"
            assert "programmering" in tpls, f"{lang_code} missing template 'programmering'"
            assert "python-arkitektur" in tpls, f"{lang_code} missing template 'python-arkitektur'"

    def test_tool_descriptions_all_four_langs(self):
        for lang_code in ["da", "en", "es", "zh"]:
            tools = lang.LANG[lang_code]["tools"]
            assert len(tools) >= 17
            assert "github_create_repo" in tools
            assert "git_status" in tools
            assert "git_log" in tools
            assert "git_create_branch" in tools, f"{lang_code} missing git_create_branch"
            assert "git_checkout" in tools, f"{lang_code} missing git_checkout"
            assert "git_current_branch" in tools, f"{lang_code} missing git_current_branch"
            assert "git_branch_list" in tools, f"{lang_code} missing git_branch_list"
            assert "git_pull" in tools, f"{lang_code} missing git_pull"

    def test_sys_error_prefix_all_langs(self):
        for lang_code in ["da", "en", "es", "zh"]:
            val = lang.LANG[lang_code].get("sys_error_prefix")
            assert val is not None, f"{lang_code} missing sys_error_prefix"

    def test_session_knowledge_all_langs(self):
        for lang_code in ["da", "en", "es", "zh"]:
            s = lang.LANG[lang_code]
            ui = s.get("ui", {})
            assert ("session.default_name" in ui or "session.default_name" in s), f"{lang_code} missing session.default_name"
            assert ("session.demo_math_fact" in ui or "session.demo_math_fact" in s), f"{lang_code} missing session.demo_math_fact"
            assert ("session.demo_optimization" in ui or "session.demo_optimization" in s), f"{lang_code} missing session.demo_optimization"
            assert ("session.demo_knowledge_header" in ui or "session.demo_knowledge_header" in s), f"{lang_code} missing session.demo_knowledge_header"

    def test_file_context_header_all_langs(self):
        for lang_code in ["da", "en", "es", "zh"]:
            val = lang.LANG[lang_code].get("file_context_header")
            assert val is not None

    def test_ui_keys_count_roughly_aligned(self):
        ui_counts = {lc: len(lang.LANG[lc]["ui"]) for lc in ["da", "en", "es", "zh"]}
        counts = list(ui_counts.values())
        assert max(counts) - min(counts) < 30

    def test_agenten_template_in_all_langs(self):
        for lc in ["da", "en", "es", "zh"]:
            tpls = lang.LANG[lc]["template_prompts"]
            assert "agenten" in tpls, f"{lc} missing agenten prompt"
            assert isinstance(tpls["agenten"], str)
            assert len(tpls["agenten"]) > 20

    def test_agenten_fallback_in_all_langs(self):
        for lc in ["da", "en", "es", "zh"]:
            fallbacks = lang.LANG[lc]["template_fallback"]
            assert "agenten" in fallbacks, f"{lc} missing agenten fallback"
            assert isinstance(fallbacks["agenten"], list)
            assert len(fallbacks["agenten"]) == 4, f"{lc} agenten fallback should have 4 steps, got {len(fallbacks['agenten'])}"

    def test_K_agenten_template_keys(self):
        assert K.T_AGENTEN == "templates.agenten"
        assert K.TP_AGENTEN == "template_prompts.agenten"
        assert K.TF_AGENTEN == "template_fallback.agenten"
        assert K.T_AGENTEN in K
        assert K.TP_AGENTEN in K
        assert K.TF_AGENTEN in K


class TestGetUiTranslations:
    def test_get_ui_translations_da(self):
        ui = lang.get_ui_translations("da")
        assert "title" in ui
        assert "sessions" in ui
        assert "decompose" in ui
        assert len(ui) > 100

    def test_get_ui_translations_en(self):
        ui = lang.get_ui_translations("en")
        assert "title" in ui
        assert "Decompose" in ui["decompose"]

    def test_get_ui_translations_es(self):
        ui = lang.get_ui_translations("es")
        assert "title" in ui
        assert "Descomponer" in ui["decompose"]

    def test_get_ui_translations_zh(self):
        ui = lang.get_ui_translations("zh")
        assert "title" in ui
        assert "分解" in ui["decompose"]

    def test_get_ui_translations_unknown_falls_back_to_da(self):
        ui = lang.get_ui_translations("xx")
        assert "title" in ui
        assert ui.get("sessions") == lang.LANG["da"]["ui"]["sessions"]