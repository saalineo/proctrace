import asyncio
import functools
import inspect
import sys
import threading
import types
import weakref
from collections.abc import Callable
from typing import Literal, TypeVar

from proctrace._types import ResourceDelta
from proctrace.watch import ResourceWatcher

F = TypeVar("F", bound=Callable)


def probe(
    *,
    memory: bool = True,
    fds: bool = True,
    threads: bool = True,
    children: bool = False,        
    sample_interval: float = 0.05,  
    output: Literal["stderr", "none"] = "stderr",
    store: bool = True,
) -> Callable[[F], F]:
    def decorator(fn: F) -> F:
        if inspect.iscoroutinefunction(fn):
            return _wrap_async(fn, memory, fds, threads, children, sample_interval, output, store)
        else:
            return _wrap_sync(fn, memory, fds, threads, children, sample_interval, output, store)
    return decorator


class _ProbeResultStorage:
    def __init__(self) -> None:
        self._thread_local = threading.local()
        self._task_results = weakref.WeakKeyDictionary()

    def get(self) -> ResourceDelta | None:
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        if task is not None and task in self._task_results:
            return self._task_results[task]
        return getattr(self._thread_local, "value", None)

    def set(self, value: ResourceDelta | None) -> None:
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        if task is not None:
            self._task_results[task] = value
        self._thread_local.value = value


class SyncProbeWrapper:
    def __init__(self, fn, memory, fds, threads, children, sample_interval, output, store):
        self._fn = fn
        self._memory = memory
        self._fds = fds
        self._threads = threads
        self._children = children
        self._sample_interval = sample_interval
        self._output = output
        self._store = store
        self._storage = _ProbeResultStorage()
        functools.update_wrapper(self, fn)

    def __call__(self, *args, **kwargs):
        watcher = ResourceWatcher(
            memory=self._memory, fds=self._fds, threads=self._threads,
            children=self._children, sample_interval=self._sample_interval,
        )
        with watcher:
            result = self._fn(*args, **kwargs)

        _handle_result(self, watcher.result, self._output, self._store)
        return result

    @property
    def last_probe_result(self) -> ResourceDelta | None:
        return self._storage.get()

    @last_probe_result.setter
    def last_probe_result(self, value: ResourceDelta | None) -> None:
        self._storage.set(value)

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return types.MethodType(self, instance)


class AsyncProbeWrapper:
    def __init__(self, fn, memory, fds, threads, children, sample_interval, output, store):
        self._fn = fn
        self._memory = memory
        self._fds = fds
        self._threads = threads
        self._children = children
        self._sample_interval = sample_interval
        self._output = output
        self._store = store
        self._storage = _ProbeResultStorage()
        functools.update_wrapper(self, fn)
        inspect.markcoroutinefunction(self)

    async def __call__(self, *args, **kwargs):
        watcher = ResourceWatcher(
            memory=self._memory, fds=self._fds, threads=self._threads,
            children=self._children, sample_interval=self._sample_interval,
        )
        with watcher:
            result = await self._fn(*args, **kwargs)

        _handle_result(self, watcher.result, self._output, self._store)
        return result

    @property
    def last_probe_result(self) -> ResourceDelta | None:
        return self._storage.get()

    @last_probe_result.setter
    def last_probe_result(self, value: ResourceDelta | None) -> None:
        self._storage.set(value)

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return types.MethodType(self, instance)


def _wrap_sync(fn, memory, fds, threads, children, sample_interval, output, store):
    return SyncProbeWrapper(fn, memory, fds, threads, children, sample_interval, output, store)


def _wrap_async(fn, memory, fds, threads, children, sample_interval, output, store):
    return AsyncProbeWrapper(fn, memory, fds, threads, children, sample_interval, output, store)


def _handle_result(
    wrapper: SyncProbeWrapper | AsyncProbeWrapper,
    delta: ResourceDelta | None,
    output: str,
    store: bool,
) -> None:
    if delta is None:
        return
    if store:
        wrapper.last_probe_result = delta
    if output == "stderr":
        print(delta.report(), file=sys.stderr)