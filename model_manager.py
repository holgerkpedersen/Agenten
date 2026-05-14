import subprocess
import shutil
import os
import requests
import difflib

LMS_PATH = shutil.which('lms') or shutil.which('lms.exe') or os.path.join(
    os.environ.get('USERPROFILE', os.environ.get('HOME', '')), '.lmstudio', 'bin', 'lms.exe'
)
LM_STUDIO_HOST = os.environ.get('LM_HOST', '127.0.0.1')
LM_STUDIO_PORT = os.environ.get('LM_PORT', '1234')
LM_STUDIO_API = f'http://{LM_STUDIO_HOST}:{LM_STUDIO_PORT}/api/v1'


def get_loaded_models():
    """Fetch currently loaded models from LM Studio REST API."""
    try:
        r = requests.get(f'{LM_STUDIO_API}/models', timeout=5)
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
        print(f'[model_manager] Failed to fetch models: {e}')
    return {}


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
    try:
        r = requests.get(f'http://{LM_STUDIO_HOST}:{LM_STUDIO_PORT}/v1/models', timeout=5)
        if r.status_code == 200:
            return [m['id'] for m in r.json().get('data', []) if 'embedding' not in m.get('id', '').lower()]
    except Exception:
        pass
    return []


def get_all_rest_models():
    """Fetch ALL models (local + remote/LM Link) from LM Studio v1 REST API.
    LM Link is transparent — remote models appear with same key as local ones.
    Requests to localhost:1234 are automatically routed to the right device."""
    try:
        r = requests.get(f'{LM_STUDIO_API}/models', timeout=5)
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
        print(f'[model_manager] Failed to fetch REST models: {e}')
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
    if not LMS_PATH:
        return False, 'lms CLI not found'
    if not os.path.exists(LMS_PATH):
        return False, f'lms not found at {LMS_PATH}'

    resolved = resolve_model_key(model_key)
    if callback:
        callback(f'Loading {resolved}...')

    cmd = [LMS_PATH, 'load', resolved, '--parallel', str(parallel), '--yes']
    if identifier:
        cmd.extend(['--identifier', identifier])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, errors='replace')
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
    if not LMS_PATH:
        return False, 'lms CLI not found'

    if identifier == '--all':
        cmd = [LMS_PATH, 'unload', '--all']
        if callback:
            callback('Unloading all models...')
    else:
        # Try to fuzzy-match identifier
        loaded = get_loaded_models()
        match_id = identifier
        for key, info in loaded.items():
            for inst in info.get('loaded_instances', []):
                iid = inst.get('id', '')
                if identifier.lower() in iid.lower() or identifier.lower() in key.lower():
                    match_id = iid
                    break
        cmd = [LMS_PATH, 'unload', match_id]
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
