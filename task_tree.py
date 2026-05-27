class TaskNode:
    def __init__(self, name, parent=None):
        self.name = name
        self.parent = parent
        self.children = []
        self.status = "pending"
        self.result = None

    def add_child(self, child_node):
        if child_node.parent is not None and child_node in child_node.parent.children:
            child_node.parent.children.remove(child_node)
        # Cycle detection: prevent adding an ancestor as child
        node = self
        while node is not None:
            if node is child_node:
                return child_node
            node = getattr(node, 'parent', None)
        child_node.parent = self
        self.children.append(child_node)
        return child_node

    def to_dict(self):
        return {
            "name": self.name,
            "status": self.status,
            "result": self.result,
            "children": [c.to_dict() for c in self.children]
        }

class TaskTree:
    def __init__(self, root_name):
        self.root = TaskNode(root_name)

    def to_dict(self):
        return self.root.to_dict()