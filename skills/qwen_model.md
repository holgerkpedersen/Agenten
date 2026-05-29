---
action_types: [analyze, write]
---

## Qwen Model — Tool Calling Viden

Qwen 3.x modeller (især qwen3.6-27b og qwen3.6-27b-mtp) har specifikke krav til tool-calling.

### Kendte Problemer

| Problem | Symptom | Årsag | Løsning |
|---------|---------|-------|---------|
| Tomme args | `{}` eller manglende parametre | Qwen udelader params hvis descriptions er for generiske | Brug beskrivende parameter-descriptions i tool schema |
| Forkerte param-navne | `cmd` i stedet for `command` | Qwen gætter param-navne | Tilføj strict instruktion om EXAKTE param-navne |
| Manglende END-marker | Tool call uden `<<<END>>>` | Qwen afslutter for tidligt | Tilføj eksempel i prompt med komplet format |
| `"tool":"name"` hallucination | Error: Hallucineret tool-navn | Qwen oversætter "navn" til feltnavn | Brug eksplicitte instruktioner |

### System Prompt Fix

I `build_system_prompt()` i `tools.py`, tilføj strict instruktion efter tools_desc:

```python
prompt += f"\n\n**STRICT RULE:** The `args` object keys MUST match the parameter names exactly as listed. Do NOT rename, omit, or add extra keys. You MUST end every tool call with {self.END_MARKER}."
```

### OpenAI Schema Fix

I `get_openai_tools_for_active()` i `tools.py`, erstat generic descriptions:

```python
# FØR (dårlig for Qwen):
"properties": {p: {"type": "string", "description": p} for p in tool.parameters},

# EFTER (bedre for Qwen):
"properties": {p: {"type": "string", "description": f"Parameter '{p}' of tool '{tool.name}'. {tool.description}"} for p in tool.parameters},
```

### Testede Modeller

| Model | Tool Calling | Vision | Notes |
|-------|-------------|--------|-------|
| `qwen3.6-27b` | ✅ OK med strict prompt | N/A | Text-only |
| `qwen3.6-27b-mtp` | ✅ OK med strict prompt | N/A | Text-only, MTP variant |
| `qwen3.5-9b-mtp` | ⚠️ Delvist | N/A | Kræver korte prompts |
| `qwen/qwen3.5-9b` (vl) | N/A | ✅ data_url | Vision model |

### Debugging Workflow

1. Tjek `flask_output.log` for `[ERROR: ...]` — viser præcis JSON eller parsing-fejl
2. Tjek system_prompt i loggen: `system_prompt length:` — bekræfter tools_desc er med
3. Hvis tool args er tomme: tilføj strict instruktion i prompten
4. Hvis param-navne er forkerte: tjek at descriptions matcher de faktiske funktionsparametre
