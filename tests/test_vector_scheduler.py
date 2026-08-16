import asyncio

from prioris_mcp.vector.scheduler import EmbeddingScheduler


class TestScheduleAndIsBuilding:
    """Tests for EmbeddingScheduler scheduling and is_building status."""

    def test_is_building_false_before_scheduling(self):
        scheduler = EmbeddingScheduler()
        assert scheduler.is_building(("arxiv", "A", "pdf")) is False

    def test_is_building_true_while_task_runs(self):
        scheduler = EmbeddingScheduler()
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_task():
            started.set()
            await release.wait()

        async def scenario():
            scheduler.schedule(("arxiv", "A", "pdf"), slow_task)
            await started.wait()
            still_building = scheduler.is_building(("arxiv", "A", "pdf"))
            release.set()
            await scheduler.wait_all()
            after = scheduler.is_building(("arxiv", "A", "pdf"))
            return still_building, after

        still_building, after = asyncio.run(scenario())
        assert still_building is True
        assert after is False

    def test_scheduling_the_same_key_twice_while_running_reruns_after_completion(self):
        scheduler = EmbeddingScheduler()
        call_count = 0
        started = asyncio.Event()
        release = asyncio.Event()

        async def task():
            nonlocal call_count
            call_count += 1
            started.set()
            await release.wait()

        async def scenario():
            scheduler.schedule(("k",), task)
            await started.wait()
            scheduler.schedule(("k",), task)  # same key, already in flight - reruns after completion
            release.set()
            await scheduler.wait_all()

        asyncio.run(scenario())
        assert call_count == 2

    def test_a_failing_task_still_clears_is_building(self):
        scheduler = EmbeddingScheduler()

        async def failing_task():
            raise RuntimeError("embedding failed")

        async def scenario():
            scheduler.schedule(("k",), failing_task)
            await scheduler.wait_all()
            return scheduler.is_building(("k",))

        assert asyncio.run(scenario()) is False


class TestCancel:
    """Tests for EmbeddingScheduler.cancel()."""

    def test_cancel_stops_an_in_flight_task(self):
        scheduler = EmbeddingScheduler()
        started = asyncio.Event()
        release = asyncio.Event()
        ran_past_wait = False

        async def slow_task():
            nonlocal ran_past_wait
            started.set()
            await release.wait()
            ran_past_wait = True

        async def scenario():
            scheduler.schedule(("k",), slow_task)
            await started.wait()
            task = scheduler._tasks[("k",)]
            release.set()
            await scheduler.cancel(("k",))
            return task.cancelled(), scheduler.is_building(("k",))

        cancelled, still_building_after = asyncio.run(scenario())
        assert cancelled is True
        assert still_building_after is False
        assert ran_past_wait is False

    def test_cancel_waits_for_the_task_to_actually_finish_before_returning(self):
        """D8: cancel() must not return until the in-flight task has genuinely stopped.

        Regression test for the delete-vs-background-write race: a caller doing `await
        scheduler.cancel(key)` immediately followed by a delete must be guaranteed the
        cancelled task can no longer perform its write.
        """
        scheduler = EmbeddingScheduler()
        started = asyncio.Event()
        completed_write = False

        async def slow_task():
            nonlocal completed_write
            started.set()
            await asyncio.sleep(0)  # cancellable checkpoint
            completed_write = True  # must never run if cancelled before this point

        async def scenario():
            scheduler.schedule(("k",), slow_task)
            await started.wait()
            task = scheduler._tasks[("k",)]
            await scheduler.cancel(("k",))
            return task.done()

        done = asyncio.run(scenario())
        assert done is True
        assert completed_write is False

    def test_cancel_unknown_key_is_a_no_op(self):
        scheduler = EmbeddingScheduler()
        asyncio.run(scheduler.cancel(("never-scheduled",)))  # must not raise

    def test_cancel_racing_a_fresh_schedule_does_not_let_stale_cleanup_evict_the_new_task(self):
        """D8: cancel() is now async and itself awaits the old task to finish before returning.

        This reframes the old (pre-D8) synchronous-cancel race: instead of cancel() and the next
        schedule() running back-to-back with no await between them, the race is now cancel()'s own
        internal await-for-completion running concurrently with another caller's schedule() for the
        same key - cancel() pops `self._tasks`/`self._pending` before it starts that await (same
        ordering as before), so the concurrent schedule()'s fresh task must survive `_run`'s
        `finally` clause once the old task's cancellation is actually delivered.
        """
        scheduler = EmbeddingScheduler()
        first_started = asyncio.Event()
        first_release = asyncio.Event()
        second_started = asyncio.Event()
        second_release = asyncio.Event()
        effects: list[str] = []

        async def first_factory():
            first_started.set()
            await first_release.wait()
            effects.append("first")  # must never run - cancelled before reaching here

        async def second_factory():
            second_started.set()
            await second_release.wait()
            effects.append("second")

        async def scenario():
            scheduler.schedule(("k",), first_factory)
            await first_started.wait()

            cancel_task = asyncio.ensure_future(scheduler.cancel(("k",)))
            # Wait until cancel()'s synchronous prefix (popping self._tasks/self._pending) has run
            # but before it has finished - i.e. it is now blocked awaiting the old task's actual
            # cancellation delivery. Polling on state, not a fixed number of loop turns, so this
            # isn't sensitive to exactly how many ticks that prefix takes.
            while ("k",) in scheduler._tasks:
                await asyncio.sleep(0)

            # A fresh schedule() for the same key, concurrent with cancel()'s still-in-flight await.
            scheduler.schedule(("k",), second_factory)
            new_task = scheduler._tasks[("k",)]

            await cancel_task
            await second_started.wait()

            still_building = scheduler.is_building(("k",))
            same_task_on_record = scheduler._tasks.get(("k",)) is new_task

            second_release.set()
            await scheduler.wait_all()
            return still_building, same_task_on_record

        still_building, same_task_on_record = asyncio.run(scenario())
        assert still_building is True
        assert same_task_on_record is True
        assert effects == ["second"]


class TestDirtyRerun:
    """Tests for the schedule-while-in-flight rerun mechanism (I2)."""

    def test_schedule_while_in_flight_reruns_with_latest_factory_after_completion(self):
        scheduler = EmbeddingScheduler()
        started = asyncio.Event()
        release = asyncio.Event()
        recorded = None

        async def first_factory():
            nonlocal recorded
            started.set()
            await release.wait()
            recorded = "first"

        async def second_factory():
            nonlocal recorded
            recorded = "second"

        async def scenario():
            scheduler.schedule(("k",), first_factory)
            await started.wait()
            scheduler.schedule(("k",), second_factory)  # in flight - stored as pending, not run yet
            still_none = recorded is None
            release.set()
            await scheduler.wait_all()
            return still_none

        still_none = asyncio.run(scenario())
        assert still_none is True
        assert recorded == "second"

    def test_wait_all_waits_through_a_dirty_rerun(self):
        scheduler = EmbeddingScheduler()
        started = asyncio.Event()
        release = asyncio.Event()
        effects: list[str] = []

        async def first_factory():
            started.set()
            await release.wait()
            effects.append("first")

        async def second_factory():
            effects.append("second")

        async def scenario():
            scheduler.schedule(("k",), first_factory)
            await started.wait()
            scheduler.schedule(("k",), second_factory)
            release.set()
            await scheduler.wait_all()
            return scheduler.is_building(("k",))

        still_building_after = asyncio.run(scenario())
        assert still_building_after is False
        assert effects == ["first", "second"]


class TestOnDiscarded:
    """Tests for schedule(..., on_discarded=...) - notifies a caller when a pending factory is cancelled unrun."""

    def test_cancel_calls_on_discarded_for_a_pending_factory_that_never_ran(self):
        scheduler = EmbeddingScheduler()
        started = asyncio.Event()
        release = asyncio.Event()
        discarded_count = 0
        pending_ran = False

        async def live_factory():
            started.set()
            await release.wait()

        async def pending_factory():
            nonlocal pending_ran
            pending_ran = True

        def on_discarded():
            nonlocal discarded_count
            discarded_count += 1

        async def scenario():
            scheduler.schedule(("k",), live_factory)
            await started.wait()
            task = scheduler._tasks[("k",)]
            scheduler.schedule(("k",), pending_factory, on_discarded=on_discarded)  # goes into _pending
            release.set()
            await scheduler.cancel(("k",))
            return task.cancelled()

        cancelled = asyncio.run(scenario())
        assert cancelled is True
        assert discarded_count == 1
        assert pending_ran is False

    def test_on_discarded_not_called_when_pending_factory_is_superseded_by_a_later_schedule(self):
        scheduler = EmbeddingScheduler()
        started = asyncio.Event()
        release = asyncio.Event()
        first_discarded = 0
        second_discarded = 0

        async def live_factory():
            started.set()
            await release.wait()

        async def first_pending_factory():
            pass

        async def second_pending_factory():
            pass

        def on_first_discarded():
            nonlocal first_discarded
            first_discarded += 1

        def on_second_discarded():
            nonlocal second_discarded
            second_discarded += 1

        async def scenario():
            scheduler.schedule(("k",), live_factory)
            await started.wait()
            scheduler.schedule(("k",), first_pending_factory, on_discarded=on_first_discarded)
            # Supersedes the first pending factory before the live task ever finishes - a newer
            # update winning, not a cancellation, so the first factory's on_discarded must not fire.
            scheduler.schedule(("k",), second_pending_factory, on_discarded=on_second_discarded)
            release.set()
            await scheduler.cancel(("k",))

        asyncio.run(scenario())
        assert first_discarded == 0
        assert second_discarded == 1

    def test_cancel_with_only_a_live_task_does_not_call_on_discarded(self):
        """No pending entry at all - behaves exactly as before cancel()'s on_discarded handling existed."""
        scheduler = EmbeddingScheduler()
        started = asyncio.Event()
        release = asyncio.Event()
        discarded_count = 0

        async def live_factory():
            started.set()
            await release.wait()

        def on_discarded():
            nonlocal discarded_count
            discarded_count += 1

        async def scenario():
            scheduler.schedule(("k",), live_factory)
            await started.wait()
            release.set()
            await scheduler.cancel(("k",))

        asyncio.run(scenario())
        assert discarded_count == 0


class TestMaxConcurrent:
    """Tests for EmbeddingScheduler(max_concurrent=...) bounding concurrent task execution."""

    def test_is_building_true_for_a_queued_task_waiting_on_the_semaphore(self):
        """A task queued behind the concurrency limit is still "building", not "not started"."""
        scheduler = EmbeddingScheduler(max_concurrent=1)
        first_started = asyncio.Event()
        first_release = asyncio.Event()
        second_started = asyncio.Event()

        async def first_task():
            first_started.set()
            await first_release.wait()

        async def second_task():
            second_started.set()

        async def scenario():
            scheduler.schedule(("k1",), first_task)
            await first_started.wait()
            scheduler.schedule(("k2",), second_task)
            # second_task must not have started yet - the semaphore (max_concurrent=1) is held by
            # first_task - but is_building() must already report True for it regardless.
            still_building_k2 = scheduler.is_building(("k2",))
            second_not_started_yet = not second_started.is_set()
            first_release.set()
            await scheduler.wait_all()
            return still_building_k2, second_not_started_yet

        still_building_k2, second_not_started_yet = asyncio.run(scenario())
        assert still_building_k2 is True
        assert second_not_started_yet is True

    def test_second_task_runs_only_after_first_completes(self):
        scheduler = EmbeddingScheduler(max_concurrent=1)
        order: list[str] = []
        first_release = asyncio.Event()

        async def first_task():
            await first_release.wait()
            order.append("first")

        async def second_task():
            order.append("second")

        async def scenario():
            scheduler.schedule(("k1",), first_task)
            scheduler.schedule(("k2",), second_task)
            await asyncio.sleep(0)  # let both schedule() calls' tasks start running/queue
            first_release.set()
            await scheduler.wait_all()

        asyncio.run(scenario())
        assert order == ["first", "second"]

    def test_none_max_concurrent_is_unbounded_default_behaviour(self):
        """max_concurrent=None (the default) must not change any existing behaviour."""
        scheduler = EmbeddingScheduler()
        assert asyncio.run(_run_and_check_unbounded(scheduler)) is True


async def _run_and_check_unbounded(scheduler: EmbeddingScheduler) -> bool:
    both_started = asyncio.Event()
    first_started = asyncio.Event()
    second_started = asyncio.Event()

    async def first_task():
        first_started.set()
        if second_started.is_set():
            both_started.set()
        await both_started.wait()

    async def second_task():
        second_started.set()
        if first_started.is_set():
            both_started.set()
        await both_started.wait()

    scheduler.schedule(("k1",), first_task)
    scheduler.schedule(("k2",), second_task)
    await asyncio.wait_for(both_started.wait(), timeout=5)
    await scheduler.wait_all()
    return True
