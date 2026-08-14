"""Tracks in-flight background embedding tasks - 'building' status is derived from this, never persisted.

See docs/requirement-specification/search/02-vector-search.md#index-status-is-per-documentnote-derived-by-comparing-recorded-vs-configured-model.
A process restart mid-embedding simply loses the in-flight task; the next status check honestly
reports whatever the record was before (not_built/stale), which is self-healing by construction.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class EmbeddingScheduler:
    """Fire-and-forget async task tracking, keyed by an opaque tuple (e.g. (provider, id, format))."""

    def __init__(self) -> None:
        self._tasks: dict[tuple, asyncio.Task] = {}
        self._pending: dict[tuple, Callable[[], Awaitable[None]]] = {}

    def is_building(self, key: tuple) -> bool:
        """Whether a live background embedding task currently exists for `key`."""
        return key in self._tasks

    def schedule(self, key: tuple, coro_factory: Callable[[], Awaitable[None]]) -> None:
        """Schedule `coro_factory()` to run in the background.

        If `key` is already in flight, the previous run is left alone but `coro_factory` is
        remembered as the latest pending factory for `key` - once the in-flight run finishes, it
        re-triggers with this (possibly newer) factory instead of the update being dropped.
        """
        if key in self._tasks:
            self._pending[key] = coro_factory
            return
        task = asyncio.ensure_future(self._run(key, coro_factory))
        self._tasks[key] = task

    def cancel(self, key: tuple) -> None:
        """Cancel any in-flight (or pending-rerun) task for `key`. A no-op if none exists."""
        task = self._tasks.pop(key, None)
        if task is not None:
            task.cancel()
        self._pending.pop(key, None)

    async def _run(self, key: tuple, coro_factory: Callable[[], Awaitable[None]]) -> None:
        try:
            await coro_factory()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Background embedding task failed for key=%r", key)
        finally:
            self._tasks.pop(key, None)
        pending = self._pending.pop(key, None)
        if pending is not None:
            self.schedule(key, pending)

    async def wait_all(self) -> None:
        """Wait for every tracked task, including any dirty re-runs it triggers. Test support only."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)
