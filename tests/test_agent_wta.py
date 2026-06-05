"""Tests for WTAState and SequenceLearner in agent_wta.py."""

import json
import os
import tempfile
from collections import Counter

from agent_wta import WTAState, SequenceLearner, EXPLORATION_RATE


# ============ WTAState Tests ============


def test_laplace_score_new_tool():
    """Unknown/untracked tools should score 0.5."""
    wta = WTAState()
    assert wta.get_score("kodeanalyse", "Purpose", "read_location") == 0.5


def test_laplace_score_after_successes():
    """9 successes out of 10 → score ≈ 0.83 (laplace: (9+1)/(10+2) = 10/12 = 0.8333)."""
    wta = WTAState()
    for _ in range(9):
        wta.record("kodeanalyse", "Purpose", "read_location", True)
    wta.record("kodeanalyse", "Purpose", "read_location", False)
    score = wta.get_score("kodeanalyse", "Purpose", "read_location")
    assert round(score, 4) == 0.8333, f"Expected 0.8333, got {score}"


def test_laplace_score_poor_tool():
    """2 successes out of 10 → score ≈ 0.25 (laplace: (2+1)/(10+2) = 3/12 = 0.25)."""
    wta = WTAState()
    for _ in range(2):
        wta.record("bugfix", "Implementering", "list_files", True)
    for _ in range(8):
        wta.record("bugfix", "Implementering", "list_files", False)
    score = wta.get_score("bugfix", "Implementering", "list_files")
    assert round(score, 4) == 0.25, f"Expected 0.25, got {score}"


def test_select_winner_highest_score():
    """Among 3 candidates with scores [0.9, 0.6, 0.3], winner is the 0.9."""
    wta = WTAState()
    wta.record("t1", "p1", "tool_a", True)  # 1/1 → (1+1)/(1+2) = 0.6667
    wta.record("t1", "p1", "tool_a", True)  # 2/2 → (2+1)/(2+2) = 0.75
    wta.record("t1", "p1", "tool_b", True)  # 1/1 → 0.6667
    wta.record("t1", "p1", "tool_c", False)  # 0/1 → (0+1)/(1+2) = 0.3333

    candidates = [
        {"function": {"name": "tool_a", "arguments": {}}},
        {"function": {"name": "tool_b", "arguments": {}}},
        {"function": {"name": "tool_c", "arguments": {}}},
    ]
    winner = wta.select_winner("t1", "p1", candidates)
    assert winner is not None
    assert winner["function"]["name"] == "tool_a"


def test_select_winner_bypass_write_file():
    """write_file should always win regardless of scores."""
    wta = WTAState()
    wta.record("t1", "p1", "read_location", True)  # high score
    wta.record("t1", "p1", "read_location", True)
    wta.record("t1", "p1", "read_location", True)  # 3/3 → (3+1)/(3+2) = 0.8
    # write_file has no records → score 0.5

    candidates = [
        {"function": {"name": "read_location", "arguments": {}}},
        {"function": {"name": "write_file", "arguments": {"filepath": "test.py", "content": "x"}}},
    ]
    winner = wta.select_winner("t1", "p1", candidates)
    assert winner is not None
    assert winner["function"]["name"] == "write_file"


def test_select_winner_edit_file_bypass():
    """edit_file should also bypass WTA scoring."""
    wta = WTAState()
    wta.record("t1", "p1", "list_symbols", True)
    wta.record("t1", "p1", "list_symbols", True)
    wta.record("t1", "p1", "list_symbols", True)

    candidates = [
        {"function": {"name": "list_symbols", "arguments": {}}},
        {"function": {"name": "edit_file", "arguments": {"filepath": "test.py"}}},
    ]
    winner = wta.select_winner("t1", "p1", candidates)
    assert winner["function"]["name"] == "edit_file"


def test_select_winner_dedup_skip():
    """A candidate whose key is already in called_tools should be skipped."""
    wta = WTAState()
    wta.record("t1", "p1", "list_files", True)
    wta.record("t1", "p1", "list_files", True)
    wta.record("t1", "p1", "list_symbols", True)

    candidates = [
        {"function": {"name": "list_files", "arguments": {}}},
        {"function": {"name": "list_symbols", "arguments": {}}},
    ]
    called_tools = {"list_files{}": 1}  # list_files already called
    winner = wta.select_winner("t1", "p1", candidates, called_tools)
    assert winner is not None
    assert winner["function"]["name"] == "list_symbols"


def test_select_winner_all_dedup():
    """If all candidates are dedup, return the first one (fallback)."""
    wta = WTAState()
    candidates = [
        {"function": {"name": "locate", "arguments": {}}},
    ]
    called_tools = {"locate{}": 2}
    winner = wta.select_winner("t1", "p1", candidates, called_tools)
    assert winner is not None
    assert winner["function"]["name"] == "locate"


def test_select_winner_empty():
    """Empty candidates → None."""
    wta = WTAState()
    assert wta.select_winner("t1", "p1", []) is None


def test_persist_roundtrip():
    """Save WTAState to temp file, load it back, verify scores match."""
    wta = WTAState()
    wta.record("kodeanalyse", "Code Quality", "list_symbols", True)
    wta.record("kodeanalyse", "Code Quality", "list_symbols", True)
    wta.record("kodeanalyse", "Code Quality", "list_files", False)
    score_before = wta.get_score("kodeanalyse", "Code Quality", "list_symbols")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        tmp_path = f.name
        wta.path = tmp_path
        wta.save()

    wta2 = WTAState(path=tmp_path)
    wta2.load()
    score_after = wta2.get_score("kodeanalyse", "Code Quality", "list_symbols")
    assert score_before == score_after
    os.unlink(tmp_path)


def test_multiple_templates_independent():
    """Scores for different templates should not mix."""
    wta = WTAState()
    wta.record("kodeanalyse", "Purpose", "read_location", True)  # 1/1 → 0.6667
    wta.record("bugfix", "Implementering", "read_location", False)  # 0/1 → 0.3333
    score_ka = wta.get_score("kodeanalyse", "Purpose", "read_location")
    score_bf = wta.get_score("bugfix", "Implementering", "read_location")
    assert score_ka != score_bf
    assert score_ka > 0.5
    assert score_bf < 0.5


def test_tool_args_dedup_key():
    """Tool with same name but different args should be treated separately."""
    wta = WTAState()
    candidates = [
        {"function": {"name": "locate", "arguments": {"name": "foo"}}},
        {"function": {"name": "locate", "arguments": {"name": "bar"}}},
    ]
    called_tools = {"locate{'name': 'foo'}": 1}
    winner = wta.select_winner("t1", "p1", candidates, called_tools)
    assert winner is not None
    assert winner["function"]["arguments"]["name"] == "bar"


# ============ SequenceLearner Tests ============


def test_sequence_record_first_tool():
    """First tool should be tracked correctly."""
    sl = SequenceLearner()
    sl.record_task("kodeanalyse", "Purpose", ["list_symbols", "locate", "locate"], True)
    entry = sl._phase_entry("kodeanalyse", "Purpose")
    assert entry["total"] == 1
    assert entry["first_tool"].get("list_symbols") == 1


def test_sequence_record_pairs():
    """Tool pairs should be tracked as bigrams."""
    sl = SequenceLearner()
    sl.record_task("refactor", "Ekstraher", ["write_file", "extract_symbol", "edit_file"], True)
    entry = sl._phase_entry("refactor", "Ekstraher")
    assert entry["tool_pairs"].get("write_file→extract_symbol") == 1
    assert entry["tool_pairs"].get("extract_symbol→edit_file") == 1


def test_sequence_record_presence():
    """Unique tool presence should be tracked."""
    sl = SequenceLearner()
    sl.record_task("kodeanalyse", "Code Quality", ["list_symbols", "locate", "locate", "locate"], True)
    entry = sl._phase_entry("kodeanalyse", "Code Quality")
    assert entry["tool_presence"]["list_symbols"] == 1
    assert entry["tool_presence"]["locate"] == 1
    # locate called 3 times but counted once (set)
    assert entry["tool_presence"]["locate"] == 1


def test_sequence_record_failure_skipped():
    """Failed tasks should not affect sequence data."""
    sl = SequenceLearner()
    sl.record_task("kodeanalyse", "Purpose", ["list_files"], False)
    entry = sl._phase_entry("kodeanalyse", "Purpose")
    assert entry["total"] == 0


def test_sequence_record_empty_skipped():
    """Empty tool_sequence should not affect sequence data."""
    sl = SequenceLearner()
    sl.record_task("kodeanalyse", "Purpose", [], True)
    entry = sl._phase_entry("kodeanalyse", "Purpose")
    assert entry["total"] == 0


def test_sequence_guidance_min_samples():
    """generate_guidance should return empty string when below min_samples."""
    sl = SequenceLearner()
    sl.record_task("kodeanalyse", "Purpose", ["list_symbols"], True)
    guidance = sl.generate_guidance("kodeanalyse", "Purpose")
    assert guidance == ""


def test_sequence_guidance_contains_pattern():
    """With enough samples, guidance should include the learned pattern."""
    sl = SequenceLearner()
    for _ in range(4):
        sl.record_task("kodeanalyse", "Purpose", ["list_symbols", "locate"], True)
    guidance = sl.generate_guidance("kodeanalyse", "Purpose")
    assert "list_symbols" in guidance
    assert "kodeanalyse/Purpose" in guidance
    assert "4 successful tasks" in guidance


def test_sequence_guidance_confidence_threshold():
    """Tool below confidence threshold should not appear in guidance."""
    sl = SequenceLearner()
    for _ in range(3):
        sl.record_task("kodeanalyse", "Purpose", ["list_symbols", "locate"], True)
    sl.record_task("kodeanalyse", "Purpose", ["list_files", "locate"], True)
    # list_files appears 1/4 = 0.25 which is below 0.6 threshold
    guidance = sl.generate_guidance("kodeanalyse", "Purpose")
    # list_files might appear in "rarely needed" section, but not as "effective"


def test_sequence_guidance_custom_min_samples():
    """Custom min_samples should be respected."""
    sl = SequenceLearner()
    for _ in range(5):
        sl.record_task("bugfix", "Test", ["run_tests"], True)
    guidance_high = sl.generate_guidance("bugfix", "Test", min_samples=10)
    assert guidance_high == ""
    guidance_low = sl.generate_guidance("bugfix", "Test", min_samples=3)
    assert "run_tests" in guidance_low


def test_sequence_tool_tip():
    """generate_tool_tip should return a short tip when enough data."""
    sl = SequenceLearner()
    for _ in range(4):
        sl.record_task("kodeanalyse", "Code Quality", ["list_symbols", "locate"], True)
    tip = sl.generate_tool_tip("kodeanalyse", "Code Quality")
    assert isinstance(tip, str)
    assert len(tip) > 0


def test_sequence_tool_tip_no_data():
    """generate_tool_tip should return '' when no data."""
    sl = SequenceLearner()
    assert sl.generate_tool_tip("kodeanalyse", "Quality") == ""


def test_sequence_persist_roundtrip():
    """Save and load SequenceLearner data."""
    sl = SequenceLearner()
    for _ in range(4):
        sl.record_task("kodeanalyse", "Purpose", ["list_symbols", "locate"], True)
    guidance_before = sl.generate_guidance("kodeanalyse", "Purpose")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        tmp_path = f.name
        sl.path = tmp_path
        sl.save()

    sl2 = SequenceLearner(path=tmp_path)
    sl2.load()
    guidance_after = sl2.generate_guidance("kodeanalyse", "Purpose")
    assert guidance_before == guidance_after
    os.unlink(tmp_path)


def test_sequence_multiple_templates():
    """Different templates should have independent sequence data."""
    sl = SequenceLearner()
    for _ in range(4):
        sl.record_task("kodeanalyse", "Purpose", ["list_symbols"], True)
    for _ in range(4):
        sl.record_task("refactor", "Ekstraher", ["write_file"], True)

    g_ka = sl.generate_guidance("kodeanalyse", "Purpose")
    g_re = sl.generate_guidance("refactor", "Ekstraher")
    assert "list_symbols" in g_ka
    assert "write_file" in g_re
    assert "list_symbols" not in g_re
