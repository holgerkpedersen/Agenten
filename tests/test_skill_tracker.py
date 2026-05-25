"""Test skill_tracker.py — outcome tracking for SkillFlow."""
import sys
import os
import json
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from skill_tracker import SkillTracker


class TestSkillTrackerInit:
    def test_default_init(self, tmp_path):
        with patch.object(SkillTracker, "DATA_DIR", str(tmp_path)):
            tracker = SkillTracker()
            assert tracker.total_outcomes == 0
            assert tracker._session_id is not None

    def test_loads_existing_data(self, tmp_path):
        data_file = tmp_path / "skill_outcomes.json"
        data_file.write_text(json.dumps([
            {"skill": "test", "success": True, "task": "do something"}
        ]))
        with patch.object(SkillTracker, "DATA_DIR", str(tmp_path)):
            tracker = SkillTracker()
            assert tracker.total_outcomes == 1

    def test_handles_corrupt_data(self, tmp_path):
        data_file = tmp_path / "skill_outcomes.json"
        data_file.write_text("not valid json")
        with patch.object(SkillTracker, "DATA_DIR", str(tmp_path)):
            tracker = SkillTracker()
            assert tracker.total_outcomes == 0


class TestSkillTrackerRecord:
    def test_record_outcome(self, tmp_path):
        with patch.object(SkillTracker, "DATA_DIR", str(tmp_path)):
            tracker = SkillTracker()
            tracker.record("test_skill", "test task", True, duration_ms=100, tokens_used=50, detail="worked", template="fri")
            assert tracker.total_outcomes == 1
            outcomes = tracker.get_outcomes("test_skill")
            assert len(outcomes) == 1
            assert outcomes[0]["skill"] == "test_skill"
            assert outcomes[0]["success"] is True

    def test_record_failure(self, tmp_path):
        with patch.object(SkillTracker, "DATA_DIR", str(tmp_path)):
            tracker = SkillTracker()
            tracker.record("test_skill", "test task", False)
            stats = tracker.get_stats("test_skill")
            assert stats["success_rate"] == 0.0
            assert stats["failures"] == 1

    def test_record_multiple_outcomes(self, tmp_path):
        with patch.object(SkillTracker, "DATA_DIR", str(tmp_path)):
            tracker = SkillTracker()
            tracker.record("skill_a", "task1", True)
            tracker.record("skill_a", "task2", False)
            tracker.record("skill_a", "task3", True)
            stats = tracker.get_stats("skill_a")
            assert stats["success_rate"] == 2/3
            assert stats["count"] == 3


class TestSkillTrackerGetStats:
    def test_no_outcomes(self, tmp_path):
        with patch.object(SkillTracker, "DATA_DIR", str(tmp_path)):
            tracker = SkillTracker()
            stats = tracker.get_stats("nonexistent")
            assert stats["success_rate"] == 0
            assert stats["count"] == 0

    def test_all_successes(self, tmp_path):
        with patch.object(SkillTracker, "DATA_DIR", str(tmp_path)):
            tracker = SkillTracker()
            for i in range(5):
                tracker.record("s", f"task{i}", True)
            stats = tracker.get_stats("s")
            assert stats["success_rate"] == 1.0
            assert stats["count"] == 5

    def test_all_skills_stats(self, tmp_path):
        with patch.object(SkillTracker, "DATA_DIR", str(tmp_path)):
            tracker = SkillTracker()
            tracker.record("a", "t1", True)
            tracker.record("b", "t2", False)
            stats = tracker.get_all_skill_stats()
            assert "a" in stats
            assert "b" in stats


class TestUnmatchedTasks:
    def test_get_unmatched_tasks(self, tmp_path):
        with patch.object(SkillTracker, "DATA_DIR", str(tmp_path)):
            tracker = SkillTracker()
            tracker.record("__none__", "unmatched task", False)
            tracker.record("real_skill", "matched task", True)
            unmatched = tracker.get_unmatched_tasks(10)
            assert len(unmatched) == 1
            assert "unmatched" in unmatched[0]


class TestSkillTrackerClear:
    def test_clear_removes_all(self, tmp_path):
        with patch.object(SkillTracker, "DATA_DIR", str(tmp_path)):
            tracker = SkillTracker()
            tracker.record("s", "t", True)
            assert tracker.total_outcomes == 1
            tracker.clear()
            assert tracker.total_outcomes == 0
