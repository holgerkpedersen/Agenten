import ast
import os
import re
import hashlib
import tempfile
from typing import Any, Dict, List, Tuple, Optional
from lang import t
from i18n import K
import config
from ast_index import _indexed_dirs, _GLOBAL_SYMBOL_INDEX, _format_params, build_ast_index, _find_enclosing_symbol, _list_top_level_vars, _list_top_level_symbols, _scan_dir_into_index, _build_global_symbol_index, _ensure_workdir_indexed, list_symbols, locate_code
from cache import _FILE_CONTENT_CACHE, _CONTENT_CACHE_MAX_SIZE, _is_file_cached, _cache_file_content, _get_cached_file_content, _clear_file_content_cache
from chunk_reader import read_location, list_chunks, read_chunk
from delegation import STUB_PATTERN, detect_delegations
from file_context import FOLDER_SCAN_MAX_FILES, FOLDER_SCAN_MAX_DEPTH, FOLDER_SCAN_EXCLUDE_DIRS, FOLDER_SCAN_EXCLUDE_FILES, FOLDER_SCAN_EXTENSIONS, read_file_content, get_single_file_context, get_folder_context
from file_hash import file_hash
from path_utils import _BASE_DIR, _SAFE_DIRS, _is_safe_path, _resolve_workdir, auto_detect_workdir, _resolve_path, is_safe_location
from text_utils import CHUNK_SIZE, chunk_text
for _td in (os.environ.get(k) for k in ('TMPDIR', 'TEMP', 'TMP')):
    if _td:
        _SAFE_DIRS.add(os.path.realpath(_td))
_SAFE_DIRS.add(os.path.realpath(tempfile.gettempdir()))
for _sub in ('exports', 'uploads'):
    _p = os.path.realpath(os.path.join(_BASE_DIR, _sub))
    _SAFE_DIRS.add(_p)
