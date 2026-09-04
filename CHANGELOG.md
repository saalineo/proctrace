# Changelog

## [0.1.1] - 2026-09-05

### Fixed ( Bug Resolutions)
**Critical & High Priority Fixes:**
* **Memory Safety / FFI:** Resolved arbitrary FD byte injection upon signal in `src/signal.rs`.
* **Concurrency:** Fixed unbounded background thread leak & CPU spin in `src/sampler.rs`.
* **Concurrency:** Fixed Asyncio cross-thread race condition and crash in `proctrace/snapshot.py`.
* **Memory Safety:** Prevented unbounded dictionary growth and bad latency stats in `proctrace/ipc.py`.

**Medium Priority Fixes:**
* **Memory Safety:** Fixed global registry memory retention leak in `proctrace/ipc.py`.
* **Logic / FFI:** Fixed undercounting of open FDs > 256 on macOS in `src/resources/macos.rs`.
* **Logic / Portability:** Fixed invalid FIONREAD ioctl opcode on macOS in `proctrace/ipc.py`.
* **Integrity:** Target command resources are now properly measured in `proctrace/cli.py`.

**Low Priority Fixes:**
* **Input Validation:** Eliminated PID broadcasting risk on negative inputs in `proctrace/cli.py`.
* **Concurrency:** Fixed shared attribute race conditions in `proctrace/decorators.py`.
* **Concurrency:** Resolved corrupted interleaved log writes in `proctrace/logger.py`.

## [0.1.0] — 2026-08-12

### Added
- `proctrace.watch()` context manager: monitor memory, fd, thread, child process deltas
- `@proctrace.probe()` decorator: wraps sync and async functions
- `proctrace.install_signal_handler()`: SIGUSR1-triggered thread + asyncio dump
- `proctrace.trace_ipc(queue)`: latency and depth tracking for queues
- `proctrace.trace_pipe(r, w)`: latency tracking for pipe pairs
- `proctrace.trace_socket(sock)`: send latency + recv buffer utilization for sockets
- `proctrace.ipc_report()`: human-readable IPC channel summary
- `ProctraceLogger`: JSONL structured logging for ResourceDelta events
- `pytest-proctrace` plugin: per-test resource tracking via `--proctrace`
- `proctrace` CLI: `run`, `dump`, `watch` subcommands
- Linux backend: `/proc/self/status`, `/proc/self/fd`
- macOS backend: `proc_pidinfo(PROC_PIDTASKINFO)`, `fcntl(F_GETPATH)`
- Background Rust sampler thread for peak RSS tracking
- Zero runtime Python dependencies

### Platform support
- Linux: ✓ (primary development platform)
- macOS: ✓ (tested on arm64 and x86_64)
- Windows: ✗ (no `/proc`, no `POSIX signals`)
