from __future__ import annotations

from typing import Any

from proctrace.ipc.channels import _registry


def _stats_name(s: Any) -> str:
    name_attr = getattr(s, "name", None)
    if callable(name_attr):
        try:
            return str(name_attr())
        except Exception:
            return repr(s)
    elif name_attr is not None:
        return str(name_attr)
    return repr(s)


def ipc_report() -> str:
    """Format an aggregated report of all actively traced IPC channels."""
    if not _registry:
        return "(no IPC channels traced)"
    active_stats = sorted(_registry, key=_stats_name)
    return "\n".join(s.report() for s in active_stats)


__all__ = ["_stats_name", "ipc_report"]
