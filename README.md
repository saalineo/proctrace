# proctrace

Non-invasive process and OS state introspection for Python developers.

Diagnose memory leaks, file descriptor leaks, race conditions, and IPC bottlenecks
with a single import — no agents, no profilers, no code changes required.

Built on a Rust core exposed to Python via [PyO3](https://pyo3.rs) and [Maturin](https://www.maturin.rs).

**Status:** Under construction.  
**Platform support:** Linux ✓ · macOS ✓ · Windows ✗  
**Python:** ≥ 3.10  
**License:** MIT

---

## Installation

```bash
pip install proctrace
```

Or to build from source (requires Rust + `maturin`):

```bash
pip install maturin
maturin develop --release
```

---

## Quick Start

### Context manager

```python
import proctrace

with proctrace.watch() as probe:
    your_code_here()

print(probe.result.report())
```

Sample output:

```
┌─ proctrace ResourceDelta ──────────────────────────────────┐
│  memory rss      +12.34 MB                                 │
│  memory peak     +45.00 MB                                 │
│  virtual mem     +8.00 MB                                  │
│  open fds        +3                                        │
│  threads         +2 (new: worker-1, worker-2)              │
│  child procs     +0                                        │
│  elapsed         142.5 ms                                  │
└────────────────────────────────────────────────────────────┘
```

### Decorator

```python
from proctrace.decorators import probe

@probe(memory=True, fds=True, output="stderr")
def load_data(path):
    ...

load_data("big_file.csv")
# ResourceDelta printed to stderr automatically

# Access the result programmatically
result = load_data.last_probe_result
print(result.rss_delta_mb)
```

Works on both regular and `async` functions.

---

## API Reference

### `proctrace.watch(...)` → `ResourceWatcher`

Returns a context manager that snapshots OS resources before and after the block.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `memory` | `bool` | `True` | Track RSS / VMS memory |
| `fds` | `bool` | `True` | Track open file descriptors |
| `threads` | `bool` | `True` | Track thread count and names |
| `children` | `bool` | `True` | Track child process count |
| `sample_interval` | `float` | `0.1` | Background sampler interval in seconds (for peak RSS) |

After the `with` block, `probe.result` is a `ResourceDelta`.

### `ResourceDelta`

| Attribute | Type | Description |
|---|---|---|
| `rss_delta_bytes` | `int` | RSS change in bytes (can be negative) |
| `vms_delta_bytes` | `int` | Virtual memory size change |
| `fd_delta` | `int` | Open FD count change |
| `peak_rss_bytes` | `int` | Highest RSS seen during the block |
| `thread_delta` | `int` | Thread count change |
| `child_delta` | `int` | Child process count change |
| `elapsed_ns` | `int` | Wall-clock time of block in nanoseconds |
| `leaked_fds` | `list[str]` | FD paths present at exit but not at entry |
| `new_thread_names` | `list[str]` | Names of threads created during the block |

Convenience properties: `.rss_delta_mb`, `.vms_delta_mb`, `.peak_rss_mb`, `.elapsed_ms`.

Serialization: `.to_dict()`, `.to_json(indent=...)`, `.from_dict(d)`.

### `@probe(...)` decorator

```python
from proctrace.decorators import probe

@probe(
    memory=True,
    fds=True,
    threads=True,
    children=False,
    sample_interval=0.1,
    output="stderr",  # or "none"
    store=True,       # stores result on fn.last_probe_result
)
def my_function(): ...
```

### IPC Tracing

Wrap queues, pipes, and sockets to measure latency and throughput:

```python
import queue, os
from proctrace.ipc import trace_ipc, trace_pipe, trace_socket, ipc_report

# Queues
q = trace_ipc(queue.Queue(), name="task-queue")
q.put(item)
item = q.get()

# OS pipes
r_fd, w_fd = os.pipe()
pipe = trace_pipe(r_fd, w_fd, name="my-pipe")
pipe.write(b"hello")
data = pipe.read(5)

# Sockets
sock = trace_socket(raw_socket, name="worker-conn")
sock.sendall(b"data")
response = sock.recv(1024)

# Print a report of all traced channels
print(ipc_report())
```

### On-demand stack dumps

Install a signal handler to dump all threads and asyncio tasks to stderr on demand:

```python
import proctrace

proctrace.install_signal_handler(sig="SIGUSR1")  # SIGUSR1, SIGUSR2, or SIGALRM

# From the terminal:
# kill -USR1 <pid>
```

Outputs a timestamped dump of every thread's stack trace plus any running asyncio tasks.

---

## CLI

```
proctrace <subcommand> [options]
```

### `proctrace run` — measure a subprocess

```bash
proctrace run -- python myscript.py
proctrace run --json -- python myscript.py   # JSON output to stdout
```

### `proctrace watch` — live memory polling

```bash
proctrace watch --pid <PID> --interval 1.0 --duration 30
```

Prints a live-updating table of RSS and VMS (MB) for the target process.

### `proctrace dump` — trigger a stack dump

```bash
proctrace dump --pid <PID> --signal SIGUSR1
```

Sends a signal to a running proctrace-instrumented process to trigger a thread dump.

---

## Benchmarks

Measured on a quiet machine: single-threaded hot loop, 200,000 iterations of
`snapshot_resources()`, which reads memory, FD count, and thread state in a
single Rust call.

### Snapshot latency

| Operation | Latency | Throughput |
|---|---|---|
| `snapshot_resources()` | 7.9 µs/call | ~127k calls/s |

### Detection accuracy

Scenario: inside the measured region, allocate 64 MB, open 10 FDs, start 2
threads, and spawn 1 child process.

| Metric | Detected |
|---|---|
| RSS delta | +64.3 MB |
| Open FD delta | +10 |
| Thread delta | +2 |
| Child process delta | +1 |

*Measured on: Linux 7.1.8 (x86_64, 12 cores), CPython 3.14, proctrace 0.1.0.
Results vary by machine.*

---

## Development

```bash
# Create virtual environment
python -m venv .venv && source .venv/bin/activate

# Install dev dependencies and build the Rust extension
pip install -e ".[dev]"
maturin develop

# Run tests
pytest
```

Requires: Rust toolchain, Python ≥ 3.10.
