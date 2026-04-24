"""Unit tests for the cross-process progress bridge."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kairos.dashboard.api import progress


@pytest.fixture(autouse=True)
def _reset_progress():
    progress.reset_for_tests()
    yield
    progress.reset_for_tests()


async def test_subscribe_publish_unsubscribe_roundtrip():
    """Round-trip: publish through the manager queue, receive via asyncio.Queue."""
    loop = asyncio.get_running_loop()
    progress.set_loop(loop)
    q = progress.subscribe("run_abc")

    progress.publish("run_abc", {"type": "progress", "pct": 10.0, "stage": "x"})
    progress.publish("run_abc", {"type": "done", "metrics": {"cagr": 0.1}})
    progress.publish("run_other", {"type": "progress", "pct": 50.0, "stage": "y"})

    results: list[dict[str, Any]] = []
    for _ in range(2):
        item = await asyncio.wait_for(q.get(), timeout=10.0)
        results.append(item)

    types = [r["type"] for r in results]
    assert types == ["progress", "done"]
    assert results[0]["pct"] == 10.0
    assert results[1]["metrics"] == {"cagr": 0.1}

    progress.unsubscribe("run_abc", q)
    progress.publish("run_abc", {"type": "progress", "pct": 99.0, "stage": "z"})
    # Queue should stay empty — give the drainer a moment to process + drop.
    await asyncio.sleep(0.3)
    assert q.qsize() == 0


async def test_subscribe_starts_single_drainer_for_multiple_runs():
    loop = asyncio.get_running_loop()
    progress.set_loop(loop)
    q1 = progress.subscribe("r1")
    q2 = progress.subscribe("r2")
    assert q1 is not q2

    progress.publish("r1", {"type": "progress", "pct": 1.0, "stage": "a"})
    progress.publish("r2", {"type": "progress", "pct": 2.0, "stage": "b"})

    m1 = await asyncio.wait_for(q1.get(), timeout=10.0)
    m2 = await asyncio.wait_for(q2.get(), timeout=10.0)
    assert m1["pct"] == 1.0
    assert m2["pct"] == 2.0
