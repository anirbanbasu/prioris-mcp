"""Tracks in-flight background embedding tasks - 'building' status is derived from this, never persisted.

See docs/requirement-specification/search/02-vector-search.md#index-status-is-per-documentnote-derived-by-comparing-recorded-vs-configured-model.
A process restart mid-embedding simply loses the in-flight task; the next status check honestly
reports whatever the record was before (not_built/stale), which is self-healing by construction.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class EmbeddingScheduler:
    """Fire-and-forget async task tracking, keyed by an opaque tuple (e.g. (provider, id, format))."""

    def __init__(self, max_concurrent: int | None = None) -> None:
        self._tasks: dict[tuple, asyncio.Task] = {}
        self._pending: dict[tuple, tuple[Callable[[], Awaitable[None]], Callable[[], None] | None]] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent) if max_concurrent is not None else None

    def is_building(self, key: tuple) -> bool:
        """Whether a live background embedding task currently exists for `key`."""
        return key in self._tasks

    def schedule(
        self,
        key: tuple,
        coro_factory: Callable[[], Awaitable[None]],
        *,
        on_discarded: Callable[[], None] | None = None,
    ) -> None:
        """Schedule `coro_factory()` to run in the background.

        If `key` is already in flight, the previous run is left alone but `coro_factory` is
        remembered as the latest pending factory for `key` - once the in-flight run finishes, it
        re-triggers with this (possibly newer) factory instead of the update being dropped.

        `on_discarded`, if given, is called (synchronously, never awaited) if and only if this
        exact pending factory is later discarded by `cancel()` without ever running. It is not
        called if a later `schedule()` call for the same key supersedes it first - superseding is
        a newer update winning, not a cancellation - nor if `coro_factory` becomes the live task
        directly (i.e. `key` wasn't already in flight when this call was made).
        """
        if key in self._tasks:
            self._pending[key] = (coro_factory, on_discarded)
            return
        task = asyncio.ensure_future(self._run(key, coro_factory))
        self._tasks[key] = task

    async def cancel(self, key: tuple) -> None:
        """Cancel any in-flight (or pending-rerun) task for `key` and wait for it to stop.

        A no-op if none exists. Waiting matters: `task.cancel()` alone only requests cancellation
        - if the task is mid-write inside `to_thread.run_sync` (not cancellable once started), it
        can otherwise finish its write after a caller's own subsequent delete completes, leaving a
        stale row behind. Awaiting here closes that race by not returning until the task has
        actually stopped.

        A still-pending, never-run factory for `key` (if one exists) is always being discarded
        here - independent of whether a live task also exists for `key` - so its `on_discarded`
        (if any) fires unconditionally. A live task's own wrapper already handles its own
        accounting through its own `except asyncio.CancelledError` branch, so that case is not
        also treated as a discarded pending factory here - doing so would double-count it.
        """
        task = self._tasks.pop(key, None)
        pending = self._pending.pop(key, None)
        if pending is not None:
            _, on_discarded = pending
            if on_discarded is not None:
                on_discarded()
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run(self, key: tuple, coro_factory: Callable[[], Awaitable[None]]) -> None:
        try:
            if self._semaphore is not None:
                async with self._semaphore:
                    await coro_factory()
            else:
                await coro_factory()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Background embedding task failed for key=%r", key)
        finally:
            # A cancel() may have already evicted (and replaced) this key's entry with a newer
            # task before this finally clause runs (cancellation delivery is not instantaneous) -
            # only pop if we're still the task on record, so a superseded run's cleanup can't
            # evict a newer task's bookkeeping entry.
            if self._tasks.get(key) is asyncio.current_task():
                self._tasks.pop(key, None)
        pending = self._pending.pop(key, None)
        if pending is not None:
            factory, on_discarded = pending
            self.schedule(key, factory, on_discarded=on_discarded)

    async def wait_all(self) -> None:
        """Wait for every tracked task, including any dirty re-runs it triggers. Test support only."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)
