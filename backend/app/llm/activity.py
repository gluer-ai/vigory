"""Live view of in-flight LLM calls, so the UI can show what a (slow) small
model is doing instead of an unexplained spinner.

Callers label their work with `llm_operation("...")`; `complete_json` records
each call in the process-wide `LLMActivity` (read by GET /llm/status). State
is per backend process — run a single uvicorn worker (the Dockerfile does).
"""
import contextvars
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache

_operation: contextvars.ContextVar[str] = contextvars.ContextVar("llm_operation", default="")


@contextmanager
def llm_operation(label: str) -> Iterator[None]:
    """Name the LLM work done inside the block; nested labels are joined
    ("report.pdf: chunk 2/5: Extracting entities")."""
    outer = _operation.get()
    token = _operation.set(f"{outer}: {label}" if outer else label)
    try:
        yield
    finally:
        _operation.reset(token)


def current_operation() -> str:
    return _operation.get() or "LLM call"


class LLMActivity:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._active: dict[int, tuple[str, float]] = {}
        self._next_id = 0
        self._last: dict | None = None

    @contextmanager
    def track(self, operation: str) -> Iterator[None]:
        call_id = self._next_id
        self._next_id += 1
        started = self._clock()
        self._active[call_id] = (operation, started)
        ok = False
        try:
            yield
            ok = True
        finally:
            del self._active[call_id]
            self._last = {
                "operation": operation,
                "duration_seconds": round(self._clock() - started, 1),
                "ok": ok,
            }

    def snapshot(self) -> dict:
        now = self._clock()
        active = [
            {"operation": op, "elapsed_seconds": round(now - started, 1)}
            for op, started in sorted(self._active.values(), key=lambda v: v[1])
        ]
        return {"active": active, "last_call": self._last}


@lru_cache
def get_activity() -> LLMActivity:
    return LLMActivity()
