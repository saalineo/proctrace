from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
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


def cmd_run(args: argparse.Namespace) -> int:      #Execute a command in a child process and report memory and time deltas
    if not args.command:
        print(
            "proctrace run: no command specified. Use: proctrace run -- cmd [args]",
            file=sys.stderr,
        )
        return 1

    from proctrace._proctrace_core import snapshot_resources

    our_before = snapshot_resources()
    t_start = time.monotonic_ns()

    try:
        proc = subprocess.Popen(args.command)
    except FileNotFoundError as err:
        print(f"proctrace run: command not found: {args.command[0]!r} ({err})", file=sys.stderr)
        return 127

    peak_rss_bytes = 0
    target_rss_bytes = 0

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

    if args.json:
        result = {
            "command": args.command,
            "exit_code": proc.returncode,
            "rss_delta_bytes": rss_delta,
            "rss_delta_mb": rss_delta / _BYTES_PER_MB,
            "elapsed_ms": elapsed_ns / _NS_PER_MS,
        }
        print(json.dumps(result, indent=2))
    else:
        mb = rss_delta / _BYTES_PER_MB
        sign = "+" if mb >= 0 else ""
        print(
            f"\n[proctrace] {' '.join(args.command)}\n"
            f"  exit code : {proc.returncode}\n"
            f"  rss delta : {sign}{mb:.2f} MB\n"
            f"  elapsed   : {elapsed_ns / _NS_PER_MS:.1f} ms\n",
            file=sys.stderr,
        )

    return proc.returncode


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

    try:
        while time.monotonic() < deadline:
            rss_mb, vms_mb = _read_proc_mem(pid)
            elapsed = duration - (deadline - time.monotonic())
            line = f"{elapsed:>7.1f}s  {rss_mb:>9.1f}  {vms_mb:>9.1f}"
            print(f"\r{line}", end="", flush=True, file=sys.stderr)
            time.sleep(interval)
    except ProcessLookupError:
        print(f"\n[proctrace] Process {pid} exited.", file=sys.stderr)
    except KeyboardInterrupt:
        pass
    finally:
        print(file=sys.stderr)

    return 0


def cmd_guide(args: argparse.Namespace) -> int:      # Print the developer documentation guide
    import proctrace

    proctrace.help()
    return 0


__all__ = [
    "_read_proc_mem",
    "cmd_dump",
    "cmd_guide",
    "cmd_run",
    "cmd_watch",
]
