# Changelog

## [0.1.2] - 2026-10-27

### Added
- **Subsystem Architecture Modularization:** Reorganized runtime into modular packages: `proctrace.core`, `telemetry`, `ipc`, `diagnostics`, `alloc`, `testing`, and `cli`.
- **Extended OS Metrics:** Added CPU utilization (`cpu_user_ns`, `cpu_sys_ns`, `cpu_percent`), voluntary and involuntary context switches (`ctx_switches_vol`, `ctx_switches_invol`), minor and major page faults (`minor_faults`, `major_faults`), and disk/syscall I/O tracking (`io_read_bytes`, `io_write_bytes`, `io_rchar_bytes`, `io_wchar_bytes`).
- **Per-Thread CPU & 64-bit Thread IDs:** Added `busiest_threads` ranking thread CPU utilization, supported by full 64-bit thread identifier inspection across Linux and macOS.
- **TCP Socket State Tracking:** Inspection and net transition deltas across TCP socket connection states (`ESTABLISHED`, `LISTEN`, `TIME_WAIT`, `CLOSE_WAIT`) in `sockets_by_state` and `socket_deltas`.
- **Zero-Alloc Bounded Ring Buffer:** Replaced the single-peak background sampler in Rust (`sampler.rs`) with a fixed-capacity circular ring buffer with zero-GIL memory sharing.
- **Resource Timeline Telemetry:** Added `proctrace.timeline.Timeline` for high-frequency timeline sampling and lifecycle event capture without overhead when disabled.
- **SVG & HTML Chart Generation:** Implemented visual timeline rendering in `proctrace.telemetry.plot` and `proctrace plot` CLI (`--plot-svg`, `--plot-html`).
- **Memory Allocation Attribution & Object Growth:** Added `proctrace.alloc` featuring `tracemalloc` source-line allocation attribution and Python GC object delta tracking.
- **Pytest Resource Gating & Baselines:** Added `@pytest.mark.resource_limits(...)`, the `proctrace` test fixture, and golden regression gating (`--proctrace-baseline`, `--proctrace-generate-baseline`).
- **In-Terminal Documentation & Benchmarking:** Added interactive `proctrace.help()` pager, comprehensive `guidedoc.md`, and empirical `BENCHMARK_REPORT.md`.

### Fixed
- **Duplicate FD Leak Detection:** Switched from set diffs to multiset difference (`collections.Counter`) to reliably track duplicate leaks of the same file path.
- **macOS FD Enumeration Limit:** Replaced blind `0..4096` iteration with dynamic buffer growth via `proc_pidinfo(PROC_PIDLISTFDS)`.
- **Stabilized `fd_delta` Calculation:** Prevented spurious negative file descriptor deltas caused by transient background descriptors closing.
- **Probe Sample Rate Comparability:** Added `sample_interval_ms` and `is_comparable_with()` checks to guarantee like-for-like comparisons across different sample rates.

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
