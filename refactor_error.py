from typing import Any
from refactor_utils import log, _BUILTINS, _BUILTINS_TYPING, _KNOWN_SYMBOL_IMPORTS, _KNOWN_MODULE_SYMBOLS, _extracted_registry, _atomic_replace, _parse_symbols_list, _auto_add_known_imports, _list_top_level_symbol_names, _find_unresolved_local_deps, _detect_import_cycle_risk, _split_imports_from_code, _registry_key, _is_nested_function, clear_extracted_registry, _mark_extracted, _is_already_extracted, _extract_module_from_import, _has_back_import

class RefactoringError(Exception):
    """Structured error for refactoring operations with rollback support.

    Attributes:
        category: Error category for logging and retry logic.
        filepath: The affected file path.
        snapshot: Optional FileSnapshot for automatic rollback.
        details: Dict with line numbers, content excerpts, etc.
    """
    # Error categories
    SYNTAX = "syntax"
    FILE_NOT_FOUND = "file_not_found"
    SYMBOL_NOT_FOUND = "symbol_not_found"
    IMPORT_FAILED = "import_failed"
    EXTRACTION_FAILED = "extraction_failed"
    REMOVAL_FAILED = "removal_failed"
    TARGET_SYNTAX = "target_syntax"
    CIRCULAR_IMPORT = "circular_import"
    SYMBOL_NESTED = "symbol_nested"
    SYMBOL_NESTED_STATEFUL = "symbol_nested_stateful"

    def __init__(self, message: str, category: str = "unknown",
                 filepath: str = "", snapshot: Any = None,
                 details: dict | None = None):
        self.category = category
        self.filepath = filepath
        self.snapshot = snapshot
        self.details = details or {}
        super().__init__(message)



class FileSnapshot:
    """Memento: stores file content for rollback."""

    def __init__(self, path: str, content: str) -> None:
        self.path = path
        self.content = content

    @classmethod
    def create(cls, path: str) -> 'FileSnapshot':
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read().replace('\r\n', '\n').replace('\r', '\n')
        return cls(path, content)

    def restore(self) -> None:
        tmppath = self.path + '.tmp'
        with open(tmppath, 'w', encoding='utf-8') as f:
            f.write(self.content)
        _atomic_replace(tmppath, self.path)
