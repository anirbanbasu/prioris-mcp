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

    def test_scheduling_the_same_key_twice_while_running_is_a_no_op(self):
        scheduler = EmbeddingScheduler()
        call_count = 0

        async def task():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)

        async def scenario():
            scheduler.schedule(("k",), task)
            scheduler.schedule(("k",), task)  # same key, already in flight - ignored
            await scheduler.wait_all()

        asyncio.run(scenario())
        assert call_count == 1

    def test_a_failing_task_still_clears_is_building(self):
        scheduler = EmbeddingScheduler()

        async def failing_task():
            raise RuntimeError("embedding failed")

        async def scenario():
            scheduler.schedule(("k",), failing_task)
            await scheduler.wait_all()
            return scheduler.is_building(("k",))

        assert asyncio.run(scenario()) is False
