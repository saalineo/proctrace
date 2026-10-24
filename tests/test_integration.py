from __future__ import annotations

import asyncio
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from conftest import allocate_dirty_mb

import proctrace
from proctrace.diagnostics import install_signal_handler
from proctrace.ipc import ipc_report, trace_ipc, trace_pipe
from proctrace.telemetry import ProctraceLogger, probe


class TestWatchIntegration:
    def test_full_watch_captures_all_dimensions(self, tmp_path):
        """Verify that watch() captures memory, fd, and thread deltas simultaneously."""
        with proctrace.watch(memory=True, fds=True, threads=True) as probe:
            # Memory: allocate 30 MB, kept alive for the whole block
            _ = allocate_dirty_mb(30)
            time.sleep(0.15)  # let sampler see peak

            # FDs: open 4 files without closing
            leaked_files = [open(tmp_path / f"leak{i}.txt", "w") for i in range(4)]  # noqa: SIM115

            # Threads: start 3 threads
            threads = []
            for i in range(3):
                t = threading.Thread(
                    target=lambda: time.sleep(10), daemon=True, name=f"IT-{i}"
                )
                t.start()
                threads.append(t)
            time.sleep(0.05)  # let threads register

        result = probe.result
        assert result is not None
        assert result.rss_delta_bytes > 15 * 1024 * 1024, "Expected >15MB RSS delta"
        assert result.peak_rss_bytes >= result.rss_delta_bytes
        assert len(result.leaked_fds) >= 4, (
            f"Expected ≥4 leaked fds, got {result.leaked_fds}"
        )
        assert result.thread_delta >= 3, (
            f"Expected ≥3 thread delta, got {result.thread_delta}"
        )

        # Cleanup
        for f in leaked_files:
            f.close()


class TestDecoratorIntegration:
    def test_sync_decorator_end_to_end(self):
        pinned: list = []

        @probe(memory=True, fds=False, threads=False, output="none")
        def allocate_and_return(n_mb: int) -> int:
            buf = allocate_dirty_mb(n_mb)
            pinned.append(buf)  # keep alive until the watcher exits
            return len(buf)

        result = allocate_and_return(10)
        assert result == 10 * 1024 * 1024
        assert allocate_and_return.last_probe_result is not None
        assert allocate_and_return.last_probe_result.rss_delta_bytes > 5 * 1024 * 1024

    def test_async_decorator_end_to_end(self):
        @probe(memory=True, fds=False, threads=False, output="none")
        async def async_allocate(n_mb: int) -> str:
            buf = bytearray(n_mb * 1024 * 1024)
            await asyncio.sleep(0.01)
            return f"allocated {len(buf)} bytes"

        result = asyncio.run(async_allocate(15))
        assert "allocated" in result
        assert async_allocate.last_probe_result is not None


class TestSignalHandlerIntegration:
    def test_sigusr1_triggers_thread_dump(self, tmp_path):
        output_file = str(tmp_path / "dump.txt")
        install_signal_handler("SIGUSR1", output=output_file)

        # Start a named thread to appear in the dump
        t = threading.Thread(
            target=lambda: time.sleep(10), name="TargetThread", daemon=True
        )
        t.start()
        time.sleep(0.05)

        # Send SIGUSR1 to ourselves
        os.kill(os.getpid(), signal.SIGUSR1)
        time.sleep(0.3)  # wait for watcher thread to process

        content = open(output_file).read()  # noqa: SIM115
        assert "TargetThread" in content
        assert "proctrace thread dump" in content


class TestIPCIntegration:
    def test_queue_and_pipe_report(self):
        # Queue tracing
        q = queue.Queue(maxsize=50)
        tq = trace_ipc(q, name="integration-queue")

        for i in range(20):
            tq.put(i)
        for _ in range(20):
            tq.get()

        # Pipe tracing
        r_fd, w_fd = os.pipe()
        tp = trace_pipe(r_fd, w_fd, name="integration-pipe")

        for i in range(5):
            tp.write(b"hello")
        for _ in range(5):
            tp.read(5)

        os.close(r_fd)
        os.close(w_fd)

        report = ipc_report()
        assert "integration-queue" in report
        assert "integration-pipe" in report
        assert "msgs" in report or "µs" in report


class TestCLIIntegration:
    def test_run_json_output(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "proctrace",
                "run",
                "--json",
                "--",
                sys.executable,
                "-c",
                "x = list(range(100000))",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert "rss_delta_bytes" in data
        assert "elapsed_ms" in data
        assert data["exit_code"] == 0

    def test_watch_duration(self):
        start = time.monotonic()
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "proctrace",
                "watch",
                "--pid",
                str(os.getpid()),
                "--duration",
                "1.5",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        elapsed = time.monotonic() - start
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert elapsed < 4.0, "watch should finish within 4 seconds for a 1.5s duration"


class TestLoggerIntegration:
    def test_full_logger_pipeline(self, tmp_path):
        """Logger → watcher → delta → JSONL → parse → assert coherent."""
        log_path = str(tmp_path / "test.jsonl")

        with ProctraceLogger(log_path, min_rss_delta_mb=0.0) as logger:
            for i in range(3):
                with proctrace.watch(memory=True, fds=False, threads=False) as w:
                    _ = bytearray((i + 1) * 2 * 1024 * 1024)  # 2, 4, 6 MB
                logger.log(w.result, label=f"step-{i}")

        lines = Path(log_path).read_text().splitlines()
        assert len(lines) == 3

        for i, line in enumerate(lines):
            entry = json.loads(line)
            assert "timestamp_iso" in entry
            assert entry["label"] == f"step-{i}"
            assert "rss_delta_mb" in entry
            assert "elapsed_ms" in entry
