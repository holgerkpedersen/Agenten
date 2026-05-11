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
        os.makedirs("custom_modules", exist_ok=True)
        module_path = f"custom_modules/{action_name.replace(' ', '_')}.py"
        with open(module_path, "w") as f:
            f.write(action_code_template)
        return {"module_path": module_path, "message": f"Module {action_name} created"}