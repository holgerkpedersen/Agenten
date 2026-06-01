"""Multi-language translations for Agenten — loaded from JSON files per language."""

import os
import json as _json
from config import get_logger
log = get_logger(__name__)

_LANG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lang")
LANG = {}
for _lang_code in ["da", "en", "es", "zh"]:
    _path = os.path.join(_LANG_DIR, f"{_lang_code}.json")
    if os.path.exists(_path):
        try:
            with open(_path, encoding="utf-8") as _f:
                LANG[_lang_code] = _json.load(_f)
        except (_json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load translation %s: %s", _lang_code, e)

def t(key: str, lang: str = "da") -> str:
    """Get translated string via dot notation: t('log.task_start', 'en')
    Supports both nested keys (e.g. 'ui.title') and flat dot-keys in ui (e.g. 'session.default_name')."""
    base = LANG.get(lang, LANG["da"])
    # Check direct match at root level first
    if key in base:
        return base[key]
    # Also search all sub-dicts for keys with dots (like session.demo_math_fact)
    for section in base.values():
        if isinstance(section, dict) and key in section:
            return section[key]
    keys = key.split(".")
    d = base
    for k in keys:
        if isinstance(d, dict):
            if k in d:
                d = d[k]
                continue
            # Key not found in nested path — return marker
            return f"?{key}"
    return d if isinstance(d, (str, list)) else str(d)


def get_ui_translations(lang: str) -> dict:
    """Return all UI translations for the frontend as a flat dict."""
    data = LANG.get(lang, LANG["da"])
    ui = dict(data.get("ui", {}))
    T = data.get("templates", {})
    for k, v in T.items():
        ui[f"template_name_{k}"] = v
    return ui
