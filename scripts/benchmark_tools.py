"""Benchmark: Test hvordan en vilkårlig LLM arbejder med tools.

Kørsel:
    python scripts/benchmark_tools.py --model minimax-m2.5@q2_k
    python scripts/benchmark_tools.py --model nex-n2-mini --native
    python scripts/benchmark_tools.py --list

Test cases dækker native function calling og tekst-baserede tool-markører.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm_wrapper import LMStudioWrapper
from tools import Tool, ToolRegistry
from i18n import K
from lang import t

PASS = "✓"
FAIL = "✗"


def create_test_registry(lang: str = "da") -> ToolRegistry:
    """Create a minimal tool registry for testing."""
    reg = ToolRegistry(lang)
    reg.register(Tool(
        "list_symbols",
        "List ALL top-level symbols in a Python file via AST",
        ["filepath"],
        lambda filepath="": {"success": True, "symbols": [{"name": "hello", "type": "function"}], "count": 1},
    ))
    reg.register(Tool(
        "read_location",
        "Read a specific function/class via AST",
        ["filepath", "name"],
        lambda filepath, name, line_no=None: {"success": True, "name": name, "content": f"def {name}():\n    pass"},
        optional_params=["line_no"],
    ))
    reg.register(Tool(
        "write_file",
        "Create a NEW file with content",
        ["path", "content"],
        lambda path, content, overwrite=False: {"success": True, "path": path, "chars": len(content)},
        optional_params=["overwrite"],
    ))
    reg.register(Tool(
        "run_tests",
        "Run pytest and return results",
        [],
        lambda test_path="": {"success": True, "exit_code": 0, "summary": "52 passed in 0.18s"},
        optional_params=["test_path"],
    ))
    reg.register(Tool(
        "done",
        "Signal that the task/phase is complete",
        ["result"],
        lambda result="": result,
        optional_params=["result"],
    ))
    return reg


def test_native_calling(model: str, base_url: str) -> list[dict]:
    """Test native function calling."""
    results = []
    llm = LMStudioWrapper(model=model, base_url=base_url)
    reg = create_test_registry()
    tools = reg.get_openai_tools_for_active()

    # Test 1: Simple tool call (list_symbols)
    prompt = "List all symbols in fil.py using list_symbols"
    try:
        response = llm.generate(prompt, tools=tools)
        tc = getattr(llm, '_pending_tool_calls', [])
        passed = len(tc) > 0 and tc[0]["function"]["name"] == "list_symbols"
        results.append({
            "test": "list_symbols kald",
            "prompt": prompt,
            "passed": passed,
            "response": response[:200] if response else "(ingen output)",
            "tool_calls": len(tc),
            "tool_name": tc[0]["function"]["name"] if tc else "ingen",
        })
    except Exception as e:
        results.append({"test": "list_symbols kald", "prompt": prompt, "passed": False, "error": str(e)[:200]})

    # Test 2: Tool with parameters (read_location)
    prompt = 'Brug read_location(filepath="test.py", name="hello") til at læse funktionen'
    try:
        response = llm.generate(prompt, tools=tools)
        tc = getattr(llm, '_pending_tool_calls', [])
        passed = False
        if tc:
            fn = tc[0]["function"]
            args = json.loads(fn.get("arguments", "{}"))
            passed = fn["name"] == "read_location" and "filepath" in args and "name" in args
        results.append({
            "test": "read_location med args",
            "prompt": prompt,
            "passed": passed,
            "response": response[:200] if response else "(ingen output)",
            "tool_calls": len(tc),
            "args": json.dumps(args) if tc else "ingen",
        })
    except Exception as e:
        results.append({"test": "read_location med args", "prompt": prompt, "passed": False, "error": str(e)[:200]})

    # Test 3: write_file med optional parameter
    prompt = 'Skriv en fil test.py med print("hello") — brug overwrite=true'
    try:
        response = llm.generate(prompt, tools=tools)
        tc = getattr(llm, '_pending_tool_calls', [])
        passed = False
        if tc:
            fn = tc[0]["function"]
            args = json.loads(fn.get("arguments", "{}"))
            passed = fn["name"] == "write_file" and "path" in args and "content" in args
        results.append({
            "test": "write_file med overwrite",
            "prompt": prompt,
            "passed": passed,
            "response": response[:200] if response else "(ingen output)",
            "tool_calls": len(tc),
            "args": json.dumps(args) if tc else "ingen",
        })
    except Exception as e:
        results.append({"test": "write_file med overwrite", "prompt": prompt, "passed": False, "error": str(e)[:200]})

    # Test 4: Multiple tool calls in one response
    prompt = "List symbols in fil.py AND read the function 'hello' — gør begge i samme svar"
    try:
        response = llm.generate(prompt, tools=tools)
        tc = getattr(llm, '_pending_tool_calls', [])
        passed = len(tc) >= 2
        results.append({
            "test": "Flere tool-kald i samme svar",
            "prompt": prompt,
            "passed": passed,
            "tool_calls": len(tc),
            "names": [t["function"]["name"] for t in tc] if tc else [],
        })
    except Exception as e:
        results.append({"test": "Flere tool-kald i samme svar", "prompt": prompt, "passed": False, "error": str(e)[:200]})

    return results


def test_text_tools(model: str, base_url: str) -> list[dict]:
    """Test text-mode tool markers (<<<TOOL>>>)."""
    results = []
    llm = LMStudioWrapper(model=model, base_url=base_url)
    reg = create_test_registry()
    tools_desc = reg.get_tool_descriptions()
    TOOL_MARKER = reg.TOOL_MARKER
    DONE_MARKER = reg.DONE_MARKER

    prompt_text = f"""Du har adgang til værktøjer. Brug dem når det er nødvendigt.

{tools_desc}

## Opgave
List alle symboler i fil.py.

Brug {TOOL_MARKER} til at kalde værktøjer og {DONE_MARKER} til at afslutte."""

    try:
        response = llm.generate(prompt_text)
        passed = TOOL_MARKER in response and "list_symbols" in response
        results.append({
            "test": "Tekst-mode: list_symbols",
            "prompt": prompt_text[:80],
            "passed": passed,
            "response": response[:300],
        })
    except Exception as e:
        results.append({"test": "Tekst-mode: list_symbols", "passed": False, "error": str(e)[:200]})

    return results


def print_report(results: list[dict], title: str):
    """Print formatted test report."""
    print(f"\n  {title}")
    print(f"  {'='*50}")
    passed = sum(1 for r in results if r.get("passed"))
    total = len(results)
    for r in results:
        icon = PASS if r.get("passed") else FAIL
        detail = ""
        if r.get("tool_calls"):
            detail += f" tools={r['tool_calls']}"
        if r.get("tool_name"):
            detail += f" navn={r['tool_name']}"
        if r.get("args"):
            detail += f" args={r['args'][:60]}"
        if r.get("error"):
            detail += f" fejl={r['error'][:60]}"
        print(f"  {icon} {r['test']}{detail}")
        if not r.get("passed") and r.get("response"):
            print(f"     Svar: {r['response'][:100]}")
    print(f"  {'-'*50}")
    print(f"  Resultat: {passed}/{total} bestået ({(passed/total*100):.0f}%)")
    print()


def main():
    parser = argparse.ArgumentParser(description="Test LLM tool calling ability")
    parser.add_argument("--model", default="", help="Model name (default: auto-detect from LM Studio)")
    parser.add_argument("--base-url", default="http://localhost:1234", help="LM Studio base URL")
    parser.add_argument("--native", action="store_true", help="Only test native function calling")
    parser.add_argument("--text", action="store_true", help="Only test text-mode tools")
    parser.add_argument("--list", action="store_true", help="List available models and exit")
    args = parser.parse_args()

    if args.list:
        try:
            import requests
            r = requests.get(f"{args.base_url}/v1/models", timeout=5)
            models = r.json().get("data", [])
            print(f"\nTilgængelige modeller på {args.base_url}:")
            for m in models:
                print(f"  - {m.get('id', m.get('model', '?'))}")
        except Exception as e:
            print(f"Kunne ikke hente model-liste: {e}")
        return

    model = args.model
    if not model:
        # Auto-detect first available model
        try:
            import requests
            r = requests.get(f"{args.base_url}/v1/models", timeout=5)
            models = r.json().get("data", [])
            if models:
                model = models[0].get("id", models[0].get("model", ""))
                print(f"\nAuto-detected model: {model}")
        except Exception as e:
            print(f"Auto-detect fejlede: {e}. Brug --model for at angive model.")
            return

    print(f"\n{'='*60}")
    print(f"  TOOL BENCHMARK — {model}")
    print(f"  {args.base_url}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    if not args.text:
        results = test_native_calling(model, args.base_url)
        print_report(results, "NATIVE FUNCTION CALLING")

    if not args.native:
        results = test_text_tools(model, args.base_url)
        print_report(results, "TEKST-BASEREDE TOOLS")


if __name__ == "__main__":
    main()
