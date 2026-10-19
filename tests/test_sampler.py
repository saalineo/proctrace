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

    assert isinstance(samples, list)
    for sample in samples:
        assert isinstance(sample, tuple)
        assert len(sample) == 4
        t_ns, rss, vms, fds = sample
        assert isinstance(t_ns, int) and t_ns >= 0
        assert isinstance(rss, int) and rss > 0
        assert isinstance(vms, int) and vms > 0
        assert isinstance(fds, int) and fds > 0

    # Verify timestamps are monotonically increasing across consecutive samples
    for i in range(1, len(samples)):
        assert samples[i][0] > samples[i - 1][0], (
            f"Expected strictly increasing timestamps, but sample {i} ({samples[i][0]}) "
            f"<= sample {i-1} ({samples[i-1][0]})"
        )


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


def test_sampler_default_capacity():
    from proctrace._proctrace_core import DEFAULT_SAMPLE_CAPACITY
    assert BackgroundSampler.DEFAULT_CAPACITY == 65_536
    assert DEFAULT_SAMPLE_CAPACITY == 65_536


def test_sampler_samples_callable_multiple_times():
    sampler = BackgroundSampler()
    sampler.start(5)
    time.sleep(0.03)
    # Can query samples while running without stall
    samples_running = sampler.samples()
    assert len(samples_running) >= 1

    sampler.stop()
    s1 = sampler.samples()
    s2 = sampler.samples()
    assert s1 == s2
    assert len(s1) >= len(samples_running)

