import collections
from importlib import import_module


class _mx_BaseMxObject:
    pass


class _mx_BaseParent(_mx_BaseMxObject):

    _mx_spaces: dict[str, '_mx_BaseSpace']

    def _mx_walk(self):
        """Generator yielding spaces in breadth-first order"""
        que = collections.deque([self])
        while que:
            parent = que.popleft()
            yield parent
            for child in parent._mx_spaces.values():
                que.append(child)


class _mx_BaseModel(_mx_BaseParent):
    pass


class _mx_BaseSpace(_mx_BaseParent):

    def _mx_get_object(self, keys):
        obj = self
        for name in keys:
            if name[0] == ".":
                for _ in name[1:]:
                    obj = obj._parent
            else:
                obj = getattr(obj, name)

        return obj


