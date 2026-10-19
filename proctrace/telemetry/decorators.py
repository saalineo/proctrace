from __future__ import annotations

import asyncio
import functools
import inspect
import sys
import threading
import types
import weakref
from collections.abc import Callable
from typing import Any, Final, Literal, TypeVar

from proctrace.core.types import ResourceDelta
from proctrace.telemetry.watcher import ResourceWatcher

F = TypeVar("F", bound=Callable[..., Any])


class _ProbeResultStorage:
    """Thread-local and asyncio Task-local storage for probe results."""

    def __init__(self) -> None:
        self._thread_local = threading.local()
        self._task_results: weakref.WeakKeyDictionary[asyncio.Task, ResourceDelta | None] = (
            weakref.WeakKeyDictionary()
        )

    def get(self) -> ResourceDelta | None:
        """Retrieve the last probe result for the current task or thread."""
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        if task is not None and task in self._task_results:
            return self._task_results[task]
        return getattr(self._thread_local, "value", None)

    def set(self, value: ResourceDelta | None) -> None:
        """Store the probe result for the current task or thread."""
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        if task is not None:
            self._task_results[task] = value
        self._thread_local.value = value


class _BaseProbeWrapper:
    """Base wrapper encapsulating common configuration and result storage."""

    def __init__(
        self,
        fn: Callable[..., Any],
        memory: bool,
        fds: bool,
        threads: bool,
        children: bool,
        sample_interval: float | None,
        output: Literal["stderr", "none"],
        store: bool,
        timeline: bool = False,
    ) -> None:
        self._fn = fn
        self._memory = memory
        self._fds = fds
        self._threads = threads
        self._children = children
        self._sample_interval = sample_interval
        self._output = output
        self._store = store
        self._timeline = timeline
        self._storage = _ProbeResultStorage()
        functools.update_wrapper(self, fn)

    @property
    def last_probe_result(self) -> ResourceDelta | None:
        """Access the most recent ResourceDelta produced by this wrapper."""
        return self._storage.get()

    @last_probe_result.setter
    def last_probe_result(self, value: ResourceDelta | None) -> None:
        self._storage.set(value)

    def _create_watcher(self) -> ResourceWatcher:
        return ResourceWatcher(
            memory=self._memory,
            fds=self._fds,
            threads=self._threads,
            children=self._children,
            sample_interval=self._sample_interval,
            timeline=self._timeline,
        )

    def _publish_result(self, delta: ResourceDelta | None) -> None:
        if delta is None:
            return
        if self._store:
            self.last_probe_result = delta
        if self._output == "stderr":
            print(delta.report(), file=sys.stderr)

    def __get__(self, instance: Any, owner: type[Any] | None = None) -> Any:
        if instance is None:
            return self
        return types.MethodType(self, instance)


class SyncProbeWrapper(_BaseProbeWrapper):
    """Wrapper for synchronous functions decorated with @probe."""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        watcher = self._create_watcher()
        with watcher:
            result = self._fn(*args, **kwargs)

        self._publish_result(watcher.result)
        return result


class AsyncProbeWrapper(_BaseProbeWrapper):
    """Wrapper for asynchronous coroutine functions decorated with @probe."""

    def __init__(
        self,
        fn: Callable[..., Any],
        memory: bool,
        fds: bool,
        threads: bool,
        children: bool,
        sample_interval: float | None,
        output: Literal["stderr", "none"],
        store: bool,
        timeline: bool = False,
    ) -> None:
        super().__init__(
            fn,
            memory,
            fds,
            threads,
            children,
            sample_interval,
            output,
            store,
            timeline=timeline,
        )
        if hasattr(inspect, "markcoroutinefunction"):
            inspect.markcoroutinefunction(self)
        else:
            self._is_coroutine = getattr(asyncio.coroutines, "_is_coroutine", True)

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        watcher = self._create_watcher()
        with watcher:
            result = await self._fn(*args, **kwargs)

        self._publish_result(watcher.result)
        return result


def probe(
    *,
    memory: bool = True,
    fds: bool = True,
    threads: bool = True,
    children: bool = False,
    sample_interval: float | None = None,
    output: Literal["stderr", "none"] = "stderr",
    store: bool = True,
    timeline: bool = False,
) -> Callable[[F], F]:
    """Decorator to monitor resource usage during function execution.

    Args:
        memory: Track RSS/VMS memory changes.
        fds: Track open file descriptors and detect leaks.
        threads: Track thread count and record newly spawned threads.
        children: Track child processes.
        sample_interval: Sampler interval in seconds.
        output: Output mode ('stderr' or 'none').
        store: Whether to store the last result on function.last_probe_result.
        timeline: Whether to record high-frequency time-series resource samples.

    Returns:
        Decorated function maintaining original signature and metadata.
    """

    def decorator(fn: F) -> F:
        if inspect.iscoroutinefunction(fn):
            return AsyncProbeWrapper(
                fn,
                memory=memory,
                fds=fds,
                threads=threads,
                children=children,
                sample_interval=sample_interval,
                output=output,
                store=store,
                timeline=timeline,
            )  # type: ignore
        else:
            return SyncProbeWrapper(
                fn,
                memory=memory,
                fds=fds,
                threads=threads,
                children=children,
                sample_interval=sample_interval,
                output=output,
                store=store,
                timeline=timeline,
            )  # type: ignore

    return decorator


__all__ = [
    "AsyncProbeWrapper",
    "SyncProbeWrapper",
    "probe",
]
