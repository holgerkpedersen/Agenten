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


def create_test_registry() -> ToolRegistry:
    """Create a minimal tool registry for testing."""
    reg = ToolRegistry()
    reg.register(Tool(
        "list_symbols",
        "List ALL top-level symbols in a Python file via AST",
        ["filepath"],
        lambda filepath: {"success": True, "symbols": [{"name": "hello", "type": "function"}], "count": 1},
    ))
    reg.register(Tool(
        "read_location",
        "Read a specific function/class via AST",
        ["filepath", "name"],
        lambda filepath, name: {"success": True, "name": name, "content": f"def {name}():\n    pass"},
    ))
    reg.register(Tool(
        "write_file",
        "Create a NEW file with content",
        ["path", "content"],
        lambda path, content: {"success": True, "path": path, "chars": len(content)},
    ))
    reg.register(Tool(
        "run_tests",
        "Run pytest and return results",
        [],
        lambda: {"success": True, "exit_code": 0, "summary": "52 passed in 0.18s"},
    ))
    reg.register(Tool(
        "done",
        "Signal that the task/phase is complete",
        ["result"],
        lambda result: result,
    ))
    return reg


def _call_llm_direct(model: str, base_url: str, prompt: str, tools: list) -> tuple[str, list]:
    """Call LLM via direct API (non-streaming) and return (response_text, tool_calls_list)."""
    import requests as _req
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 200,
        "stream": False,
    }
    if tools:
        body["tools"] = tools
    try:
        r = _req.post(f"{base_url}/v1/chat/completions", json=body, timeout=30)
        if r.status_code != 200:
            return f"[HTTP {r.status_code}]", []
        data = r.json()
        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        response = msg.get("content", "") or ""
        tc = msg.get("tool_calls", []) or []
        return response, tc
    except Exception as e:
        return f"[ERROR: {e}]", []


def test_native_calling(model: str, base_url: str) -> list[dict]:
    """Test native function calling."""
    results = []
    reg = create_test_registry()
    tools = reg.get_openai_tools_for_active()

    # Test 1: Simple tool call (list_symbols)
    prompt = "List all symbols in fil.py using list_symbols"
    try:
        test_response, tc = _call_llm_direct(model, base_url, prompt, tools)
        passed = len(tc) > 0 and tc[0].get("function", {}).get("name") == "list_symbols"
        results.append({
            "test": "list_symbols kald",
            "prompt": prompt,
            "passed": passed,
            "tool_calls": len(tc),
            "tool_name": tc[0].get("function", {}).get("name", "ingen") if tc else "ingen",
            "args": json.dumps(tc[0].get("function", {}).get("arguments", ""))[:100] if tc else "",
            "response": test_response[:80] if test_response else "(tom)",
        })
    except Exception as e:
        import traceback
        results.append({"test": "list_symbols kald", "prompt": prompt, "passed": False, "error": str(e)[:200], "traceback": traceback.format_exc()[-200:]})

    # Test 2: Tool with parameters (read_location)
    prompt = 'Brug read_location(filepath="test.py", name="hello") til at læse funktionen'
    try:
        resp, tc = _call_llm_direct(model, base_url, prompt, tools)
        passed = False
        if tc:
            fn = tc[0].get("function", {})
            try:
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    args = json.loads(args)
                passed = fn.get("name") == "read_location" and "filepath" in args and "name" in args
            except (json.JSONDecodeError, TypeError):
                pass
        results.append({
            "test": "read_location med args",
            "prompt": prompt,
            "passed": passed,
            "tool_calls": len(tc),
            "tool_name": tc[0].get("function", {}).get("name", "ingen") if tc else "ingen",
            "response": resp[:80] if resp else "(tom)",
        })
    except Exception as e:
        results.append({"test": "read_location med args", "prompt": prompt, "passed": False, "error": str(e)[:200]})

    # Test 3: write_file med optional parameter
    prompt = 'Skriv en fil test.py med print("hello")'
    try:
        resp, tc = _call_llm_direct(model, base_url, prompt, tools)
        passed = False
        if tc:
            fn = tc[0].get("function", {})
            try:
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    args = json.loads(args)
                passed = fn.get("name") == "write_file" and "path" in args and "content" in args
            except (json.JSONDecodeError, TypeError):
                pass
        results.append({
            "test": "write_file med args",
            "prompt": prompt,
            "passed": passed,
            "tool_calls": len(tc),
            "tool_name": tc[0].get("function", {}).get("name", "ingen") if tc else "ingen",
            "response": resp[:80] if resp else "(tom)",
        })
    except Exception as e:
        results.append({"test": "write_file med args", "prompt": prompt, "passed": False, "error": str(e)[:200]})

    # Test 4: Multiple tool calls in one response
    prompt = "List symbols in fil.py AND read the function 'hello' — gør begge i samme svar"
    try:
        resp, tc = _call_llm_direct(model, base_url, prompt, tools)
        passed = len(tc) >= 2
        results.append({
            "test": "Flere tool-kald i samme svar",
            "prompt": prompt,
            "passed": passed,
            "tool_calls": len(tc),
            "names": [t.get("function", {}).get("name", "?") for t in tc] if tc else [],
            "response": resp[:80] if resp else "(tom)",
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
