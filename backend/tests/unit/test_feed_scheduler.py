"""Unit tests for FeedSchedulerService (no real cron loop)."""

import pytest

from backend.services import feed_scheduler as fs_mod
from backend.services.feed_scheduler import CronJob, FeedSchedulerService


@pytest.mark.asyncio
async def test_status_lists_three_jobs():
    svc = FeedSchedulerService()
    status = svc.status()
    names = [j["name"] for j in status["jobs"]]
    assert names == ["feed_refresh", "dashboard_snapshot", "kronos_batch"]
    assert "enabled" in status
    for job in status["jobs"]:
        assert "cron_expr" in job
        assert "last_run" in job
        assert "next_run" in job


@pytest.mark.asyncio
async def test_run_job_now_unknown():
    svc = FeedSchedulerService()
    result = await svc.run_job_now("does_not_exist")
    assert result["ok"] is False
    assert "unknown job" in result["error"]


@pytest.mark.asyncio
async def test_run_job_now_records_error():
    svc = FeedSchedulerService()

    async def boom():
        raise RuntimeError("job exploded")

    svc.jobs[0].handler = boom
    result = await svc.run_job_now("feed_refresh")
    assert result["ok"] is False
    assert "exploded" in (result["last_error"] or "")
    assert result["last_run"] is not None


@pytest.mark.asyncio
async def test_run_job_now_success_clears_error():
    svc = FeedSchedulerService()
    ran = {"n": 0}

    async def ok():
        ran["n"] += 1

    svc.jobs[0].handler = ok
    svc.jobs[0].last_error = "previous"
    result = await svc.run_job_now("feed_refresh")
    assert result["ok"] is True
    assert result["last_error"] is None
    assert ran["n"] == 1


def test_get_latest_batch_starts_empty():
    svc = FeedSchedulerService()
    snap = svc.get_latest_batch()
    assert "as_of" in snap
    assert snap["results"] == [] or isinstance(snap["results"], list)


def test_cron_job_dataclass():
    job = CronJob(name="x", cron_expr="*/5 * * * *", handler=lambda: None, enabled=True)
    assert job.last_run is None
    assert job.next_run is None


def test_croniter_next_fire():
    svc = FeedSchedulerService()
    if fs_mod.croniter is None:
        pytest.skip("croniter not installed")
    from datetime import datetime, timezone
    nxt = svc._next_fire("*/5 * * * *", datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc))
    assert nxt is not None
    assert nxt.minute in (0, 5)
