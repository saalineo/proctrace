from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time


def cmd_run(args: argparse.Namespace) -> int:

    if not args.command:
        print("proctrace run: no command specified. Use: proctrace run -- cmd [args]", file=sys.stderr)
        return 1

    import proctrace
    from proctrace._proctrace_core import snapshot_resources

    our_before = snapshot_resources()
    t_start = time.monotonic_ns()

    proc = subprocess.Popen(args.command)
    proc.wait()

    elapsed_ns = time.monotonic_ns() - t_start
    our_after = snapshot_resources()

    rss_delta = our_after.rss_bytes - our_before.rss_bytes

    if args.json:
        result = {
            "command": args.command,
            "exit_code": proc.returncode,
            "rss_delta_bytes": rss_delta,
            "rss_delta_mb": rss_delta / (1024 * 1024),
            "elapsed_ms": elapsed_ns / 1_000_000,
        }
        print(json.dumps(result, indent=2))
    else:
        mb = rss_delta / (1024 * 1024)
        sign = "+" if mb >= 0 else ""
        print(
            f"\n[proctrace] {' '.join(args.command)}\n"
            f"  exit code : {proc.returncode}\n"
            f"  rss delta : {sign}{mb:.2f} MB\n"
            f"  elapsed   : {elapsed_ns / 1_000_000:.1f} ms\n",
            file=sys.stderr,
        )

    return proc.returncode


def cmd_dump(args: argparse.Namespace) -> int:
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
        print(f"[proctrace] Sent {sig_name} to pid {args.pid}. Check that process's stderr.", file=sys.stderr)
        return 0
    except ProcessLookupError:
        print(f"[proctrace] No process with pid {args.pid}", file=sys.stderr)
        return 1
    except PermissionError:
        print(f"[proctrace] Permission denied sending {sig_name} to pid {args.pid}", file=sys.stderr)
        return 1


def cmd_watch(args: argparse.Namespace) -> int:
    pid = args.pid
    if pid <= 0:
        print(f"[proctrace] Invalid PID: {pid}", file=sys.stderr)
        return 1
    interval = args.interval
    duration = args.duration
    deadline = time.monotonic() + duration

    print(f"[proctrace watch] pid={pid}, interval={interval}s, duration={duration}s", file=sys.stderr)
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


def _read_proc_mem(pid: int) -> tuple[float, float]:
    if pid <= 0:
        raise ProcessLookupError(pid)
    if sys.platform == "linux":
        try:
            status = open(f"/proc/{pid}/status").read()
        except FileNotFoundError:
            raise ProcessLookupError(pid)
        rss_kb = vms_kb = 0
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                rss_kb = int(line.split()[1])
            elif line.startswith("VmSize:"):
                vms_kb = int(line.split()[1])
        return rss_kb / 1024, vms_kb / 1024

    # No /proc on macOS/BSD: ps reports RSS and VSZ in KB
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "rss=,vsz="],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise ProcessLookupError(pid)
    parts = result.stdout.strip().split()
    if len(parts) < 2:
        raise ProcessLookupError(pid)
    return int(parts[0]) / 1024, int(parts[1]) / 1024


def cmd_guide(args: argparse.Namespace) -> int:
    import proctrace
    proctrace.help()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="proctrace",
        description="Non-invasive process introspection tool",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    run_p = subparsers.add_parser("run", help="Run a command and report resource usage")
    run_p.add_argument("--memory", action="store_true", default=True)
    run_p.add_argument("--fds", action="store_true", default=False)
    run_p.add_argument("--json", action="store_true", default=False, help="Output as JSON to stdout")
    run_p.add_argument("command", nargs=argparse.REMAINDER)

    dump_p = subparsers.add_parser("dump", help="Send a signal to trigger a stack dump")
    dump_p.add_argument("--pid", type=int, required=True)
    dump_p.add_argument("--signal", default="SIGUSR1", choices=["SIGUSR1", "SIGUSR2"])

    guide_p = subparsers.add_parser("guide", help="Print the developer guide")

    watch_p = subparsers.add_parser("watch", help="Live-poll a process's memory")
    watch_p.add_argument("--pid", type=int, required=True)
    watch_p.add_argument("--interval", type=float, default=1.0, help="Poll interval in seconds")
    watch_p.add_argument("--duration", type=float, default=10.0, help="Total watch duration in seconds")

    args = parser.parse_args()

    if args.subcommand == "run" and args.command and args.command[0] == "--":
        args.command = args.command[1:]

    dispatch = {"run": cmd_run, "dump": cmd_dump, "watch": cmd_watch, "guide": cmd_guide}
    sys.exit(dispatch[args.subcommand](args))


if __name__ == "__main__":
    main()
