"""Bounded, task-only reuse of already parsed immutable frames."""
from collections import OrderedDict


class ScanReads:
    def __init__(self, limit=64 * 1024 * 1024):
        self.limit, self.bytes = limit, 0
        self.frames = OrderedDict()

    def get(self, did):
        if did not in self.frames:
            return None
        self.frames.move_to_end(did)
        return self.frames[did][0].copy(deep=True)

    def put(self, did, frame):
        size = int(frame.memory_usage(index=True, deep=True).sum())
        if size > self.limit or did in self.frames:
            return
        while self.frames and self.bytes + size > self.limit:
            _, (_, previous) = self.frames.popitem(last=False)
            self.bytes -= previous
        self.frames[did] = (frame.copy(deep=True), size)
        self.bytes += size
