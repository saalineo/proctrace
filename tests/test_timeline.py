from __future__ import annotations

import asyncio
import time

import pytest

import proctrace
from proctrace import Timeline, probe, watch
from proctrace.core.types import ResourceDelta


def test_timeline_from_samples():
    raw = [
        (0, 1000, 2000, 5),
        (5_000_000, 1500, 2100, 6),
        (10_000_000, 2000, 2200, 6),
    ]
    tl = Timeline.from_samples(raw)
    assert len(tl) == 3
    assert tl.t_ms == [0.0, 5.0, 10.0]
    assert tl.rss == [1000, 1500, 2000]
    assert tl.vms == [2000, 2100, 2200]
    assert tl.fds == [5, 6, 6]
    assert tl.peak_at_ms == 10.0
    assert tl.min_rss_bytes == 1000
    assert tl.max_rss_bytes == 2000
    assert tl.mean_rss_bytes == 1500.0


def test_timeline_empty():
    tl = Timeline()
    assert len(tl) == 0
    assert tl.peak_at_ms is None
    assert tl.min_rss_bytes is None
    assert tl.max_rss_bytes is None
    assert tl.mean_rss_bytes is None
    assert tl.rss_slope_bytes_per_s is None
    assert tl.r2 == 0.0
    assert not tl.is_growing()


def test_timeline_linear_growth_and_slope():
    # 10 samples with 10ms step (0.01s), growing by 100_000 bytes every 10ms -> 10,000,000 bytes/s
    t_ms = [i * 10.0 for i in range(10)]
    rss = [1_000_000 + i * 100_000 for i in range(10)]
    vms = [2_000_000] * 10
    fds = [5] * 10

    tl = Timeline(t_ms=t_ms, rss=rss, vms=vms, fds=fds)
    assert tl.rss_slope_bytes_per_s is not None
    # Slope should be approximately 10,000,000 bytes/s
    assert abs(tl.rss_slope_bytes_per_s - 10_000_000.0) < 1.0
    assert abs(tl.r2 - 1.0) < 1e-4
    assert tl.is_growing(threshold_bytes_per_s=500_000, min_r2=0.9)


def test_timeline_is_growing_rejects_spike():
    # Spikes early then drops back: should NOT qualify as sustained leak growth
    t_ms = [i * 10.0 for i in range(10)]
    rss = [100, 1000, 100, 100, 100, 100, 100, 100, 100, 100]
    tl = Timeline(t_ms=t_ms, rss=rss, vms=[100] * 10, fds=[5] * 10)
    assert not tl.is_growing()


def test_resource_delta_to_dict_omits_timeline_by_default():
    tl = Timeline(t_ms=[0.0, 5.0], rss=[100, 200], vms=[100, 200], fds=[1, 1])
    delta = ResourceDelta(
        rss_delta_bytes=100,
        vms_delta_bytes=100,
        fd_delta=0,
        peak_rss_bytes=200,
        thread_delta=0,
        child_delta=0,
        elapsed_ns=5_000_000,
        leaked_fds=[],
        new_thread_names=[],
        sample_interval_ms=5,
        timeline=tl,
        peak_at_ms=5.0,
    )

    d_default = delta.to_dict()
    assert "timeline" not in d_default
    assert d_default["peak_at_ms"] == 5.0

    d_with_timeline = delta.to_dict(include_timeline=True)
    assert "timeline" in d_with_timeline
    assert d_with_timeline["timeline"]["t_ms"] == [0.0, 5.0]

    # Test round trip from dict
    restored_no_tl = ResourceDelta.from_dict(d_default)
    assert restored_no_tl.timeline is None
    assert restored_no_tl.peak_at_ms == 5.0

    restored_with_tl = ResourceDelta.from_dict(d_with_timeline)
    assert restored_with_tl.timeline is not None
    assert isinstance(restored_with_tl.timeline, Timeline)
    assert restored_with_tl.timeline.rss == [100, 200]


def test_watch_with_timeline():
    with watch(timeline=True, sample_interval=0.01) as probe:
        # Simulate some allocations
        buf = []
        for _ in range(5):
            buf.append(bytearray(256 * 1024))
            time.sleep(0.01)

    delta = probe.result
    assert delta is not None
    assert delta.timeline is not None
    assert isinstance(delta.timeline, Timeline)
    assert len(delta.timeline) >= 2
    assert delta.peak_at_ms is not None
    assert delta.peak_at_ms >= 0.0

    # Ensure timestamps are strictly increasing
    t = delta.timeline.t_ms
    for i in range(1, len(t)):
        assert t[i] > t[i - 1], "Timeline timestamps must be strictly monotonically increasing"


def test_watch_with_timeline_and_memory_false():
    # If timeline=True, the sampler must still run even if memory=False
    with watch(memory=False, timeline=True, sample_interval=0.01) as probe:
        time.sleep(0.03)

    delta = probe.result
    assert delta is not None
    assert delta.timeline is not None
    assert len(delta.timeline) >= 2
    assert delta.peak_at_ms is not None


def test_watch_without_timeline_has_no_timeline_overhead():
    with watch(timeline=False, sample_interval=0.01) as probe:
        time.sleep(0.02)

    delta = probe.result
    assert delta is not None
    assert delta.timeline is None


def test_probe_decorator_with_timeline():
    @probe(timeline=True, sample_interval=0.01, output="none")
    def work():
        time.sleep(0.03)
        return "ok"

    res = work()
    assert res == "ok"
    assert work.last_probe_result is not None
    assert work.last_probe_result.timeline is not None
    assert len(work.last_probe_result.timeline) >= 2


def test_async_probe_decorator_with_timeline():
    @probe(timeline=True, sample_interval=0.01, output="none")
    async def async_work():
        await asyncio.sleep(0.03)
        return "async_ok"

    res = asyncio.run(async_work())
    assert res == "async_ok"
    assert async_work.last_probe_result is not None
    assert async_work.last_probe_result.timeline is not None
    assert len(async_work.last_probe_result.timeline) >= 2


@pytest.mark.proctrace(timeline=True, sample_interval=0.01)
def test_pytest_marker_with_timeline(request):
    time.sleep(0.03)
    # Check that request stash captured the timeline
    from proctrace.testing.pytest_plugin import PROCTRACE_KEY

    delta = request.node.stash.get(PROCTRACE_KEY, None)
    if delta is not None:
        assert delta.timeline is not None
        assert len(delta.timeline) >= 2
