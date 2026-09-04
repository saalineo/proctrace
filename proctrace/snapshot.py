from __future__ import annotations

import asyncio
import os
import select
import signal
import sys
import threading
import traceback
from datetime import datetime, timezone
from typing import IO

_SIGNAL_MAP = {            # signal name string to numbers
    "SIGUSR1": signal.SIGUSR1,
    "SIGUSR2": signal.SIGUSR2,
    "SIGALRM": signal.SIGALRM,
}


def install_signal_handler(
    sig: str = "SIGUSR1",
    output: str | IO = "stderr",
    include_asyncio: bool = True,
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    from proctrace._proctrace_core import register_signal_pipe

    signal_num = _SIGNAL_MAP.get(sig)
    if signal_num is None:
        raise ValueError(f"Unknown signal: {sig!r}. Use one of: {list(_SIGNAL_MAP)}")

    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    loop = None
            except RuntimeError:
                loop = None

    out_stream = _resolve_output(output)         # output stream

    read_fd, _write_fd = register_signal_pipe(signal_num) #signal handler and pipe fds
    def _watcher() -> None:
        while True:
            try:
                ready, _, _ = select.select([read_fd], [], [])
            except (OSError, ValueError):
                break

            if ready:
                try:
                    os.read(read_fd, 64)
                except OSError:
                    break

                _do_dump(out_stream, include_asyncio, loop)

    watcher = threading.Thread(
        target=_watcher,
        name="proctrace-signal-watcher",
        daemon=True,
    )
    watcher.start()


def _resolve_output(output) -> IO:
    if output == "stderr":
        return sys.stderr
    elif isinstance(output, str):
        return open(output, "a")
    else:
        return output


def _do_dump(out: IO, include_asyncio: bool, loop: asyncio.AbstractEventLoop | None) -> None:  #stack dumping with gil held
    timestamp = datetime.now(timezone.utc).isoformat()
    sep = "━" * 60

    out.write(f"\n{sep}\n")
    out.write(f"proctrace thread dump @ {timestamp}\n")
    out.write(f"pid={os.getpid()}  threads={threading.active_count()}\n")
    out.write(f"{sep}\n\n")

    frames = sys._current_frames()

    for thread in threading.enumerate():
        tid = thread.ident
        frame = frames.get(tid)
        out.write(f"Thread '{thread.name}' (id={tid}, daemon={thread.daemon})\n")
        if frame is not None:
            stack = traceback.extract_stack(frame)
            for entry in stack[-10:]:  
                out.write(f"  File {entry.filename!r}, line {entry.lineno}, in {entry.name}\n")
                if entry.line:
                    out.write(f"    {entry.line.strip()}\n")
        else:
            out.write("  (no frame available)\n")
        out.write("\n")

    if include_asyncio:
        _dump_asyncio_tasks(out, loop)

    out.write(f"{sep}\n\n")
    out.flush()


def _format_tasks(tasks: list[asyncio.Task], lines: list[str]) -> None:
    if not tasks:
        lines.append("[asyncio] No running tasks\n")
        return

    lines.append(f"[asyncio] {len(tasks)} running task(s):\n\n")
    for task in tasks:
        try:
            name = task.get_name() if hasattr(task, "get_name") else repr(task)
            lines.append(f"  Task: {name!r}\n")
            try:
                frames = task.get_stack()
                for frame in frames[-5:]:  # Last 5 frames
                    lines.append(
                        f"    File {frame.f_code.co_filename!r}, "
                        f"line {frame.f_lineno}, in {frame.f_code.co_name}\n"
                    )
            except Exception:
                pass
            lines.append("\n")
        except Exception:
            continue


def _dump_asyncio_tasks(out: IO, loop: asyncio.AbstractEventLoop | None) -> None:
    try:
        target_loop = loop
        if target_loop is None:
            try:
                target_loop = asyncio.get_running_loop()
            except RuntimeError:
                try:
                    target_loop = asyncio.get_event_loop()
                    if target_loop.is_closed():
                        target_loop = None
                except RuntimeError:
                    target_loop = None

        if target_loop is not None and target_loop.is_running():
            done_event = threading.Event()
            collected_lines: list[str] = []

            def _collect_tasks(l=target_loop, cl=collected_lines, ev=done_event) -> None:
                try:
                    tasks = list(asyncio.all_tasks(l))
                    _format_tasks(tasks, cl)
                except Exception as e:
                    cl.append(f"[asyncio] Error dumping tasks: {e}\n")
                finally:
                    ev.set()

            try:
                target_loop.call_soon_threadsafe(_collect_tasks)
                if done_event.wait(timeout=0.5):
                    out.write("".join(collected_lines))
                    return
                else:
                    out.write("[asyncio] Event loop busy; capturing fallback task snapshot:\n")
            except RuntimeError:
                pass

        tasks_to_dump: list[asyncio.Task] = []
        if target_loop is not None and not target_loop.is_closed():
            try:
                tasks_to_dump = list(asyncio.all_tasks(target_loop))
            except Exception:
                tasks_to_dump = []
        else:
            try:
                tasks_mod = sys.modules.get("asyncio.tasks")
                all_tasks_set = getattr(tasks_mod, "_all_tasks", None) if tasks_mod else None
                if all_tasks_set:
                    tasks_to_dump = list(all_tasks_set)
                else:
                    out.write("[asyncio] No event loop found\n")
                    return
            except Exception:
                out.write("[asyncio] No event loop found\n")
                return

        fallback_lines: list[str] = []
        _format_tasks(tasks_to_dump, fallback_lines)
        out.write("".join(fallback_lines))

    except Exception as e:
        out.write(f"[asyncio] Error dumping tasks: {e}\n")
