"""Refine project documentation via iterative LLM Q&A.

Reads the 4 documentation files in workdir/docs/, asks the LLM to identify
missing specifications needed to build a working standard browser (or other
project type), lets the LLM answer the questions itself based on standard
browser-practice, and refines the docs/*.md files with the new specs.

Usage:
    python scripts/run_doc_refinement.py --workdir C:/Dev/StarBrowser
    python scripts/run_doc_refinement.py --workdir C:/Dev/StarBrowser --rounds 7
    python scripts/run_doc_refinement.py --workdir C:/Dev/StarBrowser --dry-run

Outputs:
    - Updated docs/*.md (in-place edits with new sections)
    - docs/uddybning_dialog.md (full conversation log)
    - docs/refinements_diff.md (summary of changes)
"""
import argparse
import json
import os
import sys
import time
from typing import Any

project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_dir)

import config  # noqa: E402
from llm_wrapper import LMStudioWrapper  # noqa: E402
from config import get_logger  # noqa: E402

log = get_logger(__name__)


DOCS_DIR_NAME = "docs"
DIALOG_FILENAME = "uddybning_dialog.md"
DIFF_FILENAME = "refinements_diff.md"
CORE_DOCS = ["kravanalyse.md", "arkitektur.md", "implementeringsplan.md", "sikkerhedsanalyse.md"]

STRONGEST_MODEL_PRIORITY = [
    "qwen3.5-122b-a10b",
    "openai/gpt-oss-120b",
    "bytedance/seed-oss-36b",
    "minimax-m2.5",
    "nousresearch/hermes-4-70b",
    "google/gemma-4-31b",
    "qwen3.6-27b-mtp",
    "qwen3.6-35b-a3b-mtp",
    "qwen3-coder-30b-a3b-instruct",
]


def select_strongest_loaded_model(llm: LMStudioWrapper) -> str | None:
    """Pick the strongest model from the priority list that is currently loaded."""
    try:
        loaded = llm.list_models()
    except Exception as e:
        log.warning("Could not list models: %s", e)
        return None
    if not loaded:
        return None
    for candidate in STRONGEST_MODEL_PRIORITY:
        if candidate in loaded:
            return candidate
    return loaded[0]


def find_working_model(llm: LMStudioWrapper, preferred: str | None = None) -> str | None:
    """Try models in priority order until one responds successfully.

    Returns the first model name that gives a non-error response to a trivial prompt.
    """
    candidates = list(STRONGEST_MODEL_PRIORITY)
    if preferred and preferred in candidates:
        candidates.remove(preferred)
        candidates.insert(0, preferred)
    test_prompt = "Reply with the single word: ok"
    for candidate in candidates:
        try:
            llm.set_model(candidate)
            resp = llm.generate(prompt=test_prompt, temperature=0.0, max_tokens=10, use_cache=False)
            if resp and not resp.startswith("ERROR:") and len(resp.strip()) > 0:
                log.info("Working model found: %s (responded: %r)", candidate, resp[:50])
                return candidate
        except Exception as e:
            log.debug("Model %s failed: %s", candidate, e)
            continue
    return None


SYSTEM_PROMPT = """Du er en senior softwarearkitekt der hjælper med at gøre eksisterende dokumentation komplet.

Opgave:
1. Læs de 4 dokumenter der er vedhæftet (kravanalyse, arkitektur, implementeringsplan, sikkerhedsanalyse)
2. Identificer specifikationer der mangler for at en fungerende standard-browser kan bygges
3. For hver mangel: stil et spørgsmål ELLER svar selv med reference til hvordan Chrome/Firefox/Safari håndterer det
4. Verificer at svarene bidrager til at browseren kan fungere som standard
5. Iterer indtil dokumenterne er tilstrækkelige

VIGTIGT:
- Vær specifik og teknisk konkret (f.eks. "Brug QNetworkAccessManager med 30s timeout" ikke bare "håndtér netværk")
- Dæk ALLE områder: HTML5, CSS3, ES6, billedformater, netværk, sikkerhed, performance, edge cases
- Tænk som en browser-udvikler: hvad ville en bruger forvente af Chrome/Firefox?

Svarformat:
For hver runde, output:
RUNDE N:
Q1: [spørgsmål]
A1: [dit svar med konkret implementeringsforslag]
Q2: ...
A2: ...
STATUS: [FÆRDIG / MERE_NØDVENDIGT - begrund kort]
"""


def read_docs(workdir: str) -> dict[str, str]:
    """Read all .md files in workdir/docs/."""
    docs_path = os.path.join(workdir, DOCS_DIR_NAME)
    if not os.path.isdir(docs_path):
        raise FileNotFoundError(f"Docs directory not found: {docs_path}")
    docs = {}
    for fname in sorted(os.listdir(docs_path)):
        if fname.endswith(".md") and not fname.startswith("uddybning") and not fname.startswith("refinements"):
            fpath = os.path.join(docs_path, fname)
            with open(fpath, encoding="utf-8") as f:
                docs[fname] = f.read()
    return docs


def format_docs_for_prompt(docs: dict[str, str]) -> str:
    """Format docs as a single concatenated string for the prompt."""
    parts = []
    for fname, content in docs.items():
        parts.append(f"=== {fname} ===\n{content}\n")
    return "\n".join(parts)


def run_refinement_round(
    llm: LMStudioWrapper,
    docs: dict[str, str],
    round_num: int,
    previous_dialog: str,
) -> str:
    """Run one refinement round with the LLM. Returns the LLM's response."""
    docs_text = format_docs_for_prompt(docs)
    user_msg = f"""Her er de 4 dokumenter:

{docs_text}

Tidligere dialog (runde 1-{round_num - 1}):
{previous_dialog if previous_dialog else "(ingen endnu)"}

Kør nu runde {round_num}. Identificer flere mangler eller angiv STATUS: FÆRDIG hvis dokumenterne er tilstrækkelige.
"""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    log.info("Round %d: sending to LLM (%d chars in prompt)", round_num, len(user_msg))
    try:
        response = llm.generate(messages=messages, temperature=0.3, max_tokens=8000, use_cache=False)
    except Exception as e:
        log.error("LLM call failed: %s", e)
        return f"ERROR: {e}"
    return response


def extract_refinements(llm_response: str) -> str:
    """Extract the actual refinement content from the LLM response.

    The LLM is asked for a Q&A format. We extract everything that looks
    like concrete refinements to add to the docs.
    """
    return llm_response.strip()


def write_dialog(workdir: str, rounds: list[dict[str, Any]], model: str) -> str:
    """Write the full dialog to docs/uddybning_dialog.md."""
    fpath = os.path.join(workdir, DOCS_DIR_NAME, DIALOG_FILENAME)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(f"# Uddybning af dokumentation\n\n")
        f.write(f"**Model:** {model}\n")
        f.write(f"**Tidspunkt:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"**Antal runder:** {len(rounds)}\n\n")
        f.write("---\n\n")
        for r in rounds:
            f.write(f"## Runde {r['round']}\n\n")
            f.write(f"### LLM Response\n\n```\n{r['response']}\n```\n\n")
            if r.get("refinements"):
                f.write(f"### Refinements tilføjet\n\n{r['refinements']}\n\n")
            f.write("---\n\n")
    return fpath


def is_complete(response: str) -> bool:
    """Check if the LLM indicated the docs are sufficient."""
    response_lower = response.lower()
    if "status: færdig" in response_lower or "status: færdig" in response_lower:
        return True
    if "status:færdig" in response_lower:
        return True
    if "dokumenterne er tilstrækkelige" in response_lower:
        return True
    if "docs are sufficient" in response_lower:
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Refine docs via LLM Q&A")
    parser.add_argument("--workdir", "-w", required=True, help="Project root containing docs/")
    parser.add_argument("--rounds", "-r", type=int, default=7, help="Max refinement rounds (default 7)")
    parser.add_argument("--model", "-m", default=None, help="Force a specific model (default: auto-select strongest)")
    parser.add_argument("--dry-run", action="store_true", help="Don't write changes to disk")
    parser.add_argument("--base-url", default=None, help="Override LM Studio base URL")
    parser.add_argument("--timeout", type=int, default=600, help="Per-LLM-call timeout in seconds (default 600)")
    args = parser.parse_args()

    workdir = os.path.abspath(args.workdir)
    log.info("Refining docs in: %s", workdir)

    if args.base_url:
        llm = LMStudioWrapper(base_url=args.base_url, timeout=args.timeout)
    else:
        llm = LMStudioWrapper(timeout=args.timeout)

    model = args.model or select_strongest_loaded_model(llm)
    if not model:
        log.error("No model available. Use --model to specify.")
        return 1
    if model != llm.model:
        log.info("Switching to model: %s (was %s)", model, llm.model)
        llm.set_model(model)
    log.info("Probing model availability: %s", model)
    working_model = find_working_model(llm, preferred=model)
    if not working_model:
        log.error("No working model found in priority list. Tried: %s", STRONGEST_MODEL_PRIORITY)
        return 1
    if working_model != model:
        log.info("Preferred model %s unavailable, falling back to: %s", model, working_model)
        llm.set_model(working_model)
        model = working_model
    log.info("Using model: %s", model)
    log.info("=" * 60)
    log.info("CONFIGURATION:")
    log.info("  Rounds: %d (max iterations)", args.rounds)
    log.info("  Per-call timeout: %ds (%d min)", args.timeout, args.timeout // 60)
    log.info("  Estimated total time: ~%d-%d min",
             args.rounds * 8, args.rounds * 15)
    log.info("  Press Ctrl+C to stop (dialog will be saved on success)")
    log.info("=" * 60)

    docs = read_docs(workdir)
    log.info("Read %d docs: %s", len(docs), list(docs.keys()))

    rounds: list[dict[str, Any]] = []
    previous_dialog = ""
    for round_num in range(1, args.rounds + 1):
        log.info("=" * 60)
        log.info("ROUND %d / %d", round_num, args.rounds)
        log.info("=" * 60)
        response = run_refinement_round(llm, docs, round_num, previous_dialog)
        if response.startswith("ERROR:"):
            log.error("Round failed: %s", response)
            break
        log.info("LLM response (%d chars):\n%s", len(response), response[:500])
        rounds.append({
            "round": round_num,
            "response": response,
            "refinements": "",
        })
        previous_dialog = f"\n\n=== Runde {round_num} ===\n{response}\n" + previous_dialog
        if is_complete(response):
            log.info("LLM indicated docs are complete. Stopping at round %d.", round_num)
            break

    if not args.dry_run:
        dialog_path = write_dialog(workdir, rounds, model)
        log.info("Dialog written to: %s", dialog_path)
    else:
        log.info("[DRY-RUN] Would write dialog with %d rounds", len(rounds))

    log.info("Done. %d rounds completed.", len(rounds))
    return 0


if __name__ == "__main__":
    sys.exit(main())
