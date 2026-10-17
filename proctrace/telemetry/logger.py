from __future__ import annotations

import json
import socket as _socket
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, TYPE_CHECKING, Literal

from proctrace.core.types import ResourceDelta
from proctrace.telemetry.watcher import ResourceWatcher

if TYPE_CHECKING:
    from typing import Self


class ProctraceLogger:
    """Logger that persists ResourceDelta metrics across execution checkpoints."""

    def __init__(
        self,
        output: str | Path | _socket.socket = "proctrace.log",
        format: Literal["json", "jsonl", "text"] = "jsonl",
        min_rss_delta_mb: float = 0.0,
    ) -> None:
        self.format = format
        self.min_rss_delta_mb = min_rss_delta_mb
        self._stream: IO | None = None
        self._socket: _socket.socket | None = None
        self._entries: list[dict] = []

        if isinstance(output, _socket.socket):
            self._socket = output
        else:
            self._stream = open(Path(output), "a", encoding="utf-8")

    def attach(self, watcher: ResourceWatcher) -> None:
        """Attach this logger to a ResourceWatcher's on_exit callback."""
        watcher.on_exit = self.log

    def log(self, delta: ResourceDelta, label: str = "") -> None:
        """Record a ResourceDelta if its magnitude meets the threshold."""
        if abs(delta.rss_delta_mb) < self.min_rss_delta_mb:
            return

        entry = {
            "timestamp_iso": datetime.now(timezone.utc).isoformat(),
            "label": label,
            "rss_delta_mb": round(delta.rss_delta_mb, 3),
            "vms_delta_mb": round(delta.vms_delta_mb, 3),
            "peak_rss_mb": round(delta.peak_rss_mb, 3),
            "fd_delta": delta.fd_delta,
            "thread_delta": delta.thread_delta,
            "elapsed_ms": round(delta.elapsed_ms, 2),
            "leaked_fds": delta.leaked_fds,
            "sample_interval_ms": delta.sample_interval_ms,
        }

        if self.format == "jsonl":
            line = json.dumps(entry) + "\n"
            self._write_raw(line)
        elif self.format == "json":
            self._entries.append(entry)
        elif self.format == "text":
            self._write_raw(delta.report() + "\n")

    def _write_raw(self, s: str) -> None:
        data = s.encode("utf-8")
        if self._socket is not None:
            self._socket.sendall(data)
        elif self._stream is not None:
            self._stream.write(s)
            self._stream.flush()

    def close(self) -> None:
        """Flush pending buffered entries and close active streams."""
        try:
            if self.format == "json" and self._entries:
                self._write_raw(json.dumps(self._entries, indent=2) + "\n")
        finally:
            if self._stream is not None:
                self._stream.close()
                self._stream = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_) -> None:
        self.close()


__all__ = ["ProctraceLogger"]
