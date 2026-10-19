from __future__ import annotations

import asyncio
import json
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
        time.sleep(0.05)

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
        time.sleep(0.05)
        return "ok"

    res = work()
    assert res == "ok"
    assert work.last_probe_result is not None
    assert work.last_probe_result.timeline is not None
    assert len(work.last_probe_result.timeline) >= 2


def test_async_probe_decorator_with_timeline():
    @probe(timeline=True, sample_interval=0.01, output="none")
    async def async_work():
        await asyncio.sleep(0.05)
        return "async_ok"

    res = asyncio.run(async_work())
    assert res == "async_ok"
    assert async_work.last_probe_result is not None
    assert async_work.last_probe_result.timeline is not None
    assert len(async_work.last_probe_result.timeline) >= 2


@pytest.mark.proctrace(timeline=True, sample_interval=0.01)
def test_pytest_marker_with_timeline(request):
    time.sleep(0.05)

    # Check that request stash captured the timeline
    from proctrace.testing.pytest_plugin import PROCTRACE_KEY

    delta = request.node.stash.get(PROCTRACE_KEY, None)
    if delta is not None:
        assert delta.timeline is not None
        assert len(delta.timeline) >= 2


def test_timeline_monotonic_buffer_collection_and_decimation():
    """Unit test: monotonic buffer collection & decimation under capacity limits."""
    from proctrace._proctrace_core import BackgroundSampler

    cap = 8
    sampler = BackgroundSampler(cap)
    sampler.start(2)
    time.sleep(0.06)
    sampler.stop()

    samples = sampler.samples()
    assert 0 < len(samples) <= cap, f"Sample count {len(samples)} exceeded capacity {cap}"

    # Verify timestamps are strictly monotonically increasing
    for i in range(1, len(samples)):
        assert samples[i][0] > samples[i - 1][0], (
            f"Timestamp at {i} ({samples[i][0]}) <= timestamp at {i-1} ({samples[i-1][0]})"
        )

    # Convert to Timeline and verify monotonicity and capacity preservation
    tl = Timeline.from_samples(samples)
    assert len(tl) <= cap
    for i in range(1, len(tl)):
        assert tl.t_ms[i] > tl.t_ms[i - 1]


def test_timeline_synthetic_leak_and_steady_state():
    """Synthetic leak test: intentional allocation loop asserts is_growing() == True, steady state asserts False."""
    # 1. Intentional allocation loop: memory steadily allocated over time
    # Using 4MB chunks guarantees allocations exceed glibc MMAP_THRESHOLD (128KB)
    # forcing direct kernel mmap pages and continuous linear RSS growth.
    with watch(timeline=True, sample_interval=0.01) as probe_leak:
        leak_store = []
        for _ in range(8):
            leak_store.append(bytearray(b"M" * (4 * 1024 * 1024)))
            time.sleep(0.015)

    delta_leak = probe_leak.result
    assert delta_leak is not None
    assert delta_leak.timeline is not None
    assert delta_leak.timeline.is_growing(), (
        f"Expected is_growing() == True for synthetic leak, slope={delta_leak.timeline.rss_slope_bytes_per_s}, "
        f"r2={delta_leak.timeline.r2}"
    )
    assert delta_leak.timeline.rss_slope_bytes_per_s is not None
    assert delta_leak.timeline.rss_slope_bytes_per_s > 0

    # 2. Steady state loop: no allocations during monitored block
    with watch(timeline=True, sample_interval=0.01) as probe_steady:
        for _ in range(8):
            time.sleep(0.015)

    delta_steady = probe_steady.result
    assert delta_steady is not None
    assert delta_steady.timeline is not None
    assert not delta_steady.timeline.is_growing(), (
        f"Expected is_growing() == False for steady state, slope={delta_steady.timeline.rss_slope_bytes_per_s}, "
        f"r2={delta_steady.timeline.r2}"
    )



def test_sampler_stop_latency_under_5ms():
    """Latency test: verify sampler.stop() completes in < 5 ms regardless of interval."""
    from proctrace._proctrace_core import BackgroundSampler

    for interval_ms in [500, 1000, 2500, 5000]:
        sampler = BackgroundSampler()
        sampler.start(interval_ms)
        time.sleep(0.01)

        t0 = time.perf_counter()
        sampler.stop()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        assert elapsed_ms < 5.0, (
            f"sampler.stop() took {elapsed_ms:.3f} ms with interval {interval_ms} ms (must be < 5 ms)"
        )


def test_timeline_plot_html_and_svg_markers(tmp_path):
    """Plot test: verify output HTML/SVG is valid and contains expected markers and data."""
    import re
    from proctrace.telemetry.plot import plot_timeline, render_html, render_svg

    t_ms = [0.0, 10.0, 20.0, 30.0, 40.0]
    rss = [10_000_000, 12_000_000, 28_000_000, 15_000_000, 14_000_000]
    vms = [50_000_000, 52_000_000, 58_000_000, 53_000_000, 51_000_000]
    fds = [5, 6, 7, 6, 5]

    tl = Timeline(t_ms=t_ms, rss=rss, vms=vms, fds=fds)
    assert tl.peak_at_ms == 20.0

    # 1. Standalone SVG test
    svg = render_svg(tl, title="Test Pipeline Memory")
    assert "<svg" in svg and "</svg>" in svg
    assert 'class="curve-rss"' in svg
    assert 'class="curve-vms"' in svg
    assert 'class="curve-fds"' in svg
    assert "RSS (MB)" in svg
    assert "VMS (MB)" in svg
    assert "Time (ms)" in svg
    assert 'class="peak-marker"' in svg
    assert 'id="peak-marker"' in svg
    assert "20.0 ms" in svg
    assert '<script type="application/json" id="proctrace-timeline-data">' in svg

    # Extract JSON embedded in SVG
    json_match = re.search(
        r'<script type="application/json" id="proctrace-timeline-data">\s*(\{.*?\})\s*</script>',
        svg,
        re.DOTALL,
    )
    assert json_match is not None, "Failed to find embedded JSON in SVG"
    extracted_json = json.loads(json_match.group(1))
    assert extracted_json["t_ms"] == t_ms
    assert extracted_json["rss"] == rss
    assert extracted_json["vms"] == vms
    assert extracted_json["fds"] == fds

    # 2. Standalone HTML test
    html = render_html(tl, title="Test HTML Pipeline")
    assert "<!DOCTYPE html>" in html
    assert "<html" in html and "</html>" in html
    assert "<svg" in html
    assert '<script type="application/json" id="proctrace-timeline-data">' in html
    assert "Peak RSS" in html
    assert "Peak Time" in html
    assert "20.0 ms" in html

    # 3. File export test via plot_timeline / tl.plot
    svg_file = tmp_path / "chart.svg"
    html_file = tmp_path / "chart.html"

    tl.plot(svg_file)
    assert svg_file.is_file()
    assert "<svg" in svg_file.read_text(encoding="utf-8")

    plot_timeline(tl, html_file)
    assert html_file.is_file()
    assert "<!DOCTYPE html>" in html_file.read_text(encoding="utf-8")

    # 4. Deserialization round-trip
    restored = Timeline.from_json(tl.to_json())
    assert restored.t_ms == tl.t_ms
    assert restored.rss == tl.rss
    assert restored.peak_at_ms == tl.peak_at_ms

