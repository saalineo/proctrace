from __future__ import annotations

import asyncio
import atexit
import os
import select
import signal
import sys
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import IO, TYPE_CHECKING, Final

if TYPE_CHECKING:
    from types import TracebackType
    from typing import Self

from proctrace.core.exceptions import ConfigurationError
from proctrace.diagnostics.dump import _do_dump, do_dump

SIGNAL_MAP: Final[Mapping[str, int]] = {
    "SIGUSR1": signal.SIGUSR1,
    "SIGUSR2": signal.SIGUSR2,
    "SIGALRM": signal.SIGALRM,
}

_active_handlers: dict[int, SignalHandlerHandle] = {}
_registry_lock = threading.RLock()


class SignalHandlerHandle:
    """Handle to an installed signal watcher thread with lifecycle management.

    Supports use as a context manager for scoped signal monitoring:
        with install_signal_handler("SIGUSR1"):
            do_work()
    """

    def __init__(
        self,
        signal_num: int,
        read_fd: int,
        write_fd: int,
        thread: threading.Thread,
        stop_event: threading.Event,
        stream: IO,
        owns_stream: bool,
    ) -> None:
        self.signal_num = signal_num
        self.read_fd = read_fd
        self.write_fd = write_fd
        self.thread = thread
        self.stop_event = stop_event
        self.stream = stream
        self.owns_stream = owns_stream
        self._uninstalled = False

    def uninstall(self) -> None:
        """Stop the watcher thread, close resources, and deregister the handler."""
        if self._uninstalled:
            return
        self._uninstalled = True

        self.stop_event.set()

        # Join the thread with a short timeout to prevent shutdown hangs
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=0.3)

        # Close the read pipe
        if self.read_fd >= 0:
            try:
                os.close(self.read_fd)
            except OSError:
                pass
            self.read_fd = -1

        # Close owned output stream
        if self.owns_stream:
            try:
                self.stream.close()
            except (OSError, ValueError):
                pass

        with _registry_lock:
            _active_handlers.pop(self.signal_num, None)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        self.uninstall()
        return False


def _resolve_output(output: str | Path | IO) -> tuple[IO, bool]:
    """Resolve the destination stream and indicate if it is owned by proctrace."""
    if output == "stderr":
        return sys.stderr, False
    elif isinstance(output, (str, Path)):
        return open(output, "a", encoding="utf-8"), True
    else:
        return output, False


def install_signal_handler(
    sig: str = "SIGUSR1",
    output: str | Path | IO = "stderr",
    include_asyncio: bool = True,
    loop: asyncio.AbstractEventLoop | None = None,
) -> SignalHandlerHandle:
    """Install a non-blocking signal handler that dumps process stacks on trigger.

    Args:
        sig: Signal name string (one of 'SIGUSR1', 'SIGUSR2', 'SIGALRM').
        output: Destination stream, file path, or 'stderr'.
        include_asyncio: Whether to inspect and dump active asyncio tasks.
        loop: Specific asyncio event loop to inspect (defaults to running loop).

    Returns:
        SignalHandlerHandle allowing scoped context management and clean uninstallation.
    """
    from proctrace._proctrace_core import register_signal_pipe

    signal_num = SIGNAL_MAP.get(sig)
    if signal_num is None:
        raise ConfigurationError(
            f"Unknown signal: {sig!r}. Use one of: {list(SIGNAL_MAP)}"
        )

    # Uninstall existing handler for this signal if already present
    with _registry_lock:
        existing = _active_handlers.pop(signal_num, None)
    if existing is not None:
        existing.uninstall()

    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

    out_stream, owns_stream = _resolve_output(output)
    read_fd, write_fd = register_signal_pipe(signal_num)
    stop_event = threading.Event()

    def _watcher() -> None:
        while not stop_event.is_set():
            if sys.is_finalizing():
                break

            try:
                ready, _, _ = select.select([read_fd], [], [], 0.1)
            except (OSError, ValueError):
                break

            if stop_event.is_set() or sys.is_finalizing():
                break

            if ready:
                try:
                    data = os.read(read_fd, 64)
                    if not data:
                        break
                except OSError:
                    break

                if not stop_event.is_set() and not sys.is_finalizing():
                    do_dump(out_stream, include_asyncio, loop)

    watcher = threading.Thread(
        target=_watcher,
        name="proctrace-signal-watcher",
        daemon=True,
    )
    watcher.start()

    handle = SignalHandlerHandle(
        signal_num=signal_num,
        read_fd=read_fd,
        write_fd=write_fd,
        thread=watcher,
        stop_event=stop_event,
        stream=out_stream,
        owns_stream=owns_stream,
    )

    with _registry_lock:
        _active_handlers[signal_num] = handle

    return handle


def uninstall_signal_handler(sig: str = "SIGUSR1") -> None:
    """Uninstall the signal handler and stop the watcher thread for a signal."""
    signal_num = SIGNAL_MAP.get(sig)
    if signal_num is None:
        return
    with _registry_lock:
        handle = _active_handlers.pop(signal_num, None)
    if handle is not None:
        handle.uninstall()


def uninstall_all_signal_handlers() -> None:
    """Uninstall all active signal handlers and terminate watcher threads."""
    with _registry_lock:
        handlers = list(_active_handlers.values())
        _active_handlers.clear()
    for handle in handlers:
        handle.uninstall()


@atexit.register
def _shutdown_signal_handlers() -> None:
    """Interpreter exit hook to guarantee daemon watcher threads terminate cleanly."""
    uninstall_all_signal_handlers()


__all__ = [
    "SIGNAL_MAP",
    "SignalHandlerHandle",
    "install_signal_handler",
    "uninstall_all_signal_handlers",
    "uninstall_signal_handler",
]
