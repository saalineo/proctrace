from __future__ import annotations

import asyncio
import io
import sys

import pytest

from proctrace._types import ResourceDelta
from proctrace.decorators import probe


class TestSyncDecorator:
    def test_attaches_last_probe_result(self):
        @probe(memory=True, fds=False, threads=False)
        def simple():
            return 42

        result = simple()
        assert result == 42
        assert simple.last_probe_result is not None
        assert isinstance(simple.last_probe_result, ResourceDelta)

    def test_preserves_return_value(self):
        @probe(output="none")
        def add(a, b):
            return a + b

        assert add(3, 4) == 7

    def test_no_output_when_none(self):
        @probe(output="none")
        def fn():
            bytearray(1024 * 1024)

        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            fn()
        finally:
            sys.stderr = old_stderr

        assert captured.getvalue() == ""

    def test_multiple_calls_update_result(self):
        @probe(output="none")
        def fn(n):
            return bytearray(n * 1024 * 1024)

        fn(1)
        fn(5)
        assert fn.last_probe_result is not None


class TestAsyncDecorator:
    def test_async_function_decorated(self):
        @probe(memory=True, fds=False, threads=False, output="none")
        async def async_fn():
            await asyncio.sleep(0.01)
            return "done"

        result = asyncio.run(async_fn())
        assert result == "done"
        assert async_fn.last_probe_result is not None
        assert isinstance(async_fn.last_probe_result, ResourceDelta)

    def test_async_preserves_exception(self):
        @probe(output="none")
        async def failing():
            raise ValueError("deliberate")

        with pytest.raises(ValueError, match="deliberate"):
            asyncio.run(failing())

    def test_thread_safety_isolation(self):
        import threading
        import time

        results = {}

        @probe(output="none")
        def slow_fn(name, duration):
            time.sleep(duration)
            return name

        def run_thread(name, duration):
            slow_fn(name, duration)
            # Store the result retrieved in this thread
            results[name] = slow_fn.last_probe_result

        t1 = threading.Thread(target=run_thread, args=("A", 0.05))
        t2 = threading.Thread(target=run_thread, args=("B", 0.01))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # They should both have captured distinct probe results
        assert results["A"] is not None
        assert results["B"] is not None
        assert results["A"] is not results["B"]

    @pytest.mark.asyncio
    async def test_async_task_safety_isolation(self):
        import asyncio

        results = {}

        @probe(output="none")
        async def slow_async_fn(name, duration):
            await asyncio.sleep(duration)
            return name

        async def run_task(name, duration):
            await slow_async_fn(name, duration)
            results[name] = slow_async_fn.last_probe_result

        await asyncio.gather(
            run_task("A", 0.05),
            run_task("B", 0.01)
        )

        assert results["A"] is not None
        assert results["B"] is not None
        assert results["A"] is not results["B"]
