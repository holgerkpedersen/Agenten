import subprocess
import shutil
import os
import requests
import difflib
import config
from urllib.parse import urlparse
from config import get_logger
log = get_logger(__name__)

_LMS_CACHE = None


def _get_lms_path():
    global _LMS_CACHE
    if _LMS_CACHE is None:
        _LMS_CACHE = shutil.which('lms') or shutil.which('lms.exe') or os.path.join(
            os.environ.get('USERPROFILE', os.environ.get('HOME', '')), '.lmstudio', 'bin', 'lms.exe'
        )
    return _LMS_CACHE


def _rest_api_base():
    """Derive LM Studio REST API base (no /v1 path suffix) from config LLM_BASE_URL."""
    parsed = urlparse(config.LLM_BASE_URL)
    base = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        base += f":{parsed.port}"
    if parsed.path and parsed.path.rstrip('/') != '/v1':
        base += parsed.path.rstrip('/')
    return base + '/api/v1'


def get_loaded_models():
    """Fetch currently loaded models from LM Studio REST API."""
    if os.environ.get('OPENCODE_BASE_URL'):
        return None
    try:
        r = requests.get(f'{config.LLM_BASE_URL.replace("/v1", "")}/api/v1/models', timeout=5)
        if r.status_code == 200:
            data = r.json()
            loaded = {}
            for m in data.get('models', data.get('data', [])):
                mtype = m.get('type', '')
                if mtype and mtype != 'llm':
                    continue
                key = m.get('key', m.get('id', ''))
                instances = m.get('loaded_instances', [])
                loaded[key] = {
                    'key': key,
                    'loaded_instances': instances,
                    'is_loaded': len(instances) > 0,
                }
            return loaded
    except Exception as e:
        log.warning("Failed to fetch models: %s", e)
        return None


def is_model_loaded(model_key):
    """Check if a model (by any identifier) is currently loaded in LM Studio."""
    loaded = get_loaded_models()
    if not loaded:
        return False, None
    for info in loaded.values():
        if not info.get('is_loaded'):
            continue
        # Check both the model key and all loaded instance IDs
        if model_key == info['key'] or model_key in info['key'] or info['key'] in model_key:
            return True, info['key']
        for inst in info.get('loaded_instances', []):
            iid = inst.get('id', '')
            if iid and (model_key == iid or model_key in iid or iid in model_key):
                return True, iid
    return False, None


def get_available_models():
    """Fetch all known models from LM Studio (OpenAI-compatible endpoint)."""
    if os.environ.get('OPENCODE_BASE_URL'):
        return []
    try:
        r = requests.get(f'{config.LLM_BASE_URL}/models', timeout=5)
        if r.status_code == 200:
            return [m['id'] for m in r.json().get('data', []) if 'embedding' not in m.get('id', '').lower()]
    except Exception as e:
        log.warning("Failed to fetch models from LM Studio: %s", e)
    return []


def get_all_rest_models():
    """Fetch ALL models (local + remote/LM Link) from LM Studio v1 REST API.
    LM Link is transparent — remote models appear with same key as local ones.
    Requests to localhost:1234 are automatically routed to the right device."""
    if os.environ.get('OPENCODE_BASE_URL'):
        return []
    try:
        r = requests.get(f'{_rest_api_base()}/models', timeout=5)
        if r.status_code == 200:
            data = r.json()
            models = []
            for m in data.get('models', data.get('data', [])):
                mtype = m.get('type', '')
                if mtype and mtype != 'llm':
                    continue
                key = m.get('key', m.get('id', ''))
                loaded_instances = m.get('loaded_instances', [])
                models.append({
                    'id': key,
                    'display_name': m.get('display_name', key),
                    'publisher': m.get('publisher', ''),
                    'state': 'loaded' if loaded_instances else 'not-loaded',
                    'is_loaded': len(loaded_instances) > 0,
                    'loaded_instances': loaded_instances,
                    'quantization': m.get('quantization', {}),
                    'params_string': m.get('params_string', ''),
                    'max_context_length': m.get('max_context_length', 0),
                })
            return models
    except Exception as e:
        log.warning("Failed to fetch REST models: %s", e)
    return []


def resolve_model_key(partial_name):
    """Fuzzy match a partial model name to a full key."""
    available = get_available_models()
    if not available:
        return partial_name
    if partial_name in available:
        return partial_name
    matches = difflib.get_close_matches(partial_name, available, n=1, cutoff=0.3)
    if matches:
        return matches[0]
    substring = [m for m in available if partial_name.lower() in m.lower()]
    if substring:
        return substring[0]
    return partial_name


def load_model(model_key, parallel=4, identifier=None, callback=None):
    """Load a model into LM Studio using lms CLI.
    Returns (success: bool, message: str)."""
    lms_path = _get_lms_path()
    if not lms_path:
        return False, 'lms CLI not found'
    if not os.path.exists(lms_path):
        return False, f'lms not found at {lms_path}'

    resolved = resolve_model_key(model_key)

    # SEC-004 Fix: Validate model key against available models to prevent command injection
    available_models = get_available_models()
    if not available_models or resolved not in available_models:
        return False, f'Invalid model key: {resolved}'

    if callback:
        callback(f'Loading {resolved}...')

    cmd = [lms_path, 'load', resolved, '--parallel', str(parallel), '--yes']
    if identifier:
        cmd.extend(['--identifier', identifier])

    try:
        import config
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=config.SUBPROCESS_TIMEOUT, errors='replace')
        if result.returncode == 0:
            return True, f'Loaded: {resolved}'
        else:
            return False, f'Error: {result.stderr.strip() or "unknown error"}'
    except subprocess.TimeoutExpired:
        return False, 'Timeout (120s) — model may still be loading'
    except Exception as e:
        return False, str(e)


def unload_model(identifier, callback=None):
    """Unload a model from LM Studio using lms CLI.
    Use identifier='--all' to unload all models.
    Returns (success: bool, message: str)."""
    if not _get_lms_path():
        return False, 'lms CLI not found'

    if identifier == '--all':
        cmd = [_get_lms_path(), 'unload', '--all']
        if callback:
            callback('Unloading all models...')
    else:
        # Try to fuzzy-match identifier
        loaded = get_loaded_models() or {}
        match_id = identifier
        for key, info in loaded.items():
            for inst in info.get('loaded_instances', []):
                iid = inst.get('id', '')
                if identifier.lower() in iid.lower() or identifier.lower() in key.lower():
                    match_id = iid
                    break
        cmd = [_get_lms_path(), 'unload', match_id]
        if callback:
            callback(f'Unloading {match_id}...')

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, errors='replace')
        if result.returncode == 0:
            return True, f'Unloaded: {identifier}'
        else:
            return False, f'Error: {result.stderr.strip() or "unknown error"}'
    except subprocess.TimeoutExpired:
        return False, 'Timeout'
    except Exception as e:
        return False, str(e)
