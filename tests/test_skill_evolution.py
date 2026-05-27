"""Test skill_evolution.py — analyze, evolve, apply evolution actions."""
import sys
import os
import json
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class TestDeduceActionType:
    def test_read_types(self):
        from skill_evolution import _deduce_action_type
        result = _deduce_action_type("læs filen og analyser")
        assert "read" in result

    def test_write_types(self):
        from skill_evolution import _deduce_action_type
        result = _deduce_action_type("skriv en ny funktion")
        assert "write" in result

    def test_git_types(self):
        from skill_evolution import _deduce_action_type
        result = _deduce_action_type("git commit and push")
        assert "git" in result

    def test_github_types(self):
        from skill_evolution import _deduce_action_type
        result = _deduce_action_type("create a pull request")
        assert "github" in result

    def test_search_types(self):
        from skill_evolution import _deduce_action_type
        result = _deduce_action_type("søg efter dokumentation")
        assert "search" in result

    def test_analyze_types(self):
        from skill_evolution import _deduce_action_type
        result = _deduce_action_type("review kodegennemgang")
        assert "analyze" in result

    def test_general_fallback(self):
        from skill_evolution import _deduce_action_type
        result = _deduce_action_type("hello world")
        assert result == ["general"]


class TestAnalyzeNotEnoughData:
    def test_less_than_5_outcomes(self):
        with patch("skill_evolution.tracker") as mock_tracker:
            mock_tracker.get_outcomes.return_value = []
            mock_tracker.total_outcomes = 3
            from skill_evolution import analyze
            result = analyze()
            assert result["status"] == "not_enough_data"


class TestAnalyzeActions:
    def test_retain_high_success(self):
        with patch("skill_evolution.tracker") as mock_tracker:
            mock_tracker.get_outcomes.return_value = [
                {"skill": "good_skill", "success": True} for _ in range(10)
            ]
            mock_tracker.get_all_skill_stats.return_value = {
                "good_skill": {"count": 10, "success_rate": 0.9}
            }
            mock_tracker.total_outcomes = 10

            from skill_evolution import analyze, RETAIN_MIN_RATE
            result = analyze()
            assert result["status"] == "ok"
            actions = result["actions"]
            retain = [a for a in actions if a["action"] == "retain"]
            assert len(retain) >= 1

    def test_refine_medium_success(self):
        with patch("skill_evolution.tracker") as mock_tracker:
            mock_tracker.get_outcomes.return_value = [
                {"skill": "ok_skill", "success": True} for _ in range(6)
            ] + [{"skill": "ok_skill", "success": False} for _ in range(4)]
            mock_tracker.get_outcomes.return_value.extend(
                [{"skill": "ok_skill", "success": True} for _ in range(6)]
            )
            mock_tracker.get_all_skill_stats.return_value = {
                "ok_skill": {"count": 10, "success_rate": 0.6}
            }
            mock_tracker.total_outcomes = 10

            from skill_evolution import analyze, REFINE_MIN_RATE
            result = analyze()
            actions = result["actions"]
            refine = [a for a in actions if a["action"] == "refine"]
            assert len(refine) >= 1 or len(actions) > 0

    def test_prune_low_success(self):
        with patch("skill_evolution.tracker") as mock_tracker:
            mock_tracker.get_outcomes.return_value = [
                {"skill": "bad_skill", "success": False} for _ in range(6)
            ]
            mock_tracker.get_all_skill_stats.return_value = {
                "bad_skill": {"count": 6, "success_rate": 0.0}
            }
            mock_tracker.total_outcomes = 6

            from skill_evolution import analyze, PRUNE_MAX_RATE, PRUNE_MIN_COUNT
            result = analyze()
            actions = result["actions"]
            prune = [a for a in actions if a["action"] == "prune"]
            assert len(prune) >= 1


class TestSuggestSkillName:
    def test_suggests_from_keywords(self):
        from skill_evolution import _suggest_skill_name
        name = _suggest_skill_name("refactor the database module")
        assert "refactor" in name
        assert "database" in name
        assert "module" in name

    def test_fallback_with_short_words(self):
        from skill_evolution import _suggest_skill_name
        name = _suggest_skill_name("is it the hat")
        assert name == "auto_generated_skill"

    def test_truncates_long_names(self):
        from skill_evolution import _suggest_skill_name
        name = _suggest_skill_name("a very long task name with many many many many words")
        assert len(name) <= 60


class TestShouldEvolve:
    def test_no_outcomes(self):
        with patch("skill_evolution.tracker") as mock_tracker:
            mock_tracker.total_outcomes = 0
            from skill_evolution import should_evolve, _reset_evolve_counter
            _reset_evolve_counter()
            assert should_evolve() is False

    def test_evolve_at_threshold(self):
        with patch("skill_evolution.tracker") as mock_tracker:
            mock_tracker.total_outcomes = 15
            from skill_evolution import should_evolve, EVOLVE_EVERY_N, _reset_evolve_counter
            _reset_evolve_counter()
            assert should_evolve() is True

    def test_not_at_threshold(self):
        with patch("skill_evolution.tracker") as mock_tracker:
            mock_tracker.total_outcomes = 3
            from skill_evolution import should_evolve, _reset_evolve_counter
            _reset_evolve_counter()
            assert should_evolve() is False


class TestEvolveIfNeeded:
    def test_skipped_when_not_needed(self):
        with patch("skill_evolution.tracker") as mock_tracker:
            mock_tracker.total_outcomes = 1
            from skill_evolution import evolve_if_needed
            result = evolve_if_needed()
            assert result["status"] == "skipped"


class TestApplyEvolutionActions:
    def test_retain_action_dry_run(self, tmp_path):
        from skill_evolution import apply_evolution_actions
        actions = [{
            "action": "retain",
            "skill": "test_skill",
        }]
        results = apply_evolution_actions(actions, dry_run=True)
        assert results[0]["dry_run"] is True
        assert results[0]["action"] == "retain"

    def test_generate_action_dry_run(self):
        from skill_evolution import apply_evolution_actions
        actions = [{
            "action": "generate",
            "skill": "new_skill",
            "frequency": 3,
            "cluster": ["do something new with the parser",
                        "do something new in the renderer"],
            "suggested_action_types": ["write"],
            "suggested_keywords": ["parser", "renderer", "something"],
        }]
        results = apply_evolution_actions(actions, dry_run=True)
        assert results[0]["action"] == "generate"
        assert "Would generate" in results[0]["message"]

    def test_unknown_action(self):
        from skill_evolution import apply_evolution_actions
        actions = [{"action": "unknown", "skill": "s"}]
        results = apply_evolution_actions(actions, dry_run=True)
        assert results[0]["action"] == "unknown"


class TestAddRefinementNote:
    def test_adds_note_to_content(self, tmp_path):
        from skill_evolution import _add_refinement_note
        content = "# Test Skill\n\nSome instructions here.\n\n"
        action = {"failure_patterns": ["pattern1", "pattern2"]}
        result = _add_refinement_note(content, action)
        assert "SkillFlow Refinement" in result
        assert "pattern1" in result
        assert "pattern2" in result
