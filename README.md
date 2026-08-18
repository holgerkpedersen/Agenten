# Agenten

Danish-first AI task planner with tool usage, delivered on the 2TI stack.

## Delivery status

- i18n: en_US default UI language (lang/en_us.json), hardcoded Danish routed through `_()`
- Icons: Iconify element form (`<iconify-icon icon="solar:...">`), libs self-hosted in `static/assets/`
- Theme: 2TITHEME (WowDash admin theme classes) wired via `static/theme.css` + `static/assets/css/wowdash.css`
- Models: LM Studio stays original (friend); 2TI local engines configured in `config.py` (llama-server :8900, Ollama :11434, OllaCompiler :8066)

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python api_server.py
```

App listens on :5000.

## Structure

- `api_server.py` — Flask entry point (modules under `agenten/`)
- `agent_*.py` — agent subsystems (core, tasks, fs, tree, skills, issues, git)
- `tools.py` / `llm_wrapper.py` — tool dispatch + LLM client
- `lang.py` + `lang/*.json` — i18n (da/en/en_us/es/zh)
- `static/` — SPA (`index.html`, `flow.html`, `theme.css`, `assets/`)

See `docs/refactor-plan.md` for the modularization roadmap.

## Branches / remotes

- `github`  → https://github.com/LebToki/Agenten.git (fork, push `main`)
- `upstream`→ https://github.com/holgerkpedersen/Agenten.git (original project)
- `gitea`   → http://git.2ti.local:3000/tarek/Agenten.git (local mirror)
