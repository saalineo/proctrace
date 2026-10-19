from __future__ import annotations

import json
import os
import signal
import sys
import time
import types
from unittest.mock import MagicMock, patch

import pytest

from proctrace.cli import (
    _read_proc_mem,
    cmd_dump,
    cmd_plot,
    cmd_run,
    cmd_watch,
    main,
)

def _ns(subcommand: str, **kwargs) -> types.SimpleNamespace:
    defaults = {
        "run":   {"command": [], "json": False, "memory": True, "fds": False},
        "dump":  {"pid": os.getpid(), "signal": "SIGUSR1"},
        "watch": {"pid": os.getpid(), "interval": 0.1, "duration": 0.3, "timeline": None},
        "plot":  {"timeline_file": "", "output": "", "title": "proctrace Resource Timeline"},
    }
    ns = types.SimpleNamespace(subcommand=subcommand, **defaults[subcommand])
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _fake_snapshot(rss=0, vms=0, fds=0):
    snap = MagicMock()
    snap.rss_bytes = rss
    snap.vms_bytes = vms
    snap.fd_count = fds
    return snap

class TestCmdRun:
    def test_no_command_returns_1(self, capsys):
        args = _ns("run", command=[])
        rc = cmd_run(args)
        assert rc == 1
        captured = capsys.readouterr()
        assert "no command specified" in captured.err

    def test_run_python_pass_exits_0(self, capsys):
        """proctrace run -- python -c 'pass' should exit 0 and print report."""
        args = _ns("run", command=[sys.executable, "-c", "pass"], json=False)
        with patch("proctrace._proctrace_core.snapshot_resources") as mock_snap:
            mock_snap.side_effect = [_fake_snapshot(rss=10 * 1024 * 1024),
                                     _fake_snapshot(rss=10 * 1024 * 1024)]
            rc = cmd_run(args)

        assert rc == 0
        captured = capsys.readouterr()
        assert "[proctrace]" in captured.err
        assert "exit code" in captured.err
        assert "rss delta" in captured.err
        assert "elapsed" in captured.err

    def test_run_json_output_is_valid_json(self, capsys):
        args = _ns("run", command=[sys.executable, "-c", "pass"], json=True)
        with patch("proctrace._proctrace_core.snapshot_resources") as mock_snap:
            mock_snap.side_effect = [_fake_snapshot(rss=0), _fake_snapshot(rss=0)]
            rc = cmd_run(args)

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["exit_code"] == 0
        assert "rss_delta_bytes" in data
        assert "rss_delta_mb" in data
        assert "elapsed_ms" in data
        assert data["command"] == [sys.executable, "-c", "pass"]

    def test_run_json_nothing_on_stderr(self, capsys):
        """JSON mode should not print the human report to stderr."""
        args = _ns("run", command=[sys.executable, "-c", "pass"], json=True)
        with patch("proctrace._proctrace_core.snapshot_resources") as mock_snap:
            mock_snap.side_effect = [_fake_snapshot(), _fake_snapshot()]
            cmd_run(args)

        captured = capsys.readouterr()
        # stderr should be empty when --json is used
        assert captured.err == ""

    def test_run_propagates_child_exit_code(self, capsys):
        """Exit code from child propagates through cmd_run return value."""
        args = _ns("run", command=[sys.executable, "-c", "raise SystemExit(42)"])
        with patch("proctrace._proctrace_core.snapshot_resources") as mock_snap:
            mock_snap.side_effect = [_fake_snapshot(), _fake_snapshot()]
            rc = cmd_run(args)
        assert rc == 42


# ──────────────────────────────────────────────────────────────────────────────
# cmd_dump
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdDump:
    def test_dump_self_exits_0(self, capsys):
        """Sending SIGUSR1 to our own PID should succeed (we handle it or ignore it)."""
        # Install a no-op handler so we don't die on SIGUSR1
        old = signal.signal(signal.SIGUSR1, lambda *_: None)
        try:
            args = _ns("dump", pid=os.getpid(), signal="SIGUSR1")
            rc = cmd_dump(args)
        finally:
            signal.signal(signal.SIGUSR1, old)

        assert rc == 0
        captured = capsys.readouterr()
        assert "Sent SIGUSR1" in captured.err

    def test_dump_unknown_signal_returns_1(self, capsys):
        args = _ns("dump")
        args.signal = "SIGNOTREAL"
        rc = cmd_dump(args)
        assert rc == 1
        assert "Unknown signal" in capsys.readouterr().err

    def test_dump_nonexistent_pid_returns_1(self, capsys):
        args = _ns("dump", pid=99999999, signal="SIGUSR1")
        rc = cmd_dump(args)
        assert rc == 1
        captured = capsys.readouterr()
        assert "No process" in captured.err or "Permission denied" in captured.err

    def test_dump_permission_error(self, capsys):
        args = _ns("dump", pid=1, signal="SIGUSR1")
        with patch("os.kill", side_effect=PermissionError):
            rc = cmd_dump(args)
        assert rc == 1
        assert "Permission denied" in capsys.readouterr().err

    def test_dump_negative_pid_returns_1(self, capsys):
        args = _ns("dump", pid=-1, signal="SIGUSR1")
        rc = cmd_dump(args)
        assert rc == 1
        assert "Invalid PID" in capsys.readouterr().err


# ──────────────────────────────────────────────────────────────────────────────
# cmd_watch
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdWatch:
    def test_watch_self_runs_and_exits(self, capsys):
        """watch on our own PID for 0.3 s should exit 0 with tabular output."""
        args = _ns("watch", pid=os.getpid(), interval=0.1, duration=0.3)
        t0 = time.monotonic()
        rc = cmd_watch(args)
        elapsed = time.monotonic() - t0

        assert rc == 0
        assert 0.2 <= elapsed <= 2.0  # ran for roughly the right amount of time
        captured = capsys.readouterr()
        assert "proctrace watch" in captured.err
        assert "RSS MB" in captured.err

    def test_watch_exits_on_dead_pid(self, capsys):
        """If /proc/<pid>/status is missing, watch should print 'exited' and return 0."""
        args = _ns("watch", pid=99999999, interval=0.05, duration=5.0)
        rc = cmd_watch(args)
        assert rc == 0
        assert "exited" in capsys.readouterr().err

    def test_watch_displays_slope_and_peak_timing_in_summary(self, capsys):
        """proctrace watch should display slope and peak timing in its summary output."""
        args = _ns("watch", pid=os.getpid(), interval=0.05, duration=0.2)
        rc = cmd_watch(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "[proctrace watch summary]" in captured.err
        assert "Peak RSS :" in captured.err
        assert "RSS Slope:" in captured.err
        assert "ms)" in captured.err

    def test_watch_saves_timeline_to_json(self, capsys, tmp_path):
        """proctrace watch --timeline <out.json> saves valid timeline JSON."""
        out_json = tmp_path / "timeline.json"
        args = _ns("watch", pid=os.getpid(), interval=0.05, duration=0.2, timeline=str(out_json))
        rc = cmd_watch(args)
        assert rc == 0
        assert out_json.is_file()

        data = json.loads(out_json.read_text(encoding="utf-8"))
        assert "t_ms" in data
        assert "rss" in data
        assert "vms" in data
        assert "fds" in data
        assert len(data["t_ms"]) > 0

    def test_watch_negative_pid_returns_1(self, capsys):
        args = _ns("watch", pid=-5, interval=0.05, duration=1.0)
        rc = cmd_watch(args)
        assert rc == 1
        assert "Invalid PID" in capsys.readouterr().err



# ──────────────────────────────────────────────────────────────────────────────
# cmd_plot
# ──────────────────────────────────────────────────────────────────────────────

class TestCmdPlot:
    def test_plot_missing_input_file(self, capsys, tmp_path):
        missing = tmp_path / "missing.json"
        out_html = tmp_path / "out.html"
        args = _ns("plot", timeline_file=str(missing), output=str(out_html))
        rc = cmd_plot(args)
        assert rc == 1
        assert "file not found" in capsys.readouterr().err

    def test_plot_invalid_json(self, capsys, tmp_path):
        bad_json = tmp_path / "bad.json"
        bad_json.write_text("invalid json { content", encoding="utf-8")
        out_html = tmp_path / "out.html"
        args = _ns("plot", timeline_file=str(bad_json), output=str(out_html))
        rc = cmd_plot(args)
        assert rc == 1
        assert "failed to parse" in capsys.readouterr().err

    def test_plot_generates_html_and_svg(self, capsys, tmp_path):
        tl_data = {
            "t_ms": [0.0, 10.0, 20.0],
            "rss": [1000000, 2000000, 1500000],
            "vms": [5000000, 6000000, 5500000],
            "fds": [4, 5, 4],
        }
        json_file = tmp_path / "samples.json"
        json_file.write_text(json.dumps(tl_data), encoding="utf-8")

        html_out = tmp_path / "chart.html"
        args_html = _ns("plot", timeline_file=str(json_file), output=str(html_out))
        rc_html = cmd_plot(args_html)
        assert rc_html == 0
        assert html_out.is_file()
        html_content = html_out.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in html_content
        assert "<svg" in html_content
        assert '<script type="application/json" id="proctrace-timeline-data">' in html_content

        svg_out = tmp_path / "chart.svg"
        args_svg = _ns("plot", timeline_file=str(json_file), output=str(svg_out))
        rc_svg = cmd_plot(args_svg)
        assert rc_svg == 0
        assert svg_out.is_file()
        svg_content = svg_out.read_text(encoding="utf-8")
        assert "<svg" in svg_content
        assert 'class="peak-marker"' in svg_content
        assert '<script type="application/json" id="proctrace-timeline-data">' in svg_content



# ──────────────────────────────────────────────────────────────────────────────
# _read_proc_mem
# ──────────────────────────────────────────────────────────────────────────────

class TestReadProcMem:
    def test_reads_self(self):
        rss_mb, vms_mb = _read_proc_mem(os.getpid())
        assert rss_mb > 0
        assert vms_mb > 0

    def test_dead_pid_raises(self):
        with pytest.raises(ProcessLookupError):
            _read_proc_mem(99999999)

    def test_negative_pid_raises(self):
        with pytest.raises(ProcessLookupError):
            _read_proc_mem(-1)


# ──────────────────────────────────────────────────────────────────────────────
# main() integration via subprocess
# ──────────────────────────────────────────────────────────────────────────────

class TestMainIntegration:
    """Invoke cli.main() through subprocess so we get a real argv parse."""

    def _run(self, *argv: str) -> tuple[int, str, str]:
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "proctrace.cli", *argv],
            capture_output=True,
            text=True,
        )
        return result.returncode, result.stdout, result.stderr

    def test_run_python_pass(self):
        rc, out, err = self._run("run", "--", sys.executable, "-c", "pass")
        assert rc == 0
        assert "[proctrace]" in err

    def test_run_json_flag(self):
        rc, out, err = self._run("run", "--json", "--", sys.executable, "-c", "pass")
        assert rc == 0
        data = json.loads(out)
        assert data["exit_code"] == 0

    def test_dump_self(self):
        import subprocess as _sp
        # Spawn a long-lived subprocess that ignores SIGUSR1 so the signal is safe.
        target = _sp.Popen(
            [sys.executable, "-c",
             "import signal, time; signal.signal(signal.SIGUSR1, signal.SIG_IGN); time.sleep(30)"],
        )
        try:
            rc, out, err = self._run("dump", "--pid", str(target.pid))
            assert rc == 0
            assert "Sent SIGUSR1" in err
        finally:
            target.kill()
            target.wait()

    def test_watch_duration(self):
        t0 = time.monotonic()
        rc, out, err = self._run(
            "watch", "--pid", str(os.getpid()), "--duration", "0.5", "--interval", "0.1"
        )
        elapsed = time.monotonic() - t0
        assert rc == 0
        assert elapsed < 5.0  # should not hang

    def test_watch_timeline_flag_via_cli(self, tmp_path):
        out_json = tmp_path / "cli_timeline.json"
        rc, out, err = self._run(
            "watch",
            "--pid",
            str(os.getpid()),
            "--duration",
            "0.3",
            "--interval",
            "0.05",
            "--timeline",
            str(out_json),
        )
        assert rc == 0
        assert "Peak RSS :" in err
        assert "RSS Slope:" in err
        assert out_json.is_file()

        data = json.loads(out_json.read_text(encoding="utf-8"))
        assert len(data["t_ms"]) > 0
        assert len(data["rss"]) == len(data["t_ms"])

    def test_plot_via_cli(self, tmp_path):
        # Create a timeline file
        out_json = tmp_path / "sample_timeline.json"
        tl_data = {
            "t_ms": [0.0, 10.0, 20.0],
            "rss": [1000000, 2000000, 1500000],
            "vms": [5000000, 6000000, 5500000],
            "fds": [3, 4, 3],
        }
        out_json.write_text(json.dumps(tl_data), encoding="utf-8")

        # 1. HTML output
        out_html = tmp_path / "plot.html"
        rc, out, err = self._run("plot", str(out_json), "-o", str(out_html))
        assert rc == 0
        assert out_html.is_file()
        assert "<!DOCTYPE html>" in out_html.read_text(encoding="utf-8")

        # 2. SVG output
        out_svg = tmp_path / "plot.svg"
        rc, out, err = self._run("plot", str(out_json), "-o", str(out_svg))
        assert rc == 0
        assert out_svg.is_file()
        assert "<svg" in out_svg.read_text(encoding="utf-8")

