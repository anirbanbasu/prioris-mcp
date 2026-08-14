import asyncio
import contextlib

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
            scheduler.cancel(("k",))
            release.set()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            return task.cancelled(), scheduler.is_building(("k",))

        cancelled, still_building_after = asyncio.run(scenario())
        assert cancelled is True
        assert still_building_after is False
        assert ran_past_wait is False

    def test_cancel_unknown_key_is_a_no_op(self):
        scheduler = EmbeddingScheduler()
        scheduler.cancel(("never-scheduled",))  # must not raise


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
