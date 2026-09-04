from __future__ import annotations

from collections import deque
import fcntl
import os
import socket as _socket
import struct
import sys
import time
from typing import TYPE_CHECKING, Any
import weakref

try:
    import termios
    _FIONREAD = getattr(termios, "FIONREAD", 0x4004667F if sys.platform == "darwin" else 0x541B)
except ImportError:
    _FIONREAD = 0x4004667F if sys.platform == "darwin" else 0x541B

if TYPE_CHECKING:
    from typing import Self

from proctrace._proctrace_core import IpcStats

_registry: weakref.WeakSet[Any] = weakref.WeakSet()


def _register(stats: Any) -> Any:
    _registry.add(stats)
    return stats


def ipc_report() -> str:
    if not _registry:
        return "(no IPC channels traced)"
    return "\n".join(s.report() for s in _registry)


class TracedQueue:
    __slots__ = ("_q", "_stats", "_put_times")

    def __init__(self, queue: Any, stats: IpcStats, max_tracked: int = 1024) -> None:
        self._q = queue
        self._stats = stats
        self._put_times: deque[int] = deque(maxlen=max_tracked)

    def put(self, item: Any, block: bool = True, timeout: float | None = None) -> None:
        t_put_ns = time.monotonic_ns()
        self._q.put(item, block=block, timeout=timeout)
        self._put_times.append(t_put_ns)

        try:
            self._stats.record_depth(self._q.qsize())
        except NotImplementedError:
            pass

    def put_nowait(self, item: Any) -> None:
        self.put(item, block=False)

    def get(self, block: bool = True, timeout: float | None = None) -> Any:
        item = self._q.get(block=block, timeout=timeout)
        t_get_ns = time.monotonic_ns()

        if self._put_times:
            t_put_ns = self._put_times.popleft()
            latency_us = (t_get_ns - t_put_ns) // 1000
            self._stats.record_latency_us(latency_us)

        return item

    def get_nowait(self) -> Any:
        return self.get(block=False)

    @property
    def stats(self) -> IpcStats:
        return self._stats

    def __getattr__(self, name: str) -> Any:
        return getattr(self._q, name)


class TracedPipe:
    __slots__ = ("read_fd", "write_fd", "_stats", "_write_times")

    def __init__(self, read_fd: int, write_fd: int, stats: IpcStats, max_tracked: int = 1024) -> None:
        self.read_fd = read_fd
        self.write_fd = write_fd
        self._stats = stats
        self._write_times: deque[int] = deque(maxlen=max_tracked)

    def write(self, data: bytes) -> int:
        t_write_ns = time.monotonic_ns()
        n = os.write(self.write_fd, data)
        self._write_times.append(t_write_ns)
        return n

    def read(self, n: int) -> bytes:
        data = os.read(self.read_fd, n)
        t_read_ns = time.monotonic_ns()
        if self._write_times:
            t_write_ns = self._write_times.popleft()
            latency_us = (t_read_ns - t_write_ns) // 1000
            self._stats.record_latency_us(latency_us)
        return data

    def fileno_read(self) -> int:
        return self.read_fd

    def fileno_write(self) -> int:
        return self.write_fd

    @property
    def stats(self) -> IpcStats:
        return self._stats


class TracedSocket:
    __slots__ = ("_sock", "_stats")

    def __init__(self, sock: _socket.socket, stats: Any) -> None:
        self._sock = sock
        self._stats = stats

    def sendall(self, data: bytes, flags: int = 0) -> None:
        t0 = time.monotonic_ns()
        self._sock.sendall(data, flags)
        latency_us = (time.monotonic_ns() - t0) // 1000
        self._stats.record_send(len(data), latency_us)

    def send(self, data: bytes, flags: int = 0) -> int:
        t0 = time.monotonic_ns()
        n = self._sock.send(data, flags)
        latency_us = (time.monotonic_ns() - t0) // 1000
        self._stats.record_send(n, latency_us)
        return n

    def recv(self, bufsize: int, flags: int = 0) -> bytes:
        # Query how many bytes are in the kernel receive buffer BEFORE recv
        try:
            buf = struct.pack("I", 0)
            result = fcntl.ioctl(self._sock.fileno(), _FIONREAD, buf)
            waiting = struct.unpack("I", result)[0]
            # Calculate utilization: what fraction of bufsize is already waiting
            pct = min(100, int(waiting * 100 / bufsize)) if bufsize > 0 else 0
        except OSError:
            pct = 0

        data = self._sock.recv(bufsize, flags)
        self._stats.record_recv(len(data), pct)
        return data

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_) -> bool:
        return False  # never suppress exceptions

    @property
    def stats(self) -> Any:
        return self._stats

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sock, name)


def trace_ipc(queue: Any, name: str = "", ring_capacity: int = 1024) -> TracedQueue:
    channel_name = name or repr(queue)[:40]
    stats = IpcStats(channel_name, ring_capacity)
    _register(stats)
    return TracedQueue(queue, stats, max_tracked=ring_capacity)


def trace_pipe(
    read_fd: int,
    write_fd: int,
    name: str = "",
    ring_capacity: int = 1024,
) -> TracedPipe:
    channel_name = name or f"pipe({read_fd},{write_fd})"
    stats = IpcStats(channel_name, ring_capacity)
    _register(stats)
    return TracedPipe(read_fd, write_fd, stats, max_tracked=ring_capacity)


def trace_socket(
    sock: _socket.socket,
    name: str = "",
    ring_capacity: int = 1024,
) -> TracedSocket:

    from proctrace._proctrace_core import SocketStats
    channel_name = name or f"socket({sock.fileno()})"
    stats = SocketStats(channel_name, ring_capacity)
    _register(stats)
    return TracedSocket(sock, stats)
