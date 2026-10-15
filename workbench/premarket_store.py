"""Coordinate short local checkpoints, never serialize vendor downloads.

Only the in-process premarket workers use this adapter. Schema, SQL, commit /
rollback and artifact ownership remain in Store. All worker connections use
the same existing artifact lock, in artifact -> DB order; network requests
and strategy calculations stay outside these connection scopes.
"""
from contextlib import contextmanager

from .store import Store


class PremarketStore(Store):
    @contextmanager
    def connect(self):
        with self._files_lock:
            with super().connect() as db:
                yield db
