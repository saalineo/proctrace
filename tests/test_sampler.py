from __future__ import annotations

import time

from proctrace._proctrace_core import BackgroundSampler


def test_sampler_records_monotonic_samples():
    sampler = BackgroundSampler()
    sampler.start(5)
    time.sleep(0.04)
    peak = sampler.stop()
    assert peak > 0

    samples = sampler.samples()
    assert len(samples) >= 2, f"Expected at least 2 samples, got {len(samples)}"

    t_ns_prev = -1
    for t_ns, rss, vms, fds in samples:
        assert t_ns >= t_ns_prev, "Timestamps must be monotonically non-decreasing"
        assert rss > 0, "RSS must be positive"
        assert vms > 0, "VMS must be positive"
        assert fds > 0, "FD count must be positive"
        t_ns_prev = t_ns


def test_sampler_decimation_bounds_samples():
    cap = 4
    sampler = BackgroundSampler(cap)
    sampler.start(2)
    time.sleep(0.06)
    sampler.stop()

    samples = sampler.samples()
    assert len(samples) <= cap, f"Sample count {len(samples)} exceeded capacity {cap}"
    assert len(samples) > 0


def test_sampler_stop_unparks_promptly():
    sampler = BackgroundSampler()
    sampler.start(1000)  # 1-second interval
    time.sleep(0.01)

    start = time.perf_counter()
    sampler.stop()
    elapsed = time.perf_counter() - start

    # Stop must unpark immediately without waiting out the 1-second interval (< 50ms)
    assert elapsed < 0.05, f"stop() took {elapsed*1000:.2f}ms, expected < 50ms"


def test_sampler_peak_at_ns():
    sampler = BackgroundSampler()
    sampler.start(5)
    time.sleep(0.03)
    peak = sampler.stop()
    assert peak > 0
    assert sampler.peak_at_ns() >= 0
    assert sampler.peak_rss() == peak
