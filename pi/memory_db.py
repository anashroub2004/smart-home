"""In-memory stand-in for the Realtime Database REST API (get / put / patch / post / delete).

Used by `sim_house.py --twin-only` (digital twin without Firebase: any speed, no quota) and by the tests.
"""
import copy
import itertools


class MemoryDB:
    base, ns = "memory", "memory"

    def __init__(self):
        self.root = {}
        self.n = itertools.count()
        self.writes = 0

    def _walk(self, path, create=False):
        node = self.root
        parts = [p for p in path.split("/") if p]
        for p in parts[:-1]:
            if p not in node or not isinstance(node[p], dict):
                if not create:
                    return None, None
                node[p] = {}
            node = node[p]
        return node, (parts[-1] if parts else None)

    def get(self, path, **query):
        if not path:
            return copy.deepcopy(self.root) or None
        node, key = self._walk(path)
        return copy.deepcopy(node.get(key)) if node is not None else None

    def put(self, path, value):
        self.writes += 1
        if not path:
            self.root = copy.deepcopy(value) or {}
            return
        node, key = self._walk(path, True)
        if value is None:
            node.pop(key, None)
        else:
            node[key] = copy.deepcopy(value)

    def patch(self, path, value):
        self.writes += 1
        node, key = self._walk(path, True)
        target = self.root if key is None else node.setdefault(key, {})
        for k, v in value.items():
            if "/" in k:
                self.put(f"{path}/{k}", v)
            else:
                target[k] = copy.deepcopy(v)

    def post(self, path, value):
        key = f"k{next(self.n):06d}"
        self.put(f"{path}/{key}", value)
        return key

    def delete(self, path):
        self.put(path, None)


FakeDB = MemoryDB
