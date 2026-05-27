import os

class ModuleBuilder:
    def __init__(self, action_history):
        self.action_history = action_history

    def get_repeated_actions(self, threshold=2):
        from collections import Counter
        counter = Counter(self.action_history)
        return [action for action, count in counter.items() if count >= threshold]

    def should_build_module(self, threshold=2):
        return len(self.get_repeated_actions(threshold)) > 0

    def build_module(self, action_name, action_code_template):
        safe_name = action_name.replace(' ', '_')
        if not safe_name or '..' in safe_name or safe_name.startswith('/') or safe_name.startswith('\\'):
            return {"error": "Invalid module name"}
        allowed_ext = '.py'
        if not safe_name.endswith(allowed_ext):
            safe_name += allowed_ext
        os.makedirs("custom_modules", exist_ok=True)
        custom_dir = os.path.realpath("custom_modules")
        module_path = os.path.join(custom_dir, safe_name)
        if not module_path.startswith(custom_dir + os.sep):
            return {"error": "Path traversal detected"}
        with open(module_path, "w", encoding="utf-8") as f:
            f.write(action_code_template)
        return {"module_path": module_path, "message": f"Module {action_name} created"}