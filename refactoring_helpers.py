"""Hjælpefunktioner til refactoring og retry-logik."""
from typing import Any
from utility_routes import status


def _count_source_symbols(source_file: str = 'api_server.py') -> int:
    """Count top-level symbols in a Python source file.
    
    Args:
        source_file: Sti til Python-filen der skal analyseres.
        
    Returns:
        Antal top-level symboler i filen.
    """
    import ast
    try:
        with open(source_file, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read())
        return sum(1 for node in ast.iter_child_nodes(tree) 
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign)))
    except (OSError, SyntaxError):
        return 0


def _extract_batch_results(agent: Any) -> list:
    """Extract successful batch_extract_symbols results from tool_log.
    
    Args:
        agent: Agent-objekt med tool_log.
        
    Returns:
        Liste af succesfulde batch_extract_symbols resultater.
    """
    if not hasattr(agent, 'tool_log'):
        return []
    results = []
    for entry in agent.tool_log:
        if isinstance(entry, dict) and entry.get('name') == 'batch_extract_symbols':
            if entry.get('success'):
                results.append(entry)
    return results


def _extract_retry_context(node: Any, agent: Any, full_response: str, symbols_before: int = -1, symbols_after: int = -1) -> dict:
    """Extract failure context for retry.
    
    Args:
        node: Task-node.
        agent: Agent-objekt.
        full_response: Fulde svar fra LLM.
        symbols_before: Antal symboler før operationen.
        symbols_after: Antal symboler efter operationen.
        
    Returns:
        Dictionary med kontekst for retry.
    """
    moved = max(0, symbols_before - symbols_after) if symbols_before >= 0 and symbols_after >= 0 else 0
    return {
        'node': node,
        'full_response': full_response,
        'symbols_before': symbols_before,
        'symbols_after': symbols_after,
        'symbols_moved': moved,
        'tool_log_tail': getattr(agent, 'tool_log', [])[-5:] if hasattr(agent, 'tool_log') else []
    }


def _build_retry_lessons(context: dict, agent: Any, all_contexts: list = None) -> str:
    """Build a 'Lessons Learned' prompt section from a failed attempt.
    
    Args:
        context: Kontekst-dict fra _extract_retry_context.
        agent: Agent-objekt.
        all_contexts: Liste af tidligere kontekster.
        
    Returns:
        Streng med lessons learned prompt-sektion.
    """
    if not context:
        return ""
    
    lessons = ["## Lessons Learned fra tidligere forsøg:\n"]
    
    # Tilføj info om symbol-ændringer
    before = context.get('symbols_before', -1)
    after = context.get('symbols_after', -1)
    if before > 0 and after > 0:
        lessons.append(f"- Symboler før: {before}, efter: {after} (ændring: {before - after})")
    
    # Tilføj info fra tool_log
    tool_log_tail = context.get('tool_log_tail', [])
    for entry in tool_log_tail:
        if isinstance(entry, dict):
            name = entry.get('name', 'unknown')
            success = entry.get('success', False)
            error = entry.get('error', '')
            status = "✅" if success else "❌"
            lessons.append(f"- {status} {name}: {error[:100]}")
    
    return "\n".join(lessons)
