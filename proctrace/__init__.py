"""
proctrace - Process introspection for humans.

Provides lightweight, non-invasive process and resource introspection
tools for measuring memory deltas, file descriptor leaks, thread creation,
IPC latency, and real-time process diagnostics.
"""

from __future__ import annotations

import pathlib
from typing import Final

from proctrace._proctrace_core import probe_version as _probe_version
from proctrace.alloc import (
    AllocEntry,
    AllocationReport,
    AllocationTracker,
    ObjectGrowth,
)
from proctrace.core import (
    ConfigurationError,
    Error,
    ProcessNotFoundError,
    ProctraceError,
    ResourceDelta,
    ResourceMonitoringError,
    SignalHandlingError,
    Timeline,
)
from proctrace.diagnostics import (
    SignalHandlerHandle,
    install_signal_handler,
    uninstall_all_signal_handlers,
    uninstall_signal_handler,
)
from proctrace.ipc import ipc_report, trace_ipc, trace_pipe, trace_socket
from proctrace.telemetry import (
    ResourceWatcher,
    plot_timeline,
    probe,
    render_html,
    render_svg,
    watch,
)
from proctrace.testing import Limits, load_baseline, save_baseline

__version__: Final[str] = _probe_version()

_GUIDE_PATH: Final[pathlib.Path] = (
    pathlib.Path(__file__).resolve().parent / "guidedoc.md"
)
_FALLBACK_GUIDE_PATH: Final[pathlib.Path] = (
    pathlib.Path(__file__).resolve().parent.parent / "guidedoc.md"
)


def _read_guide() -> str | None:
    # 1. Package resources (installed wheel / distribution)
    try:
        import importlib.resources as importlib_resources

        for name in ("guidedoc.md", "GUIDE.md"):
            ref = importlib_resources.files("proctrace").joinpath(name)
            if ref.is_file():
                text = ref.read_text(encoding="utf-8")
                if text.strip().startswith("../") and len(text.strip().splitlines()) == 1:
                    resolved = (pathlib.Path(__file__).resolve().parent / text.strip()).resolve()
                    if resolved.is_file():
                        return resolved.read_text(encoding="utf-8")
                return text
    except Exception:
        pass

    # 2. Filesystem candidates (package dir, repo root, docs dir)
    pkg_dir = pathlib.Path(__file__).resolve().parent
    candidates = (
        _GUIDE_PATH,
        pkg_dir / "GUIDE.md",
        _FALLBACK_GUIDE_PATH,
        pkg_dir.parent / "GUIDE.md",
        pkg_dir.parent / "docs" / "guidedoc.md",
    )
    for path in candidates:
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
                if text.strip().startswith("../") and len(text.strip().splitlines()) == 1:
                    resolved = (path.parent / text.strip()).resolve()
                    if resolved.is_file():
                        return resolved.read_text(encoding="utf-8")
                return text
            except OSError:
                continue

    return None


def help() -> None:
    """Print the developer guide to standard output or interactive pager if available."""
    content = _read_guide()
    if content is not None:
        import pydoc

        pydoc.pager(content)
        return
    print("Guide not found. See the project repository for documentation.")


__all__ = [
    "AllocEntry",
    "AllocationReport",
    "AllocationTracker",
    "ConfigurationError",
    "Error",
    "Limits",
    "ObjectGrowth",
    "ProcessNotFoundError",
    "ProctraceError",
    "ResourceDelta",
    "ResourceMonitoringError",
    "ResourceWatcher",
    "SignalHandlerHandle",
    "SignalHandlingError",
    "Timeline",
    "__version__",
    "help",
    "install_signal_handler",
    "ipc_report",
    "load_baseline",
    "plot_timeline",
    "probe",
    "render_html",
    "render_svg",
    "save_baseline",
    "trace_ipc",
    "trace_pipe",
    "trace_socket",
    "uninstall_all_signal_handlers",
    "uninstall_signal_handler",
    "watch",
]
