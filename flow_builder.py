import json
from typing import List, Dict, Optional
from urllib.parse import quote

FLOW_SCHEMA = "https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#"


def get_flow_template() -> dict:
    return {
        "definition": {
            "$schema": FLOW_SCHEMA,
            "contentVersion": "1.0.0.0",
            "parameters": {},
            "triggers": {
                "manual": {
                    "type": "Request",
                    "kind": "Http",
                    "inputs": {
                        "schema": {
                            "properties": {
                                "query": {"type": "string"},
                                "maxResults": {"type": "integer", "default": 5}
                            },
                            "required": ["query"]
                        }
                    }
                }
            },
            "actions": {
                "Search_the_web": {
                    "type": "Http",
                    "inputs": {
                        "method": "GET",
                        "uri": "https://html.duckduckgo.com/html/?q={query}",
                        "headers": {"User-Agent": "Mozilla/5.0"}
                    },
                    "runAfter": {}
                },
                "Parse_results": {
                    "type": "Compose",
                    "inputs": {},
                    "runAfter": {
                        "Search_the_web": ["Succeeded"]
                    }
                },
                "Store_results": {
                    "type": "Compose",
                    "inputs": {},
                    "runAfter": {
                        "Parse_results": ["Succeeded"]
                    }
                }
            }
        }
    }


def generate_research_flow(topic: str, results: List[Dict]) -> dict:
    flow = get_flow_template()
    flow["definition"]["triggers"]["manual"]["inputs"]["schema"]["properties"]["query"]["default"] = topic

    flow["definition"]["actions"]["Search_the_web"]["inputs"]["uri"] = \
        f"https://html.duckduckgo.com/html/?q={quote(topic, safe='')}"

    parsed = []
    for r in results:
        parsed.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("snippet", "")
        })

    flow["definition"]["actions"]["Parse_results"]["inputs"] = {
        "topic": topic,
        "timestamp": "__NOW__",
        "total_results": len(parsed),
        "results": parsed
    }

    flow["definition"]["actions"]["Store_results"]["inputs"] = {
        "flow_name": f"Research: {topic}",
        "status": "completed",
        "output": "@{outputs('Parse_results')}"
    }

    return flow


def flow_to_mermaid(flow: dict) -> str:
    actions = flow.get("definition", {}).get("actions", {})
    if not actions:
        return "graph LR\n  Start[No actions]"

    lines = ["graph LR"]
    lines.append("  Trigger(Manual Trigger)")
    prev = "Trigger"

    for name in actions:
        safe_name = name.replace(" ", "_").replace("&", "and")
        display = name.replace("_", " ").replace("-", " ")
        lines.append(f"  {safe_name}[\"{display}\"]")
        lines.append(f"  {prev} --> {safe_name}")
        prev = safe_name

    lines.append(f"  {prev} --> End([Done])")

    # Check if there are results to show
    parse_action = actions.get("Parse_results", {})
    results = parse_action.get("inputs", {}).get("results", [])
    if results:
        lines.append(f"  {prev} -.-> Results[\"📄 {len(results)} results\"]")

    return "\n".join(lines)


def flow_to_mermaid_full(flow: dict) -> str:
    actions = flow.get("definition", {}).get("actions", {})

    lines = ["graph TB"]
    lines.append("  Trigger[\"🔷 Manual Trigger\"]")
    lines.append("  style Trigger fill:#3b82f6,color:#fff")
    prev = "Trigger"

    for idx, (name, action) in enumerate(actions.items()):
        safe_name = f"Action{idx}"
        display = name.replace("_", " ").replace("-", " ")

        act_type = action.get("type", "Action")
        if act_type == "Http":
            shape = f"🌐 {display}"
            fill = "#10b981"
        elif act_type == "Compose":
            shape = f"📦 {display}"
            fill = "#8b5cf6"
        else:
            shape = f"⚙️ {display}"
            fill = "#f59e0b"

        lines.append(f"  {safe_name}[\"{shape}\"]")
        lines.append(f"  style {safe_name} fill:{fill},color:#fff")
        lines.append(f"  {prev} -->|Succeeded| {safe_name}")
        prev = safe_name

    lines.append(f"  {prev} --> End[\"✅ Complete\"]")
    lines.append("  style End fill:#10b981,color:#fff")

    return "\n".join(lines)


def format_flow_json(flow: dict) -> str:
    return json.dumps(flow, indent=2, ensure_ascii=False)
