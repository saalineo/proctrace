<p align="center">
  <img src="logos/proctrace.svg" alt="proctrace logo" width="160" height="160" />
</p>

<h1 align="center">proctrace</h1>

<p align="center">
  <b>Sub-microsecond, non-invasive process and OS state introspection for Python.</b><br>
  <i>Diagnose memory leaks, file descriptor leaks, IPC bottlenecks, and runtime deadlocks with near-zero overhead.</i>
</p>

<p align="center">
  <a href="https://github.com/proctrace/proctrace/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  <a href="https://pypi.org/project/proctrace/"><img src="https://img.shields.io/badge/python-%E2%89%A53.10-blue" alt="Python Version"></a>
  <a href="https://www.rust-lang.org/"><img src="https://img.shields.io/badge/rust-2021-orange.svg" alt="Rust 2021"></a>
  <a href="#platform-support"><img src="https://img.shields.io/badge/platforms-Linux%20%7C%20macOS-green.svg" alt="Platforms"></a>
</p>

---

## Detailed Purpose

Traditional Python profilers (`cProfile`, `tracemalloc`, `memray`, `py-spy`) approach performance and diagnostics from inside the interpreter or via sampling stack frames. While valuable for function timing, they introduce severe limitations:

1. **Massive Overhead in Tests and Production**: Tracing allocators and bytecode hooks often slow applications down by 2x to 50x, distorting timings and causing performance tests to flake.
2. **Blind to Native Extensions**: Allocations in C/C++/Rust libraries (e.g., NumPy, PyTorch, Polars, Cryptography, PyArrow, or raw `mmap`) bypass the Python memory allocator (`PyMem_Malloc`). `tracemalloc` will report 0 bytes allocated while your OS Resident Set Size (RSS) climbs into gigabytes.
3. **Invisible Operating System Resources**: Memory is only one dimension of process health. Standard tools cannot detect unclosed file descriptors, leaked epoll/kqueue handles, runaway background OS threads, zombie child processes, or blocked kernel IPC buffers.

**`proctrace` takes a fundamentally different approach: sub-microsecond OS kernel introspection.**

Built with a compiled Rust engine via PyO3, `proctrace` queries kernel procfs, task APIs, and file tables directly in **under 8 microseconds** (<0.008 ms). It measures true operating system state before, during, and after execution blocks—with zero bytecode modification, zero interpreter hooks, and zero overhead in steady-state operation.

---

## What `proctrace` Can Do

`proctrace` provides a unified telemetry toolkit spanning local execution, CI/CD regression gating, and production diagnosis:

###  True OS-Level Memory & Peak Telemetry
- **Physical RSS & Virtual VMS**: Directly reads OS resident memory (`/proc/self/statm` on Linux, `mach_task_self()` on macOS), capturing allocations across Python and native extensions alike.
- **Monotonic High-Water Mark (Peak RSS)**: An asynchronous, ultra-low-overhead background sampler captures memory spikes that allocate and deallocate within the lifetime of a function.
- **Allocation Attribution (`tracemalloc` & Object Census)**: Pinpoints exact file and line numbers where Python allocations occurred, decomposes growth into Python-managed vs native/untracked C extension allocations, and maps object type growth.

###  Allocation Attribution (Where Did the Memory Go?)

When process RSS grows, `proctrace` can report exact Python source lines, object counts, and whether memory growth occurred in Python-managed objects or native C extensions/allocator fragmentation:

```python
import proctrace

with proctrace.watch(attribution="tracemalloc", top=10) as p:
    work()

print(p.result.allocations.report())
# +84.2 MB  app/loader.py:57   (12,331 blocks)
# +20.1 MB  app/cache.py:19    (   410 blocks)
# python-tracked: 112 MB   native/untracked: 8 MB   (rss delta 120 MB)

# In "objects" or "all" mode, inspect GC object census deltas:
print(p.result.object_growth)
# {"dict": +10234, "MyRecord": +200, ...}
```

> [!NOTE]
> **Overhead & Performance Trade-offs**: Attribution tracking is strictly opt-in (`attribution=None` by default with zero runtime overhead). When enabled, 1-frame tracing (`frames=1`) introduces roughly 2x–4x allocation overhead in CPython. Deeper stacks (`frames=10`) capture multi-level call chains with ~4x–8x allocation overhead. Object census (`attribution="objects"`) performs full GC sweeps before and after the block.


###  Deterministic Handle & File Descriptor Leak Detection
- **Exact Path Resolution**: Takes instant diffs of `/proc/self/fd` to detect newly leaked handles and resolves them to their exact filesystem paths or socket endpoints.
- **Duplicate FD Leak Tracking**: Surfaces unclosed duplicates (`dup`/`dup2`) using multiset difference analysis.

###  Thread & Concurrency Lifecycle Monitoring
- **OS Thread Creation**: Detects hardware and interpreter threads created during a block.
- **Thread Naming & Identification**: Surfaces newly spawned thread names (e.g. `worker-1`, `asyncio_0`) to isolate background thread leaks.

###  Subprocess & Child Process Tree Introspection
- Tracks child process forks (`fork()`, `clone()`, `subprocess.Popen`) and monitors active subprocess tree deltas.

###  Low-Latency IPC & Channel Tracing
- **Queues**: Measures put-to-get latency and message volume across `queue.Queue` and `multiprocessing.Queue`.
- **OS Pipes & Unix Sockets**: Intercepts reads and writes to track byte throughput and queries buffer queue depth via the `FIONREAD` ioctl opcode.

###  Live, Non-Blocking Runtime Diagnostics
- **On-Demand Stack Dumps**: Installs non-intrusive signal handlers (`SIGUSR1`, `SIGUSR2`, `SIGALRM`) to atomically dump stack traces for every thread and active `asyncio` task to stderr without pausing or restarting the target process.

###  Production-Grade Pytest Suite & CI Baselines
- **`@pytest.mark.proctrace(...)`**: Enforces strict resource budgets per test (e.g., max peak memory, zero leaked FDs) and displays top allocations on violation via `attribution=True`.
- **`proctrace_probe` Fixture**: Enables scoped assertions (`assert_no_leaks`) within specific test segments.
- **CI Regression Baselines**: Records baseline metrics and fails builds on memory or latency regressions (`--proctrace-fail-on-regression 10%`).
- **Top-N Memory Ranking**: Displays the highest peak RSS tests in a formatted terminal summary table.
- **`pytest-xdist` Compatibility**: Seamlessly merges results across multi-core test workers.

### Multi-Transport Structured Logging
- Streams `ResourceDelta` telemetry formatted as JSON Lines (`jsonl`), JSON, or human-readable text directly to files, stdout, TCP sockets, or UNIX domain sockets.

###  Standalone CLI Tooling
- `proctrace run`: Profile external commands with memory, FD, execution time telemetry, and allocation attribution via `--attribution`.
- `proctrace watch`: Real-time live PID polling with interactive terminal visualization.
- `proctrace dump`: Send diagnostic dump signals to remote processes.

---

## Comprehensive Documentation

For complete architectural details, API specifications, and real-world production recipes, refer to the developer manual:

 **[Read the Technical Guide & Developer Manual (`guidedoc.md`)](guidedoc.md)**

Inside `guidedoc.md`:
- **Architecture & System Design**: Sub-microsecond Rust snapshotting mechanics.
- **API Reference**: `ResourceWatcher`, `ResourceDelta`, `Limits`, `ProctraceLogger`.
- **IPC & Synchronization**: Tracing queues, OS pipes, and network sockets with `FIONREAD`.
- **Live Diagnostics**: Signal handlers and remote `asyncio` task tree inspection.
- **Pytest Plugin**: Markers, fixtures, baseline schemas, tolerance syntax, and xdist coordination.
- **Production Cookbooks**: Recipes for FastAPI memory creep, Celery workers, and GitHub Actions workflows.
- **OS Quirks & FAQ**: Linux kernel `ru_maxrss` high-water mark behavior, thread noise suppression, and event loop FD warmup.

You can also print the technical guide directly from your Python terminal:
```python
import proctrace
proctrace.help()
```

---

## Installation

### From PyPI

```bash
pip install proctrace
```

### From Source (Requires Rust & Maturin)

```bash
git clone https://github.com/proctrace/proctrace.git
cd proctrace
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
maturin develop --release
```

---

## Platform Support

| Operating System | Architecture | Support Status | Kernel Mechanics |
|---|---|---|---|
| **Linux** | x86_64, aarch64 | **Tier 1 (Complete)** | `/proc/self/statm`, `/proc/self/fd`, `getrusage`, `ioctl(FIONREAD)` |
| **macOS** | Apple Silicon, x86_64 | **Tier 1 (Complete)** | `mach_task_self()`, `proc_pidinfo()`, `kqueue`, `proc_listpids()` |
| **Windows** | x86_64 | *Under Construction* | Win32 `GetProcessMemoryInfo`, handle snapshotting |

---

## Benchmarks

Benchmarked on Linux 6.8 (x86_64, 12 cores) with CPython 3.13:

### Snapshot Latency
| Operation | Latency | Throughput |
|---|---|---|
| `snapshot_resources()` | **7.9 µs / call** | **~127,000 calls / sec** |

### Detection Accuracy Under Test
| Scenario | Injected | Detected by `proctrace` |
|---|---|---|
| Memory Allocation | +64.0 MB | **+64.3 MB** |
| File Descriptors | +10 open files | **+10 leaked FDs (with paths)** |
| Background Threads | +2 OS threads | **+2 threads (with names)** |
| Subprocess Spawn | +1 child process | **+1 child process** |

---

## License

`proctrace` is open-source software licensed under the [MIT License](LICENSE).

