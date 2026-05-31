"""Task tree data structures for representing hierarchical task execution."""

class TaskNode:
    """task node."""
    def __init__(self, name, parent=None):
        """Initialize the instance.
        
        Args:
            name:
            parent:"""
        self.name = name
        self.parent = parent
        self.children = []
        self.status = "pending"
        self.result = None

    def add_child(self, child_node):
        """add child.
        
        Args:
            child_node:"""
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
        """to dict."""
        return {
            "name": self.name,
            "status": self.status,
            "result": self.result,
            "children": [c.to_dict() for c in self.children]
        }

class TaskTree:
    """task tree."""
    def __init__(self, root_name):
        """Initialize the instance.
        
        Args:
            root_name:"""
        self.root = TaskNode(root_name)

    def to_dict(self):
        """to dict."""
        return self.root.to_dict()
