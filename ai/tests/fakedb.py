"""In-memory stand-in for the Realtime Database REST API, for tests."""
import copy, itertools
class FakeDB:
    def __init__(self): self.root = {}; self.n = itertools.count(); self.writes = 0
    def _walk(self, path, create=False):
        node = self.root
        parts = [p for p in path.split("/") if p]
        for p in parts[:-1]:
            if p not in node or not isinstance(node[p], dict):
                if not create: return None, None
                node[p] = {}
            node = node[p]
        return node, (parts[-1] if parts else None)
    def get(self, path, **q):
        if not path: return copy.deepcopy(self.root) or None
        node, k = self._walk(path)
        return copy.deepcopy(node.get(k)) if node is not None else None
    def put(self, path, value):
        self.writes += 1
        if not path: self.root = copy.deepcopy(value) or {}; return
        node, k = self._walk(path, True)
        if value is None: node.pop(k, None)
        else: node[k] = copy.deepcopy(value)
    def patch(self, path, value):
        self.writes += 1
        node, k = self._walk(path, True)
        if k is None: target = self.root
        else: target = node.setdefault(k, {})
        for kk, v in value.items():
            if "/" in kk: self.put(f"{path}/{kk}", v)
            else: target[kk] = copy.deepcopy(v)
    def post(self, path, value):
        key = f"k{next(self.n):06d}"; self.put(f"{path}/{key}", value); return key
    def delete(self, path): self.put(path, None)
