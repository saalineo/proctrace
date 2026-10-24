from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Final

from proctrace.core.exceptions import ProcessNotFoundError

_BYTES_PER_MB: Final[int] = 1024 * 1024
_NS_PER_MS: Final[int] = 1_000_000


def _read_proc_mem(pid: int) -> tuple[float, float]:
    if pid <= 0:
        raise ProcessNotFoundError(f"Invalid process id: {pid}")

    if sys.platform == "linux":
        status_path = Path(f"/proc/{pid}/status")
        try:
            status = status_path.read_text(encoding="utf-8")
        except FileNotFoundError as err:
            raise ProcessNotFoundError(f"Process {pid} terminated") from err
        except OSError as err:
            raise ProcessNotFoundError(f"Cannot inspect process {pid}: {err}") from err

        rss_kb = 0
        vms_kb = 0
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                rss_kb = int(line.split()[1])
            elif line.startswith("VmSize:"):
                vms_kb = int(line.split()[1])
        return rss_kb / 1024.0, vms_kb / 1024.0

    # Fallback on non-Linux platforms: ps reports RSS and VSZ in KB
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "rss=,vsz="],
            capture_output=True,
            text=True,
            check=False,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError) as err:
        raise ProcessNotFoundError(f"Failed to query ps for pid {pid}: {err}") from err

    if result.returncode != 0:
        raise ProcessNotFoundError(f"Process {pid} terminated")
    parts = result.stdout.strip().split()
    if len(parts) < 2:
        raise ProcessNotFoundError(f"Unexpected output from ps for pid {pid}")
    return int(parts[0]) / 1024.0, int(parts[1]) / 1024.0


def _read_proc_fds(pid: int) -> int:
    if sys.platform == "linux":
        try:
            return len(os.listdir(f"/proc/{pid}/fd"))
        except (OSError, FileNotFoundError):
            return 0
    return 0



def cmd_run(args: argparse.Namespace) -> int:      #Execute a command in a child process and report memory and time deltas
    if not args.command:
        print(
            "proctrace run: no command specified. Use: proctrace run -- cmd [args]",
            file=sys.stderr,
        )
        return 1

    from proctrace._proctrace_core import snapshot_resources

    attribution_enabled = getattr(args, "attribution", False)
    temp_dir: str | None = None
    attr_file: Path | None = None
    child_env: dict[str, str] | None = None

    if attribution_enabled:
        temp_dir = tempfile.mkdtemp(prefix="proctrace_attr_")
        attr_file = Path(temp_dir) / "attribution.json"
        sitecustomize_path = Path(temp_dir) / "sitecustomize.py"
        sitecustomize_code = (
            "import atexit\n"
            "import json\n"
            "import os\n"
            "import tracemalloc\n\n"
            "def _proctrace_dump_attribution():\n"
            "    if not tracemalloc.is_tracing():\n"
            "        return\n"
            "    snapshot = tracemalloc.take_snapshot()\n"
            "    top_n = int(os.environ.get('PROCTRACE_ATTRIBUTION_TOP', '10'))\n"
            "    out_path = os.environ.get('PROCTRACE_ATTRIBUTION_FILE')\n"
            "    if not out_path:\n"
            "        return\n"
            "    try:\n"
            "        filters = [\n"
            "            tracemalloc.Filter(False, '<frozen *'),\n"
            "            tracemalloc.Filter(False, __file__),\n"
            "        ]\n"
            "        tracemalloc_file = getattr(tracemalloc, '__file__', None)\n"
            "        if tracemalloc_file:\n"
            "            filters.append(tracemalloc.Filter(False, tracemalloc_file))\n"
            "        filtered = snapshot.filter_traces(filters)\n"
            "        stats = filtered.statistics('lineno')\n"
            "        entries = []\n"
            "        py_tracked = sum(s.size for s in stats)\n"
            "        for stat in stats[:top_n]:\n"
            "            frame = stat.traceback[0]\n"
            "            entries.append({\n"
            "                'filename': frame.filename,\n"
            "                'lineno': frame.lineno,\n"
            "                'size_diff_bytes': stat.size,\n"
            "                'count_diff': stat.count,\n"
            "            })\n"
            "        data = {\n"
            "            'entries': entries,\n"
            "            'python_tracked_bytes': py_tracked,\n"
            "        }\n"
            "        with open(out_path, 'w', encoding='utf-8') as f:\n"
            "            json.dump(data, f)\n"
            "    except Exception:\n"
            "        pass\n\n"
            "atexit.register(_proctrace_dump_attribution)\n"
        )
        sitecustomize_path.write_text(sitecustomize_code, encoding="utf-8")

        child_env = dict(os.environ)
        child_env["PYTHONTRACEMALLOC"] = str(getattr(args, "attribution_frames", 10))
        child_env["PROCTRACE_ATTRIBUTION_TOP"] = str(getattr(args, "attribution_top", 10))
        child_env["PROCTRACE_ATTRIBUTION_FILE"] = str(attr_file)
        cur_pp = child_env.get("PYTHONPATH")
        child_env["PYTHONPATH"] = f"{temp_dir}{os.pathsep}{cur_pp}" if cur_pp else temp_dir

    our_before = snapshot_resources()
    t_start = time.monotonic_ns()

    try:
        try:
            proc = subprocess.Popen(args.command, env=child_env)
        except FileNotFoundError as err:
            print(f"proctrace run: command not found: {args.command[0]!r} ({err})", file=sys.stderr)
            return 127

        peak_rss_bytes = 0
        target_rss_bytes = 0
        child_rusage = None

        if hasattr(os, "wait4"):
            try:
                while True:
                    pid_res, status_res, ru = os.wait4(proc.pid, os.WNOHANG)
                    if pid_res != 0:
                        child_rusage = ru
                        if os.WIFEXITED(status_res):
                            proc.returncode = os.WEXITSTATUS(status_res)
                        elif os.WIFSIGNALED(status_res):
                            proc.returncode = -os.WTERMSIG(status_res)
                        else:
                            proc.returncode = 0
                        break
                    try:
                        rss_mb, _ = _read_proc_mem(proc.pid)
                        rss_b = int(rss_mb * _BYTES_PER_MB)
                        if rss_b > peak_rss_bytes:
                            peak_rss_bytes = rss_b
                        target_rss_bytes = rss_b
                    except (ProcessLookupError, OSError):
                        pass
                    time.sleep(0.005)
            except ChildProcessError:
                proc.wait()
        else:
            while proc.poll() is None:
                try:
                    rss_mb, _ = _read_proc_mem(proc.pid)
                    rss_b = int(rss_mb * _BYTES_PER_MB)
                    if rss_b > peak_rss_bytes:
                        peak_rss_bytes = rss_b
                    target_rss_bytes = rss_b
                except (ProcessLookupError, OSError):
                    pass
                time.sleep(0.005)
            proc.wait()

        elapsed_ns = time.monotonic_ns() - t_start
        our_after = snapshot_resources()

        if peak_rss_bytes > 0:
            rss_delta = peak_rss_bytes
        elif target_rss_bytes > 0:
            rss_delta = target_rss_bytes
        else:
            rss_delta = our_after.rss_bytes - our_before.rss_bytes

        alloc_report = None
        if attribution_enabled and attr_file and attr_file.is_file():
            try:
                raw_json = json.loads(attr_file.read_text(encoding="utf-8"))
                from proctrace.alloc import AllocationReport

                raw_json["rss_delta_bytes"] = rss_delta
                raw_json["native_estimate_bytes"] = max(
                    0, rss_delta - raw_json.get("python_tracked_bytes", 0)
                )
                alloc_report = AllocationReport.from_dict(raw_json)
            except Exception:
                pass

        if args.json:
            result = {
                "command": args.command,
                "exit_code": proc.returncode,
                "rss_delta_bytes": rss_delta,
                "rss_delta_mb": rss_delta / _BYTES_PER_MB,
                "elapsed_ms": elapsed_ns / _NS_PER_MS,
            }
            if child_rusage is not None:
                elapsed_s = elapsed_ns / 1_000_000_000.0 if elapsed_ns > 0 else 1.0
                cpu_total = child_rusage.ru_utime + child_rusage.ru_stime
                result["cpu_user_s"] = child_rusage.ru_utime
                result["cpu_sys_s"] = child_rusage.ru_stime
                result["cpu_percent"] = (cpu_total / elapsed_s) * 100.0
                result["minor_faults"] = child_rusage.ru_minflt
                result["major_faults"] = child_rusage.ru_majflt
                result["ctx_switches_vol"] = child_rusage.ru_nvcsw
                result["ctx_switches_invol"] = child_rusage.ru_nivcsw
            if alloc_report is not None:
                result["allocations"] = alloc_report.to_dict()
            print(json.dumps(result, indent=2))
        else:
            mb = rss_delta / _BYTES_PER_MB
            sign = "+" if mb >= 0 else ""
            lines = [
                f"\n[proctrace] {' '.join(args.command)}",
                f"  exit code : {proc.returncode}",
                f"  rss delta : {sign}{mb:.2f} MB",
                f"  elapsed   : {elapsed_ns / _NS_PER_MS:.1f} ms",
            ]
            if child_rusage is not None and (
                child_rusage.ru_utime > 0 or child_rusage.ru_stime > 0
            ):
                elapsed_s = elapsed_ns / 1_000_000_000.0 if elapsed_ns > 0 else 1.0
                cpu_total = child_rusage.ru_utime + child_rusage.ru_stime
                cpu_pct = (cpu_total / elapsed_s) * 100.0
                lines.append(
                    f"  cpu time  : {child_rusage.ru_utime:.3f}s user / {child_rusage.ru_stime:.3f}s sys ({cpu_pct:.1f}%)"
                )
                lines.append(
                    f"  faults    : {child_rusage.ru_minflt} minor / {child_rusage.ru_majflt} major"
                )
                lines.append(
                    f"  ctx switch: {child_rusage.ru_nvcsw} vol / {child_rusage.ru_nivcsw} invol"
                )
            print("\n".join(lines) + "\n", file=sys.stderr)
            if alloc_report is not None:
                print("Top Allocations:\n" + alloc_report.report() + "\n", file=sys.stderr)

        return proc.returncode
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)


def cmd_dump(args: argparse.Namespace) -> int:      #Send a POSIX signal to trigger a proctrace thread dump in the target process
    sig_name = args.signal
    sig_num = getattr(signal, sig_name, None)
    if sig_num is None:
        print(f"Unknown signal: {sig_name}", file=sys.stderr)
        return 1

    if args.pid <= 0:
        print(f"[proctrace] Invalid PID: {args.pid}", file=sys.stderr)
        return 1

    try:
        os.kill(args.pid, sig_num)
        print(
            f"[proctrace] Sent {sig_name} to pid {args.pid}. Check that process's stderr.",
            file=sys.stderr,
        )
        return 0
    except ProcessLookupError:
        print(f"[proctrace] No process with pid {args.pid}", file=sys.stderr)
        return 1
    except PermissionError:
        print(
            f"[proctrace] Permission denied sending {sig_name} to pid {args.pid}",
            file=sys.stderr,
        )
        return 1


def cmd_watch(args: argparse.Namespace) -> int:    # Live-monitor the RSS and VMS memory of a target PID for a given duration
    pid = args.pid
    if pid <= 0:
        print(f"[proctrace] Invalid PID: {pid}", file=sys.stderr)
        return 1
    interval = args.interval
    duration = args.duration
    deadline = time.monotonic() + duration

    print(
        f"[proctrace watch] pid={pid}, interval={interval}s, duration={duration}s",
        file=sys.stderr,
    )
    print(f"{'Time':>8}  {'RSS MB':>10}  {'VMS MB':>10}", file=sys.stderr)
    print("-" * 32, file=sys.stderr)

    t_start_ns = time.monotonic_ns()
    t_ms_list: list[float] = []
    rss_list: list[int] = []
    vms_list: list[int] = []
    fds_list: list[int] = []

    try:
        while time.monotonic() < deadline:
            now_ns = time.monotonic_ns()
            rss_mb, vms_mb = _read_proc_mem(pid)
            elapsed = duration - (deadline - time.monotonic())
            line = f"{elapsed:>7.1f}s  {rss_mb:>9.1f}  {vms_mb:>9.1f}"
            print(f"\r{line}", end="", flush=True, file=sys.stderr)

            elapsed_ms = (now_ns - t_start_ns) / _NS_PER_MS
            t_ms_list.append(elapsed_ms)
            rss_list.append(int(rss_mb * _BYTES_PER_MB))
            vms_list.append(int(vms_mb * _BYTES_PER_MB))
            fds_list.append(_read_proc_fds(pid))

            time.sleep(interval)
    except (ProcessLookupError, ProcessNotFoundError):
        print(f"\n[proctrace] Process {pid} exited.", file=sys.stderr)
    except KeyboardInterrupt:
        pass
    finally:
        print(file=sys.stderr)
        from proctrace.timeline import Timeline

        timeline = Timeline(t_ms=t_ms_list, rss=rss_list, vms=vms_list, fds=fds_list)
        if len(timeline) > 0:
            peak_mb = (timeline.max_rss_bytes or 0) / _BYTES_PER_MB
            peak_t = timeline.peak_at_ms or 0.0
            slope = timeline.rss_slope_bytes_per_s
            slope_str = (
                f"{slope / _BYTES_PER_MB:+.2f} MB/s"
                if slope is not None
                else "N/A"
            )
            print("-" * 32, file=sys.stderr)
            print("[proctrace watch summary]", file=sys.stderr)
            print(f"  Peak RSS : {peak_mb:.1f} MB (at {peak_t:.1f} ms)", file=sys.stderr)
            print(f"  RSS Slope: {slope_str} (R² = {timeline.r2:.2f})", file=sys.stderr)

        timeline_out = getattr(args, "timeline", None)
        if timeline_out:
            out_p = Path(timeline_out)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(timeline.to_json(indent=2), encoding="utf-8")
            print(f"[proctrace] Wrote timeline to {out_p}", file=sys.stderr)

    return 0


def cmd_plot(args: argparse.Namespace) -> int:
    """Generate an HTML or SVG plot from recorded timeline JSON."""
    input_file = Path(args.timeline_file)
    if not input_file.is_file():
        print(f"proctrace plot: file not found: {input_file}", file=sys.stderr)
        return 1

    from proctrace.telemetry.plot import plot_timeline
    from proctrace.timeline import Timeline

    try:
        content = input_file.read_text(encoding="utf-8")
        timeline = Timeline.from_json(content)
    except Exception as err:
        print(f"proctrace plot: failed to parse timeline from {input_file}: {err}", file=sys.stderr)
        return 1

    out_file = Path(args.output)
    title = getattr(args, "title", "proctrace Resource Timeline") or "proctrace Resource Timeline"
    try:
        plot_timeline(timeline, out_file, title=title)
        print(f"[proctrace] Plot generated at {out_file}", file=sys.stderr)
        return 0
    except Exception as err:
        print(f"proctrace plot: error generating plot: {err}", file=sys.stderr)
        return 1


def cmd_guide(args: argparse.Namespace) -> int:      # Print the developer documentation guide
    import proctrace

    proctrace.help()
    return 0


__all__ = [
    "_read_proc_fds",
    "_read_proc_mem",
    "cmd_dump",
    "cmd_guide",
    "cmd_plot",
    "cmd_run",
    "cmd_watch",
]

