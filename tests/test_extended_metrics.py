from __future__ import annotations

import os
import socket
import tempfile
import threading
import time

import pytest
from conftest import allocate_dirty_mb

import proctrace
from proctrace.core import ResourceDelta


class TestCpuMetrics:
    def test_cpu_busy_loop(self):
        with proctrace.watch(cpu=True) as probe:
            start = time.monotonic()
            # Burn CPU for ~150 ms
            x = 0
            while time.monotonic() - start < 0.15:
                x = (x + 1) * 3 % 1000000

        result = probe.result
        assert result is not None
        assert result.cpu_user_s > 0.05
        assert result.cpu_percent > 30.0
        assert result.ctx_switches_vol >= 0
        assert result.minor_faults >= 0

    def test_cpu_idle_sleep(self):
        with proctrace.watch(cpu=True) as probe:
            time.sleep(0.15)

        result = probe.result
        assert result is not None
        assert result.cpu_percent < 25.0

    def test_page_faults_on_mmap(self):
        with proctrace.watch(cpu=True) as probe:
            buf = allocate_dirty_mb(10)
            time.sleep(0.01)
            del buf

        result = probe.result
        assert result is not None
        assert result.minor_faults > 100

    def test_context_switches(self):
        with proctrace.watch(cpu=True) as probe:
            # Frequent sleeps cause voluntary context switches
            for _ in range(10):
                time.sleep(0.005)

        result = probe.result
        assert result is not None
        assert result.ctx_switches_vol > 0


class TestIoMetrics:
    def test_io_metrics_on_write(self, tmp_path):
        target = tmp_path / "test_io.bin"
        chunk = b"X" * (5 * 1024 * 1024)

        with proctrace.watch(io=True) as probe:
            with open(target, "wb") as f:
                f.write(chunk)
                f.flush()
                os.fsync(f.fileno())

        result = probe.result
        assert result is not None
        # Either syscall wchar or disk write bytes should capture the 5MB write
        assert result.io_wchar_bytes >= 5 * 1024 * 1024 or result.io_write_bytes > 0


class TestSocketMetrics:
    def test_socket_state_tracking(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(5)
        port = server.getsockname()[1]

        with proctrace.watch(sockets=True) as probe:
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.connect(("127.0.0.1", port))
            conn, _ = server.accept()

        result = probe.result
        assert result is not None
        # Sockets in ESTABLISHED or LISTEN state should be detected
        assert (
            result.sockets_by_state.get("ESTABLISHED", 0) >= 1
            or result.sockets_by_state.get("LISTEN", 0) >= 1
        )

        client.close()
        conn.close()
        server.close()


class TestThreadCpuMetrics:
    def test_per_thread_cpu(self):
        stop_event = threading.Event()

        def worker():
            x = 0
            while not stop_event.is_set():
                x = (x + 1) * 3 % 1000000

        t = threading.Thread(target=worker, name="WorkerCpuTest", daemon=True)
        t.start()

        with proctrace.watch(threads_cpu=True) as probe:
            time.sleep(0.12)

        stop_event.set()
        t.join(timeout=1.0)

        result = probe.result
        assert result is not None
        assert len(result.busiest_threads) > 0
        names = [name for name, _pct in result.busiest_threads]
        assert any("Worker" in n or "tid" in n for n in names)


class TestReportingAndSerialization:
    def test_report_includes_extended_metrics(self):
        with proctrace.watch(cpu=True, io=True) as probe:
            time.sleep(0.02)

        rep = probe.result.report()
        assert "elapsed" in rep
        # Report formatting is valid string
        assert isinstance(rep, str)

    def test_json_roundtrip_extended(self):
        delta = ResourceDelta(
            rss_delta_bytes=1024,
            vms_delta_bytes=2048,
            fd_delta=1,
            peak_rss_bytes=4096,
            thread_delta=0,
            child_delta=0,
            elapsed_ns=1_000_000,
            leaked_fds=[],
            new_thread_names=[],
            cpu_user_ns=500_000,
            cpu_sys_ns=200_000,
            ctx_switches_vol=12,
            ctx_switches_invol=3,
            minor_faults=45,
            major_faults=1,
            io_read_bytes=100,
            io_write_bytes=200,
            sockets_by_state={"ESTABLISHED": 2},
            socket_deltas={"ESTABLISHED": 1},
            busiest_threads=[("worker", 45.5)],
        )

        d = delta.to_dict()
        restored = ResourceDelta.from_dict(d)

        assert restored.cpu_user_ns == 500_000
        assert restored.cpu_sys_ns == 200_000
        assert restored.ctx_switches_vol == 12
        assert restored.sockets_by_state == {"ESTABLISHED": 2}
        assert restored.busiest_threads == [("worker", 45.5)]
        assert abs(restored.cpu_user_s - 0.0005) < 1e-6
