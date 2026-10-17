from __future__ import annotations

import asyncio
import os
import sys
import threading
import traceback
from datetime import datetime, timezone
from typing import IO


def _format_tasks(tasks: list[asyncio.Task], lines: list[str]) -> None:
    """Format asyncio tasks into human-readable lines."""
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
                for frame in frames[-5:]:
                    lines.append(
                        f"    File {frame.f_code.co_filename!r}, "
                        f"line {frame.f_lineno}, in {frame.f_code.co_name}\n"
                    )
            except Exception:
                pass
            lines.append("\n")
        except Exception:
            continue


def _dump_asyncio_tasks(
    lines: list[str],
    loop: asyncio.AbstractEventLoop | None,
) -> None:
    """Inspect and append active asyncio tasks to the output lines."""
    try:
        target_loop = loop
        if target_loop is None:
            try:
                target_loop = asyncio.get_running_loop()
            except RuntimeError:
                target_loop = None

        if target_loop is not None and target_loop.is_running():
            done_event = threading.Event()
            collected: list[str] = []

            def _collect_tasks(
                l=target_loop, cl=collected, ev=done_event
            ) -> None:
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
                    lines.extend(collected)
                    return
                else:
                    lines.append(
                        "[asyncio] Event loop busy; capturing fallback task snapshot:\n"
                    )
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
                all_tasks_set = (
                    getattr(tasks_mod, "_all_tasks", None) if tasks_mod else None
                )
                if all_tasks_set:
                    tasks_to_dump = list(all_tasks_set)
                else:
                    lines.append("[asyncio] No event loop found\n")
                    return
            except Exception:
                lines.append("[asyncio] No event loop found\n")
                return

        _format_tasks(tasks_to_dump, lines)

    except Exception as e:
        lines.append(f"[asyncio] Error dumping tasks: {e}\n")


def do_dump(
    out: IO,
    include_asyncio: bool,
    loop: asyncio.AbstractEventLoop | None,
) -> None:
    """Format and write the thread and asyncio dump."""
    if sys.is_finalizing():
        return

    timestamp = datetime.now(timezone.utc).isoformat()
    sep = "━" * 60

    lines: list[str] = [
        f"\n{sep}\n",
        f"proctrace thread dump @ {timestamp}\n",
        f"pid={os.getpid()}  threads={threading.active_count()}\n",
        f"{sep}\n\n",
    ]

    frames = sys._current_frames()

    for thread in threading.enumerate():
        tid = thread.ident
        frame = frames.get(tid)
        lines.append(f"Thread '{thread.name}' (id={tid}, daemon={thread.daemon})\n")
        if frame is not None:
            stack = traceback.extract_stack(frame)
            for entry in stack[-10:]:
                lines.append(
                    f"  File {entry.filename!r}, line {entry.lineno}, in {entry.name}\n"
                )
                if entry.line:
                    lines.append(f"    {entry.line.strip()}\n")
        else:
            lines.append("  (no frame available)\n")
        lines.append("\n")

    if include_asyncio:
        _dump_asyncio_tasks(lines, loop)

    lines.append(f"{sep}\n\n")

    if not sys.is_finalizing():
        try:
            out.write("".join(lines))
            out.flush()
        except (OSError, ValueError):
            pass


_do_dump = do_dump

__all__ = ["_do_dump", "do_dump"]
