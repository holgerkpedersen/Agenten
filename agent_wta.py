"""Winner-Takes-All tool learning + sequence learning for Agenten."""

import json
import os
import time
from collections import Counter
from typing import Any

# Tool dependency order: lower number = should run earlier
TOOL_ORDER = {
    # 1. Read/gather information first
    "read_issue": 10, "list_files": 10, "list_chunks": 10, "list_symbols": 10,
    "locate": 10, "read_location": 10, "read_chunk": 10,
    "analyze_own_logs": 10, "analyze_dependencies": 10, "suggest_module_groups": 10,
    # 2. Create new code
    "write_file": 20, "add_method": 20, "add_function": 20, "add_import": 20,
    # 3. Modify/delete existing code
    "edit_file": 30, "remove_symbol": 30, "delete_file": 30, "extract_symbol": 30,
    # 4. Verify
    "run_tests": 40, "verify_refactor": 40,
    # 5. Finalize
    "update_issue_status": 50, "done": 50,
    # 6. Git/PR operations (after everything)
    "git_add_all": 60, "git_commit": 60, "git_push": 60, "git_create_branch": 60,
    "git_checkout": 60, "github_create_pr": 60, "github_create_issue": 60,
    "git_status": 60, "git_diff": 60, "git_log": 60,
    # Default (unknown tools)
}
_DEFAULT_TOOL_ORDER = 50


_WTA_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".agent_storage", "tool_wta_scores.json"
)
_SEQUENCE_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".agent_storage", "tool_sequences.json"
)
SEQUENCE_MIN_SAMPLES = 3
SEQUENCE_MIN_CONFIDENCE = 0.6


class WTAState:
    """Tracks per-tool success rates per (template, phase) with Laplace smoothing.

    Used to rank tool calls by historical success rate within a given
    template + phase, so the most reliable tools execute first.
    """

    def __init__(self, path: str = "") -> None:
        self.path = path or _WTA_DEFAULT_PATH
        self.data: dict[str, dict[str, dict[str, dict[str, float | int]]]] = {}

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                self.data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def _tool_entry(self, template: str, phase: str, tool: str) -> dict:
        tpl = self.data.setdefault(template, {})
        ph = tpl.setdefault(phase, {})
        return ph.setdefault(tool, {"ok": 0, "fail": 0, "score": 0.5, "last": 0})

    def record(self, template: str, phase: str, tool: str, success: bool) -> None:
        entry = self._tool_entry(template, phase, tool)
        if success:
            entry["ok"] = entry["ok"] + 1  # type: ignore
        else:
            entry["fail"] = entry["fail"] + 1  # type: ignore
        ok = entry["ok"] or 0
        fail = entry["fail"] or 0
        entry["score"] = round((ok + 1) / (ok + fail + 2), 4)
        entry["last"] = time.time()

    def get_score(self, template: str, phase: str, tool: str) -> float:
        entry = self._tool_entry(template, phase, tool)
        return float(entry.get("score", 0.5))

    def rank_tool_calls(
        self,
        template: str,
        phase: str,
        tool_calls: list[dict],
        max_calls: int | None = None,
    ) -> list[dict]:
        """Score and reorder tool calls by dependency order, then historical success.

        Tools are first grouped by dependency order (read → create → modify →
        verify → finalize → git), then sorted by historical success rate within
        each group. Optionally cap the total number returned.

        Args:
            template: Template name (e.g. 'refactor', 'bugfix').
            phase: Phase name (e.g. 'Ekstraher', 'Implementering').
            tool_calls: List of tool-call dicts from the LLM.
            max_calls: If set, only the top *N* calls are returned.

        Returns:
            Reordered (and optionally truncated) list of tool-call dicts.
        """
        if not tool_calls:
            return []

        scored = []
        for tc in tool_calls:
            name = tc.get("function", {}).get("name", "?")
            order = TOOL_ORDER.get(name, _DEFAULT_TOOL_ORDER)
            score = self.get_score(template, phase, name)
            # Primary sort: dependency order. Secondary sort: success rate (descending)
            scored.append((order, -score, tc))

        scored.sort(key=lambda x: (x[0], x[1]))

        result = [tc for _, _, tc in scored]
        if max_calls is not None and max_calls > 0:
            result = result[:max_calls]

        # Log reorder if order changed
        original_order = [tc.get("function", {}).get("name", "?") for tc in tool_calls]
        new_order = [tc.get("function", {}).get("name", "?") for tc in result]
        if original_order != new_order:
            import logging
            logging.getLogger(__name__).info(
                "WTA dependency reorder: %s -> %s",
                ", ".join(original_order),
                ", ".join(new_order)
            )

        return result


class SequenceLearner:
    """Mines tool-call sequences from successful tasks per (template, phase).

    Generates human-readable guidance for prompt injection so the LLM
    learns effective tool strategies over time.
    """

    def __init__(self, path: str = "") -> None:
        self.path = path or _SEQUENCE_DEFAULT_PATH
        self.data: dict[str, dict[str, dict[str, Any]]] = {}

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                self.data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def _phase_entry(self, template: str, phase: str) -> dict:
        tpl = self.data.setdefault(template, {})
        return tpl.setdefault(phase, {
            "total": 0,
            "first_tool": {},
            "tool_pairs": {},
            "tool_presence": {},
        })

    def record_task(
        self,
        template: str,
        phase: str,
        tool_sequence: list[str],
        success: bool,
    ) -> None:
        if not success or not tool_sequence:
            return
        entry = self._phase_entry(template, phase)
        entry["total"] = entry["total"] + 1

        first = tool_sequence[0]
        cnt = entry["first_tool"].get(first, 0)
        entry["first_tool"][first] = cnt + 1

        for i in range(1, len(tool_sequence)):
            pair = f"{tool_sequence[i-1]}→{tool_sequence[i]}"
            cnt = entry["tool_pairs"].get(pair, 0)
            entry["tool_pairs"][pair] = cnt + 1

        for t in set(tool_sequence):
            cnt = entry["tool_presence"].get(t, 0)
            entry["tool_presence"][t] = cnt + 1

    def generate_guidance(self, template: str, phase: str, min_samples: int = 0) -> str:
        min_n = min_samples or SEQUENCE_MIN_SAMPLES
        entry = self._phase_entry(template, phase)
        total = entry.get("total", 0)
        if total < min_n:
            return ""

        first = entry.get("first_tool", {})
        pairs = entry.get("tool_pairs", {})
        presence = entry.get("tool_presence", {})

        lines: list[str] = []
        lines.append(f"Learned pattern ({template}/{phase} \u2014 {total} successful tasks):")

        if first:
            top_first = max(first, key=first.get)
            ratio = first[top_first] / total
            if ratio >= SEQUENCE_MIN_CONFIDENCE:
                lines.append(f"  First tool: {top_first} ({first[top_first]}/{total})")

        if pairs:
            top_pair = max(pairs, key=pairs.get)
            ratio = pairs[top_pair] / total
            if ratio >= SEQUENCE_MIN_CONFIDENCE:
                lines.append(f"  Common sequence: {top_pair} ({pairs[top_pair]}/{total})")

        if presence:
            high = [(t, c) for t, c in presence.items() if c / total >= SEQUENCE_MIN_CONFIDENCE]
            low = [(t, c) for t, c in presence.items() if c / total < 0.3]
            if high:
                lines.append("  Effective tools:")
                for t, c in sorted(high, key=lambda x: -x[1]):
                    lines.append(f"    {t} \u2014 used in {c}/{total} tasks")
            if low:
                skip = [t for t, _ in sorted(low, key=lambda x: x[1])]
                lines.append(f"  Rarely needed: {', '.join(skip[:3])}")

        return "\n".join(lines)

    def generate_tool_tip(self, template: str, phase: str, min_samples: int = 0) -> str:
        min_n = min_samples or SEQUENCE_MIN_SAMPLES
        entry = self._phase_entry(template, phase)
        total = entry.get("total", 0)
        if total < min_n:
            return ""

        presence = entry.get("tool_presence", {})
        first = entry.get("first_tool", {})

        tips: list[str] = []
        if first:
            top_first = max(first, key=first.get)
            if first[top_first] / total >= SEQUENCE_MIN_CONFIDENCE:
                tips.append(f"Try starting with {top_first}")

        low = [(t, c) for t, c in presence.items() if c / total < 0.3]
        if low:
            skip = min(low, key=lambda x: x[1])[0]
            tips.append(f"{skip} is rarely useful here")

        if not tips:
            return ""
        return "Tip: " + "; ".join(tips[:2]) + "."
