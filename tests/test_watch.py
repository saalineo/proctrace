from __future__ import annotations

import threading
import time

from conftest import allocate_dirty_mb

import proctrace
from proctrace._types import ResourceDelta


class TestMemoryTracking:
    def test_positive_rss_delta_on_allocation(self):
        with proctrace.watch(memory=True, fds=False, threads=False) as probe:
            _ = allocate_dirty_mb(20)  # 20 MB allocation, kept alive by `_`
        assert probe.result is not None
        # RSS delta should be at least 15 MB (OS may not page everything in)
        assert probe.result.rss_delta_bytes > 15 * 1024 * 1024, (
            f"Expected >15MB RSS delta, got {probe.result.rss_delta_mb:.1f}MB"
        )

    def test_peak_rss_exceeds_or_equals_final_delta(self):
        with proctrace.watch(memory=True, fds=False, threads=False) as probe:
            buf = allocate_dirty_mb(30)  # allocate
            time.sleep(0.15)  # let sampler see it
            del buf  # free
        assert probe.result.peak_rss_bytes >= abs(probe.result.rss_delta_bytes)

    def test_small_allocation_has_small_delta(self):
        with proctrace.watch(memory=True, fds=False, threads=False) as probe:
            _ = [1, 2, 3]

        # A tiny allocation must not produce a significant RSS increase.
        # RSS may decrease because of allocator and OS reclamation behavior.
        assert probe.result.rss_delta_bytes < 5 * 1024 * 1024, (
            f"Unexpected RSS increase: {probe.result.rss_delta_mb:.1f}MB"
        )

    def test_elapsed_is_positive(self):
        with proctrace.watch() as probe:
            time.sleep(0.05)
        assert probe.result.elapsed_ns > 0
        assert probe.result.elapsed_ms >= 40


class TestFdTracking:
    def test_fd_leak_detection(self, tmp_path):
        files = [open(tmp_path / f"f{i}", "w") for i in range(5)]

        with proctrace.watch(memory=False, fds=True, threads=False) as probe:
            leaked = [open(tmp_path / f"leak{i}", "w") for i in range(3)]

        assert probe.result.fd_delta >= 3
        assert len(probe.result.leaked_fds) >= 3

        # Cleanup
        for f in files + leaked:
            f.close()

    def test_closed_fds_not_reported_as_leaked(self, tmp_path):
        with proctrace.watch(memory=False, fds=True, threads=False) as probe:
            f = open(tmp_path / "clean.txt", "w")
            f.close()

        assert probe.result.fd_delta == 0
        assert len(probe.result.leaked_fds) == 0

    def test_duplicate_fd_leak_detection(self, tmp_path):
        target = tmp_path / "dup.txt"
        base_file = open(target, "w")

        with proctrace.watch(memory=False, fds=True, threads=False) as probe:
            dup_leaked = [open(target, "r") for _ in range(3)]

        assert probe.result.fd_delta >= 3
        assert len(probe.result.leaked_fds) >= 3
        assert probe.result.leaked_fds.count(str(target)) == 3

        for f in [base_file] + dup_leaked:
            f.close()


class TestThreadTracking:
    def test_thread_delta_detected(self):
        with proctrace.watch(memory=False, fds=False, threads=True) as probe:
            threads = []
            for i in range(3):
                t = threading.Thread(
                    target=lambda: time.sleep(5), name=f"W{i}", daemon=True
                )
                t.start()
                threads.append(t)
            time.sleep(0.05)

        assert probe.result.thread_delta >= 3
        names = probe.result.new_thread_names
        assert any(n.startswith("W") for n in names)

    def test_no_false_positive_on_existing_threads(self):
        with proctrace.watch(memory=False, fds=False, threads=True) as probe:
            time.sleep(0.05)

        assert probe.result.thread_delta == 0
        assert probe.result.new_thread_names == []


class TestResultType:
    def test_result_is_resource_delta(self):
        with proctrace.watch() as probe:
            pass
        assert isinstance(probe.result, ResourceDelta)

    def test_report_returns_string(self):
        with proctrace.watch() as probe:
            pass
        report = probe.result.report()
        assert isinstance(report, str)
        assert "rss" in report.lower() or "memory" in report.lower()

    def test_json_round_trip(self):
        import json

        with proctrace.watch() as probe:
            pass
        j = probe.result.to_json()
        d = json.loads(j)
        restored = ResourceDelta.from_dict(d)
        assert restored.rss_delta_bytes == probe.result.rss_delta_bytes
        assert restored.elapsed_ns == probe.result.elapsed_ns
        assert restored.sample_interval_ms == probe.result.sample_interval_ms

    def test_sample_interval_tracking_and_comparability(self):
        with proctrace.watch(sample_interval=0.05) as p50:
            pass
        with proctrace.watch(sample_interval=0.1) as p100:
            pass
        with proctrace.watch(sample_interval=0.05) as p50_second:
            pass

        assert p50.result.sample_interval_ms == 50
        assert p50.result.sample_interval_s == 0.05
        assert p100.result.sample_interval_ms == 100
        assert p100.result.sample_interval_s == 0.1

        assert p50.result.is_comparable_with(p50_second.result)
        assert not p50.result.is_comparable_with(p100.result)

    def test_env_var_sample_interval_override(self, monkeypatch):
        monkeypatch.setenv("PROCTRACE_SAMPLE_INTERVAL", "0.025")
        with proctrace.watch() as probe:
            pass
        assert probe.result.sample_interval_ms == 25
        assert probe.result.sample_interval_s == 0.025
