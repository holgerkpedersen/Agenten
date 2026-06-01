import os
from typing import Any
from agent_files import _is_safe_path

class ModuleBuilder:
    """module builder."""
    def __init__(self, action_history: Any) -> None:
        """Initialize the instance.
        
        Args:
            action_history:"""
        self.action_history = action_history

    def get_repeated_actions(self, threshold: int = 2) -> list[str]:
        """get repeated actions.
        
        Args:
            threshold:"""
        from collections import Counter
        counter = Counter(self.action_history)
        return [action for action, count in counter.items() if count >= threshold]

    def should_build_module(self, threshold: int = 2) -> bool:
        """should build module.
        
        Args:
            threshold:"""
        return len(self.get_repeated_actions(threshold)) > 0

    def build_module(self, action_name: str, action_code_template: str) -> dict:
        """build module.
        
        Args:
            action_name:
            action_code_template:"""
        safe_name = action_name.replace(' ', '_')
        if not safe_name:
            return {"error": "Invalid module name"}
        allowed_ext = '.py'
        if not safe_name.endswith(allowed_ext):
            safe_name += allowed_ext
        os.makedirs("custom_modules", exist_ok=True)
        custom_dir = os.path.realpath("custom_modules")
        module_path = os.path.join(custom_dir, safe_name)
        if not _is_safe_path(custom_dir, module_path):
            return {"error": "Path traversal detected"}
        with open(module_path, "w", encoding="utf-8") as f:
            f.write(action_code_template)
        return {"module_path": module_path, "message": f"Module {action_name} created"}
