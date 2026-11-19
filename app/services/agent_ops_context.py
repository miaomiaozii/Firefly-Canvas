"""Per-tool attribution for REST and MCP instrumentation.

MCP enters ``agent_ops_scope`` with its resolved project and agent and an
optional explicit ``turn_id`` query parameter. Missing identity or turn is
represented by None, never by a session or tool-call ID. The local Pi REST
routes still need to enter a scope and obtain the host's active logical turn.
A scope alone does not capture a mutation or write a journal record.

Asyncio tasks normally inherit a *snapshot* of their creator's context, including
when they outlive the scope. ``create_agent_ops_task`` makes that choice explicit;
for detached work use ``contextvars.Context().run(asyncio.create_task, coro)``.
``asyncio.to_thread`` copies context; raw threads/executors need a separate
``contextvars.copy_context().run`` per submission. A Context must not be entered
concurrently by multiple threads or tasks. Context is attribution, not authority.
"""
from __future__ import annotations

import asyncio
import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import math
from typing import Any, Coroutine, Iterator, TypeVar
from uuid import uuid4


def _validate_json(value: Any) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _validate_json(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("argument object keys must be strings")
            _validate_json(item)
        return
    raise ValueError("arguments must be valid JSON")


def _identity(value: str | None, name: str, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string" + (" or None" if optional else ""))


@dataclass(frozen=True, slots=True, init=False)
class AgentOpsContext:
    """Immutable attribution; ``args`` returns an independent JSON snapshot."""

    project_id: str
    agent_node_id: str | None
    turn_id: str | None
    tool: str
    op_id: str
    _args_json: str = field(repr=False)

    def __init__(self, *, project_id: str, agent_node_id: str | None,
                 turn_id: str | None, tool: str, args: dict[str, Any]):
        _identity(project_id, "project_id")
        _identity(agent_node_id, "agent_node_id", optional=True)
        _identity(turn_id, "turn_id", optional=True)
        _identity(tool, "tool")
        if type(args) is not dict:
            raise ValueError("args must be a JSON object")
        _validate_json(args)
        object.__setattr__(self, "project_id", project_id)
        object.__setattr__(self, "agent_node_id", agent_node_id)
        object.__setattr__(self, "turn_id", turn_id)
        object.__setattr__(self, "tool", tool)
        object.__setattr__(self, "op_id", uuid4().hex)
        object.__setattr__(self, "_args_json", json.dumps(args, ensure_ascii=False, allow_nan=False))

    @property
    def args(self) -> dict[str, Any]:
        return json.loads(self._args_json)


_current: contextvars.ContextVar[AgentOpsContext | None] = contextvars.ContextVar(
    "agent_ops_context", default=None
)


def current_agent_ops_context() -> AgentOpsContext | None:
    """Return the current operation, or None outside an instrumented scope."""
    return _current.get()


@contextmanager
def agent_ops_scope(*, project_id: str, agent_node_id: str | None = None,
                    turn_id: str | None = None, tool: str,
                    args: dict[str, Any]) -> Iterator[AgentOpsContext]:
    """Mint a server operation ID and restore the prior context on every exit."""
    operation = AgentOpsContext(project_id=project_id, agent_node_id=agent_node_id,
                                turn_id=turn_id, tool=tool, args=args)
    token = _current.set(operation)
    try:
        yield operation
    finally:
        _current.reset(token)


_T = TypeVar("_T")


def create_agent_ops_task(coro: Coroutine[Any, Any, _T], *, name: str | None = None) -> asyncio.Task[_T]:
    """Start a task with an independent copy of the current context at creation."""
    return contextvars.copy_context().run(asyncio.create_task, coro, name=name)
