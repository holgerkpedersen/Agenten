"""Multi-language translations for Agenten — loaded from JSON files per language."""

import os
import json as _json

_LANG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lang")
LANG = {}
for _lang_code in ["da", "en", "es", "zh"]:
    _path = os.path.join(_LANG_DIR, f"{_lang_code}.json")
    if os.path.exists(_path):
        with open(_path, encoding="utf-8") as _f:
            LANG[_lang_code] = _json.load(_f)

def t(key, lang="da"):
    """Get translated string via dot notation: t('log.task_start', 'en')
    Supports both nested keys (e.g. 'ui.title') and flat dot-keys in ui (e.g. 'session.default_name')."""
    base = LANG.get(lang, LANG["da"])
    keys = key.split(".")
    d = base
    for k in keys:
        if isinstance(d, dict):
            if k in d:
                d = d[k]
                continue
        # Key not found in nested path — try flat key in ui
        ui = base.get("ui", {})
        if key in ui:
            return ui[key]
        return f"?{key}"
    return d if isinstance(d, (str, list)) else str(d)


def get_ui_translations(lang):
    """Return all UI translations for the frontend as a flat dict."""
    data = LANG.get(lang, LANG["da"])
    ui = dict(data.get("ui", {}))
    T = data.get("templates", {})
    for k, v in T.items():
        ui[f"template_name_{k}"] = v
    return ui
