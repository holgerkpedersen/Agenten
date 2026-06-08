"""Winner-Takes-All tool learning + sequence learning for Agenten."""

import json
import os
import time
from collections import Counter
from typing import Any


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
        """Score and reorder tool calls by historical success rate.

        Tools with higher success rates come first; low-success tools sink
        to the end of the list. Optionally cap the total number returned.

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
            score = self.get_score(template, phase, name)
            scored.append((score, tc))

        scored.sort(key=lambda x: -x[0])

        result = [tc for _, tc in scored]
        if max_calls is not None and max_calls > 0:
            result = result[:max_calls]
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
